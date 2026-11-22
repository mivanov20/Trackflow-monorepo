"""Technical evaluation of the sales forecast model: time-aware cross-validation, learning curve, MAE / RMSE.

Everything here uses the TRAINING years only (2016-2023). The two test years stay untouched: evaluating and
choosing on them would turn them into training data.

Folds. `TimeSeriesSplit(n_splits=5, test_size=12)` over the 84 feature rows of the training years (2017-01 .. 2023-12)
gives expanding windows: every fold trains on the past and validates on the next 12 months (one full seasonal
cycle, so every fold contains a February and a Black Friday peak). Chronological order is never broken:
`TimeSeriesSplit` does not shuffle, and `verify_folds` checks it explicitly.

Leakage across the fold boundary. The lag / rolling features are causal row by row (`features.feature_row` sees
only the months before the row), so a training row never reads a month of the validation window. This is not
assumed, it is checked per fold (`check_fold_leakage`): the features are recomputed from the series cut at the fold
boundary and must equal the ones computed on the full series, and every month a training row reads (its lag-12 / rolling
window start included) must be before the first validation month. The scaler and the model are refitted inside
each fold on that fold's training rows only.

Two ways to validate each fold (as in the test of the model itself):
* recursive: the 12 validation months are forecast from the end of the training window, feeding predictions back as
  lags (what production does: the future actuals do not exist yet). The honest one.
* one-step-ahead: each month is predicted knowing the actuals before it. Easier; used for the learning curve
  because it isolates how well the model fits, without compounding.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from data.pipelines.sales_forecasting import features as ft
from data.pipelines.sales_forecasting import modeling

N_SPLITS = 5
VALIDATION_MONTHS = 12
TRAIN_SIZES = (12, 18, 24, 36, 48, 60, 72)  # rows (months) of training data used by the learning curve
PEAK_MONTHS = (11, 12)  # Black Friday and the holidays (CONTEXT)


# ----------------------------------------------------------------------------- folds
def make_folds(n_rows: int, n_splits: int = N_SPLITS, test_size: int = VALIDATION_MONTHS) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window, chronological folds over `n_rows` rows ordered in time."""
    return [(train, test) for train, test in TimeSeriesSplit(n_splits=n_splits, test_size=test_size).split(np.arange(n_rows))]


def verify_folds(folds, n_rows: int) -> dict:
    """Explicit proof that no fold shuffles or mixes the data. Raises AssertionError on the first violation."""
    assert len(folds) >= 5, "at least 5 folds are required"
    previous_test_end = -1
    for number, (train, test) in enumerate(folds, start=1):
        assert (np.diff(train) == 1).all() and (np.diff(test) == 1).all(), f"fold {number}: rows are not consecutive and in order"
        assert train.max() < test.min(), f"fold {number}: a training row is not before every validation row"
        assert not set(train) & set(test), f"fold {number}: a row is in both sets"
        assert test.min() > previous_test_end, f"fold {number}: validation windows overlap or go back in time"
        assert train[0] == 0, f"fold {number}: the training window must start at the first row (expanding window)"
        assert test.max() < n_rows
        previous_test_end = test.max()
    return {
        "folds": len(folds),
        "train_sizes": [int(len(t)) for t, _ in folds],
        "validation_sizes": [int(len(v)) for _, v in folds],
        "chronological": True,
        "shuffled": False,
        "overlapping_validation_windows": False,
    }


def check_fold_leakage(series: pd.Series, table: pd.DataFrame, train_idx, test_idx) -> dict:
    """Checks that no training row of the fold reads a month of its validation window.

    `table` holds one row per month from the 13th (build_features); `train_idx` / `test_idx` index its rows.
    """
    first_validation_month = table.index[test_idx[0]]
    train_months = table.index[train_idx]
    # 1) recompute the features from the series CUT at the fold boundary: identical to the full-series ones
    cut = series.loc[series.index < first_validation_month]
    recomputed = ft.build_features(cut)
    columns = ft.RAW_COLUMNS + ft.MODEL_FEATURES + ["y", "ratio"]
    same = recomputed.index.equals(train_months) and np.allclose(recomputed[columns].to_numpy(), table.iloc[train_idx][columns].to_numpy())
    assert same, "features of the training rows change when the validation window is removed: leakage"
    # 2) the oldest and newest month any training row reads: its own month at most, and 12 months back at least
    newest_read = train_months.max()
    assert newest_read < first_validation_month
    return {
        "first_validation_month": str(first_validation_month.date()),
        "newest_month_read_by_training": str(newest_read.date()),
        "features_identical_with_validation_removed": bool(same),
        "months_between_training_end_and_validation_start": int((first_validation_month.year - newest_read.year) * 12 + first_validation_month.month - newest_read.month),
    }


# ----------------------------------------------------------------------------- metrics
def mae(actual, predicted) -> float:
    return float(np.mean(np.abs(np.asarray(actual, float) - np.asarray(predicted, float))))


def rmse(actual, predicted) -> float:
    return float(math.sqrt(np.mean((np.asarray(actual, float) - np.asarray(predicted, float)) ** 2)))


def error_metrics(actual, predicted) -> dict:
    actual = np.asarray(actual, float)
    r, a = rmse(actual, predicted), mae(actual, predicted)
    bias = float(np.mean(np.asarray(predicted, float) - actual))  # > 0: the model over-forecasts
    return {"mae": a, "rmse": r, "rmse_pct": 100 * r / actual.mean(), "mae_pct": 100 * a / actual.mean(), "bias_pct": 100 * bias / actual.mean()}


def in_sample_prediction(model, rows: pd.DataFrame) -> np.ndarray:
    """Revenue the fitted model predicts for rows it was trained on (one step ahead, features from the actuals)."""
    ratio = np.exp(model.predict(rows[getattr(model, "feature_names_", ft.MODEL_FEATURES)]))
    return rows["roll_mean_12"].to_numpy() * ratio


# ----------------------------------------------------------------------------- fold evaluation
def _fit_on(series: pd.Series, train_rows: pd.DataFrame):
    model, band = modeling.fit(train_rows)
    return model, band


def evaluate_folds(series: pd.Series, table: pd.DataFrame, folds) -> list[dict]:
    """Fits one model per fold (on that fold's training rows only) and scores it on the fold's validation year."""
    results = []
    for number, (train_idx, test_idx) in enumerate(folds, start=1):
        leak = check_fold_leakage(series, table, train_idx, test_idx)
        train_rows, valid_rows = table.iloc[train_idx], table.iloc[test_idx]
        model, band = _fit_on(series, train_rows)
        train_series = series.loc[series.index < valid_rows.index[0]]
        recursive = modeling.forecast_recursive(model, band, train_series, valid_rows.index)["forecast"]
        one_step = modeling.forecast_one_step(model, band, series, valid_rows.index)["forecast"]
        train_pred = in_sample_prediction(model, train_rows)
        oob = np.exp(model.named_steps["forest"].oob_prediction_) * train_rows["roll_mean_12"].to_numpy()
        results.append(
            {
                "fold": number,
                "train_months": [str(train_rows.index[0].date()), str(train_rows.index[-1].date()), len(train_rows)],
                "validation_months": [str(valid_rows.index[0].date()), str(valid_rows.index[-1].date()), len(valid_rows)],
                "train": error_metrics(train_rows["y"], train_pred),
                "train_oob": error_metrics(train_rows["y"], oob),
                "validation_recursive": error_metrics(valid_rows["y"], recursive.to_numpy()),
                "validation_one_step": error_metrics(valid_rows["y"], one_step.to_numpy()),
                "leakage_check": leak,
                "_valid": pd.DataFrame({"actual": valid_rows["y"], "recursive": recursive, "one_step": one_step}),
            }
        )
    return results


def summarize(values) -> dict:
    values = np.asarray(values, float)
    return {"mean": float(values.mean()), "std": float(values.std(ddof=1)), "min": float(values.min()), "max": float(values.max())}


def summarize_folds(results: list[dict]) -> dict:
    """mean +/- std across folds for every error number."""
    summary = {}
    for part in ("train", "train_oob", "validation_recursive", "validation_one_step"):
        summary[part] = {metric: summarize([r[part][metric] for r in results]) for metric in ("mae", "rmse", "rmse_pct", "mae_pct")}
    return summary


def peak_vs_rest(results: list[dict], column: str = "one_step") -> dict:
    """Validation error in the peak months (Nov-Dec) against the other months, in EUR: where the cost concentrates."""
    frame = pd.concat([r["_valid"] for r in results])
    peak = frame.index.month.isin(PEAK_MONTHS)
    out = {}
    for name, mask in (("peak_nov_dec", peak), ("other_months", ~peak)):
        part = frame[mask]
        out[name] = {"months": int(mask.sum()), **error_metrics(part["actual"], part[column])}
    out["worst_months"] = [
        {"month": str(month.date()), "actual": float(row["actual"]), "predicted": float(row[column]), "error": float(row[column] - row["actual"])}
        for month, row in frame.assign(abs_error=(frame[column] - frame["actual"]).abs()).nlargest(5, "abs_error").iterrows()
    ]
    return out


# ----------------------------------------------------------------------------- learning curve
def learning_curve(series: pd.Series, table: pd.DataFrame, folds, sizes=TRAIN_SIZES) -> list[dict]:
    """Training and validation error as the training set grows.

    For every fold and every size n that fits in the fold's training window, a model is fitted on the n MOST RECENT
    training rows before the validation year (a contiguous block: time order kept) and scored on that fold's
    validation year (one step ahead) and on the n rows it was trained on. Results are averaged over the folds that
    have that many rows, so the largest sizes come from the later folds only.
    """
    points = []
    for size in sizes:
        train_err, oob_err, valid_err, used = [], [], [], 0
        for train_idx, test_idx in folds:
            if size > len(train_idx):
                continue
            rows = table.iloc[train_idx[-size:]]
            valid_rows = table.iloc[test_idx]
            model, band = modeling.fit(rows)
            valid_pred = modeling.forecast_one_step(model, band, series, valid_rows.index)["forecast"].to_numpy()
            train_err.append(error_metrics(rows["y"], in_sample_prediction(model, rows)))
            oob = np.exp(model.named_steps["forest"].oob_prediction_) * rows["roll_mean_12"].to_numpy()
            oob_err.append(error_metrics(rows["y"], oob))
            valid_err.append(error_metrics(valid_rows["y"], valid_pred))
            used += 1
        points.append(
            {
                "train_rows": size,
                "folds_used": used,
                **{f"{name}_{metric}": summarize([e[metric] for e in errors]) for name, errors in (("train", train_err), ("oob", oob_err), ("validation", valid_err)) for metric in ("rmse", "mae", "rmse_pct")},
            }
        )
    return points


def noise_floor_pct(train_series: pd.Series) -> float:
    """The part of the month-to-month variation that no model can explain: std of the decomposition residual (in %)."""
    parts = modeling.seasonal_decompose(train_series, model="multiplicative", period=12)
    return float(100 * (parts.resid.dropna() - 1).std(ddof=1))


# ----------------------------------------------------------------------------- corrective action: test regularisation
CANDIDATES = {
    "current (min_samples_leaf=2, max_features=0.7)": {},
    "min_samples_leaf=4": {"min_samples_leaf": 4},
    "min_samples_leaf=6": {"min_samples_leaf": 6},
    "min_samples_leaf=8": {"min_samples_leaf": 8},
    "min_samples_leaf=4, max_features=0.5": {"min_samples_leaf": 4, "max_features": 0.5},
    "min_samples_leaf=6, max_features=0.5": {"min_samples_leaf": 6, "max_features": 0.5},
    "max_depth=4": {"max_depth": 4},
    "seasonal index added (lag_12_rel kept)": {"features": ft.EXTENDED_FEATURES},
    "seasonal index replaces lag_12_rel": {"features": ft.SEASONAL_INDEX_FEATURES},
    "growth-regime feature added (yoy_growth_prev)": {"features": ft.GROWTH_FEATURES},
    "growth-regime feature + seasonal index (replaces lag_12_rel)": {"features": ft.GROWTH_AND_SEASONAL_FEATURES},
}


def compare_candidates(series: pd.Series, table: pd.DataFrame, folds, candidates=CANDIDATES) -> list[dict]:
    """Cross-validates each candidate setting on the SAME time-aware folds (training years only)."""
    rows = []
    for name, params in candidates.items():
        one_step, recursive, train = [], [], []
        for train_idx, test_idx in folds:
            train_rows, valid_rows = table.iloc[train_idx], table.iloc[test_idx]
            model, band = modeling.fit(train_rows, **params)
            train_series = series.loc[series.index < valid_rows.index[0]]
            one_step.append(error_metrics(valid_rows["y"], modeling.forecast_one_step(model, band, series, valid_rows.index)["forecast"].to_numpy()))
            recursive.append(error_metrics(valid_rows["y"], modeling.forecast_recursive(model, band, train_series, valid_rows.index)["forecast"].to_numpy()))
            train.append(error_metrics(train_rows["y"], in_sample_prediction(model, train_rows)))
        rows.append(
            {
                "setting": name,
                "params": params,
                "train_rmse_pct": summarize([e["rmse_pct"] for e in train]),
                "validation_one_step_rmse_pct": summarize([e["rmse_pct"] for e in one_step]),
                "validation_recursive_rmse_pct": summarize([e["rmse_pct"] for e in recursive]),
                "validation_one_step_rmse_eur": summarize([e["rmse"] for e in one_step]),
                "validation_recursive_rmse_eur": summarize([e["rmse"] for e in recursive]),
            }
        )
    return rows


# ----------------------------------------------------------------------------- diagnosis
def diagnose(summary: dict, curve: list[dict], floor_pct: float) -> dict:
    """Classifies the fit from the learning curve and the CV, with rules written down so the verdict is checkable.

    Errors are RMSE as % of mean monthly revenue; `floor_pct` is the noise no model can explain.
    * bias (underfitting): the TRAINING error is high, above ~1.3x the noise floor, and validation is close to it.
    * variance (overfitting): the training error is at or below the noise floor (the model is fitting noise) and
      validation is much higher: a wide, persistent gap.
    * well fitted: both curves converge at a low, close error.
    """
    train_pct = summary["train"]["rmse_pct"]["mean"]
    valid_pct = summary["validation_one_step"]["rmse_pct"]["mean"]
    oob_pct = summary["train_oob"]["rmse_pct"]["mean"]
    gap_pct = valid_pct - train_pct
    gap_share = gap_pct / valid_pct
    sized = [p for p in curve if p["folds_used"] >= 2]  # points backed by at least two folds
    valid_curve = [p["validation_rmse_pct"]["mean"] for p in sized]
    last_gap_share = (sized[-1]["validation_rmse_pct"]["mean"] - sized[-1]["train_rmse_pct"]["mean"]) / sized[-1]["validation_rmse_pct"]["mean"]
    still_falling = valid_curve[-1] < valid_curve[-2] * 0.97 or valid_curve[-1] < valid_curve[0] * 0.9
    evidence = {
        "train_rmse_pct": train_pct,
        "oob_rmse_pct": oob_pct,
        "validation_rmse_pct": valid_pct,
        "noise_floor_pct": floor_pct,
        "train_vs_floor": train_pct / floor_pct,
        "validation_vs_floor": valid_pct / floor_pct,
        "gap_pct_points": gap_pct,
        "gap_share_of_validation_error": gap_share,
        "gap_share_at_largest_size": last_gap_share,
        "validation_curve_still_falling": bool(still_falling),
    }
    if train_pct > 1.3 * floor_pct and gap_share < 0.25:
        label = "underfitting"
    elif train_pct <= 1.1 * floor_pct and gap_share >= 0.25 and last_gap_share >= 0.25:
        label = "overfitting"
    else:
        label = "well fitted"
    return {"label": label, **evidence}
