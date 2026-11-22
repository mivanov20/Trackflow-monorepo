"""Data loading, train/test split and causal time-aware features for the TrackFlow sales forecast.

The target is `revenue_eur` of the `consolidated` row of `data/raw/trackflow_sales.csv`
(CONTEXT: 120 months, 2016-01 .. 2025-12, no missing months, revenue always positive).

Causality rule: every feature of month t is computed from `history`, the revenues of the months BEFORE t
(`history[-1]` is t-1). Nothing here ever receives the value of month t or later. The same single function
(`feature_row`) builds the features for training, for the one-step-ahead check and for the recursive forecast,
so a feature cannot be computed one way when training and another way when predicting.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_CSV = Path(__file__).resolve().parents[3] / "data" / "raw" / "trackflow_sales.csv"
EXPECTED_COLUMNS = ["month", "revenue_eur", "shipments_processed", "avg_revenue_per_shipment_eur", "market"]
TARGET = "revenue_eur"
MARKET = "consolidated"  # the CONTEXT names this row as the one the model uses
TRAIN_YEARS = 8
TEST_YEARS = 2
MIN_HISTORY = 12  # the 12-month rolling mean needs a full year of past values

# What the model sees. Raw (EUR) values are kept next to them in the feature table so the tests can compare
# them with the raw series, but the model is fed the dimensionless ratios: growth moves the EUR level every
# year, and a tree cannot extrapolate beyond the levels it saw in training.
RAW_COLUMNS = ["lag_1", "lag_12", "roll_mean_3", "roll_mean_12", "roll_std_6"]
MODEL_FEATURES = [
    "lag_1_rel",  # last month vs the trailing-year average
    "lag_12_rel",  # same month last year vs the trailing-year average (the seasonal memory)
    "roll_mean_3_rel",  # recent trend: last 3 months vs trailing year
    "roll_cv_6",  # recent volatility: std of the last 6 months / trailing-year average
    "month_sin",
    "month_cos",
    "quarter",
]


def load_sales(csv_path: str | Path = DEFAULT_CSV) -> tuple[pd.DataFrame, dict]:
    """Loads the CSV, checks it against CONTEXT and returns the consolidated monthly series (+ a data report)."""
    raw = pd.read_csv(csv_path)
    missing = [c for c in EXPECTED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"{csv_path} does not match CONTEXT: missing columns {missing}")
    raw["month"] = pd.to_datetime(raw["month"])
    frame = raw[raw["market"] == MARKET].drop(columns="market").sort_values("month").set_index("month")
    if frame.empty:
        raise ValueError(f"no '{MARKET}' rows in {csv_path}")

    full_range = pd.date_range(frame.index.min(), frame.index.max(), freq="MS")
    report = {
        "rows": int(len(frame)),
        "first_month": str(frame.index.min().date()),
        "last_month": str(frame.index.max().date()),
        "missing_months": [str(m.date()) for m in full_range.difference(frame.index)],
        "duplicated_months": int(frame.index.duplicated().sum()),
        "null_values": int(frame.isna().sum().sum()),
        "non_positive_revenue": int((frame[TARGET] <= 0).sum()),
    }
    if report["duplicated_months"]:
        raise ValueError("duplicated months in the series")

    # Null / empty handling. The provided file is clean (the report above says so), but the loader must not
    # break on a dirty one: missing months become empty rows and every gap is filled by linear interpolation in
    # time, which keeps the seasonal shape of a short gap. Edges that cannot be interpolated are back/forward filled.
    frame = frame.reindex(full_range)
    frame.index.name = "month"
    filled = int(frame.isna().sum().sum())
    if filled:
        frame = frame.interpolate(method="time").bfill().ffill()
    report["values_filled"] = filled
    if (frame[TARGET] <= 0).any():
        raise ValueError("revenue_eur must be positive (CONTEXT business constraint)")
    return frame, report


def split_train_test(frame: pd.DataFrame, train_years: int = TRAIN_YEARS, test_years: int = TEST_YEARS):
    """First `train_years` years for training, the last `test_years` years for the check. No row is in both."""
    months = frame.index
    train_n, test_n = train_years * 12, test_years * 12
    if len(frame) != train_n + test_n:
        raise ValueError(f"expected {train_n + test_n} months ({train_years}+{test_years} years), got {len(frame)}")
    train, test = frame.iloc[:train_n], frame.iloc[train_n:]
    assert train.index.max() < test.index.min() and not set(train.index) & set(test.index)
    assert months[0] == train.index[0] and months[-1] == test.index[-1]
    return train, test


def feature_row(history: list[float] | np.ndarray, month: pd.Timestamp) -> dict[str, float]:
    """Features for `month` from the revenues of the months before it. history[-1] is month-1.

    Only past values are read: lag_1 = history[-1], lag_12 = history[-12], rolling windows end at history[-1].
    """
    history = np.asarray(history, dtype=float)
    if len(history) < MIN_HISTORY:
        raise ValueError(f"need at least {MIN_HISTORY} past months, got {len(history)}")
    lag_1, lag_12 = history[-1], history[-12]
    roll_mean_3, roll_mean_12 = history[-3:].mean(), history[-12:].mean()
    roll_std_6 = history[-6:].std(ddof=1)
    angle = 2 * math.pi * (month.month - 1) / 12
    return {
        "lag_1": lag_1,
        "lag_12": lag_12,
        "roll_mean_3": roll_mean_3,
        "roll_mean_12": roll_mean_12,
        "roll_std_6": roll_std_6,
        "lag_1_rel": lag_1 / roll_mean_12,
        "lag_12_rel": lag_12 / roll_mean_12,
        "roll_mean_3_rel": roll_mean_3 / roll_mean_12,
        "roll_cv_6": roll_std_6 / roll_mean_12,
        "month_sin": math.sin(angle),  # cyclical month: December and January are neighbours
        "month_cos": math.cos(angle),
        "quarter": float(month.quarter),
    }


def build_features(series: pd.Series) -> pd.DataFrame:
    """Feature table for every month that has MIN_HISTORY past months. Also holds `y` and the target `ratio`.

    ratio = revenue / trailing-12-month mean (the mean of the 12 months before it): the model predicts this
    seasonal-and-trend ratio and the revenue is ratio * roll_mean_12.
    """
    rows = []
    for position in range(MIN_HISTORY, len(series)):
        row = feature_row(series.iloc[:position].to_numpy(), series.index[position])
        row["month"] = series.index[position]
        row["y"] = float(series.iloc[position])
        row["ratio"] = row["y"] / row["roll_mean_12"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("month")
