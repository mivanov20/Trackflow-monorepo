"""Model, forecast, metrics and plots of the TrackFlow sales forecast.

WHY RANDOM FOREST (and not XGBoost) FOR THIS CASE
-------------------------------------------------
* Data size: 96 training months (84 usable rows once the first year is spent as history). With so few rows
  a boosted model that "corrects the previous tree" fits the noise of single months easily; a forest that
  averages many decorrelated trees is the more stable choice (lower variance), and its out-of-bag (OOB)
  predictions give an honest error estimate from the training rows alone.
* Explainability: the stakeholder (Finance, the CEO) has to trust the number. A forest has almost no knobs
  to defend, and `feature_importances_` shows which lag/seasonal signal drives the forecast.
* Tuning time: the ticket asks for a feasibility answer first. The forest works well with defaults
  (500 trees, `min_samples_leaf=2`); XGBoost needs a learning-rate / depth / rounds search to be fair.
* What XGBoost would give: some extra accuracy on large data. Here the signal is simple and regular (trend
  plus a strong yearly cycle), so accuracy is limited by the noise in the data, not by the model family.
  If the dashboard later needs maximum accuracy, XGBoost is a drop-in replacement for the same features.

THE TARGET
----------
The model predicts `ratio = revenue / mean(revenue of the previous 12 months)` and the forecast is
`ratio * trailing mean`. A tree cannot extrapolate: with the raw EUR as target it would never predict above
the best level it saw in training, and the test years are higher than any training year (6% yearly growth).
The ratio is stationary and holds exactly what the model must learn (the seasonal shape and the momentum);
the trend enters through the trailing mean.

TWO WAYS TO CHECK THE FORECAST (both on the 24 test months, never seen in training)
-----------------------------------------------------------------------------------
* recursive (the main result): the model forecasts the 24 months starting right after the training data.
  From the second month on it uses its own earlier predictions as lags, as it would in production, where
  the future actuals do not exist yet. It is the honest "how much will we sell in the coming months" test.
* one-step-ahead: each test month is predicted knowing all actuals up to the month before. Easier; shown for
  reference.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402
from sklearn.ensemble import RandomForestRegressor  # noqa: E402
from sklearn.metrics import mean_squared_error, roc_auc_score  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from statsmodels.tsa.seasonal import seasonal_decompose  # noqa: E402

from data.pipelines.sales_forecasting import features as ft  # noqa: E402

SEED = 42
INTERVAL = (0.10, 0.90)  # an 80% variability band
DEFAULT_OUT = Path(__file__).resolve().parent / "outputs"


# ----------------------------------------------------------------------------- model
def make_model(seed: int = SEED) -> Pipeline:
    """Scaler + Random Forest. The scaler is fitted on the training rows only (it is inside the pipeline).

    A forest does not need scaling (splits are order-based); it is there so the features share one scale, as the
    ticket asks, and so swapping in a scale-sensitive model later does not change the pipeline.
    """
    forest = RandomForestRegressor(
        n_estimators=500, min_samples_leaf=2, max_features=0.7, bootstrap=True, oob_score=True, random_state=seed, n_jobs=1
    )
    return Pipeline([("scale", StandardScaler()), ("forest", forest)])


def fit(train_features: pd.DataFrame, seed: int = SEED):
    """Fits on the training rows. Returns (model, residual quantiles of the OOB error in log space)."""
    model = make_model(seed)
    y = np.log(train_features["ratio"].to_numpy())
    model.fit(train_features[ft.MODEL_FEATURES], y)
    oob_residual = y - model.named_steps["forest"].oob_prediction_
    low, high = np.quantile(oob_residual, INTERVAL)
    return model, {"low": float(low), "high": float(high), "oob_rmse_log": float(np.sqrt(np.mean(oob_residual**2)))}


def predict_ratio(model: Pipeline, row: dict) -> float:
    return float(np.exp(model.predict(pd.DataFrame([[row[c] for c in ft.MODEL_FEATURES]], columns=ft.MODEL_FEATURES))[0]))


def forecast_recursive(model: Pipeline, band: dict, train_series: pd.Series, months: pd.DatetimeIndex) -> pd.DataFrame:
    """Forecasts `months` after the training data, feeding each prediction back as history for the next one."""
    history = list(train_series.to_numpy())
    rows = []
    for month in months:
        row = ft.feature_row(history, month)
        ratio = predict_ratio(model, row)
        point = row["roll_mean_12"] * ratio
        rows.append(
            {
                "month": month,
                "forecast": point,
                "ratio": ratio,
                "lower": point * math.exp(band["low"]),
                "upper": point * math.exp(band["high"]),
            }
        )
        history.append(point)  # the prediction, never the actual
    return pd.DataFrame(rows).set_index("month")


def forecast_one_step(model: Pipeline, band: dict, series: pd.Series, months: pd.DatetimeIndex) -> pd.DataFrame:
    """Each month predicted from the actuals before it (what a monthly re-run would do)."""
    rows = []
    for month in months:
        position = series.index.get_loc(month)
        row = ft.feature_row(series.iloc[:position].to_numpy(), month)
        ratio = predict_ratio(model, row)
        point = row["roll_mean_12"] * ratio
        rows.append(
            {
                "month": month,
                "forecast": point,
                "ratio": ratio,
                "lower": point * math.exp(band["low"]),
                "upper": point * math.exp(band["high"]),
            }
        )
    return pd.DataFrame(rows).set_index("month")


def seasonal_naive(train_series: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """Baseline: the same month of the last training year, repeated. A model must beat this to be worth it."""
    last_year = train_series.iloc[-12:]
    return pd.Series([last_year.iloc[m.month - 1] for m in months], index=months)


# ----------------------------------------------------------------------------- metrics
def psi(expected, actual, bins: int = 5, eps: float = 1e-4) -> float:
    """Population Stability Index of `actual` against `expected`, with bins taken from the quantiles of `expected`.

    < 0.1 stable, 0.1-0.25 some shift, > 0.25 a large shift. Five bins (not the usual ten) because the test set has
    only 24 months: with ten bins several would be empty and the log term would blow up on sampling noise alone.
    """
    expected, actual = np.asarray(expected, float), np.asarray(actual, float)
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected) + eps
    a = np.histogram(actual, edges)[0] / len(actual) + eps
    return float(np.sum((a - e) * np.log(a / e)))


def gini(is_positive, score) -> float:
    """Gini = 2*AUC - 1 of `score` ranking the positive months above the others (1 = perfect, 0 = random)."""
    return float(2 * roc_auc_score(is_positive, score) - 1)


def compute_metrics(actual: pd.Series, result: pd.DataFrame, train_f: pd.DataFrame, test_f: pd.DataFrame, frames: dict) -> dict:
    """The four required metrics, on the TEST months only, plus the percent errors Finance can read."""
    pred = result["forecast"].reindex(actual.index)
    residual = actual - pred
    mse = float(mean_squared_error(actual, pred))
    mean_revenue = float(actual.mean())

    # Gini: can the model tell the months that sell ABOVE the trailing-year average from the ones that sell below?
    # (CONTEXT: tell a normal low-season month, e.g. February, from the rest.) Label = actual ratio > 1.
    above_average = (test_f["ratio"] > 1.0).astype(int)
    k2_stat, k2_p = stats.normaltest(residual.to_numpy())

    return {
        "mse_eur2": mse,
        "rmse_eur": math.sqrt(mse),
        "rmse_pct_of_mean_monthly_revenue": 100 * math.sqrt(mse) / mean_revenue,
        "mape_pct": float(100 * (residual.abs() / actual).mean()),
        "psi_target_ratio": psi(train_f["ratio"], test_f["ratio"]),
        "psi_revenue_eur": psi(frames["train"]["revenue_eur"], frames["test"]["revenue_eur"]),
        "psi_shipments": psi(frames["train"]["shipments_processed"], frames["test"]["shipments_processed"]),
        "gini": gini(above_average, result["ratio"].reindex(actual.index)),
        "k2_statistic": float(k2_stat),
        "k2_p_value": float(k2_p),
        "residuals_look_normal_at_5pct": bool(k2_p > 0.05),
        "interval_coverage_pct": float(100 * ((actual >= result["lower"]) & (actual <= result["upper"])).mean()),
        "mean_monthly_revenue_test_eur": mean_revenue,
    }


# ----------------------------------------------------------------------------- decomposition
def decompose(train_series: pd.Series) -> dict:
    """Trend / seasonality / residual of the TRAINING series, and a check against what CONTEXT describes."""
    parts = seasonal_decompose(train_series, model="multiplicative", period=12)
    seasonal = parts.seasonal.iloc[:12]
    factor = {int(m.month): float(v) for m, v in seasonal.items()}
    yearly = train_series.groupby(train_series.index.year).sum()
    growth = (yearly.pct_change().dropna() * 100).round(1)
    return {
        "parts": parts,
        "seasonal_factor_by_month": factor,
        "yearly_growth_pct": {int(y): float(g) for y, g in growth.items()},
        "checks": {
            "nov_dec_above_average": factor[11] > 1.15 and factor[12] > 1.15,
            "february_below_average": factor[2] < 0.95,
            "growth_always_positive": bool((growth > 0).all()),
            # CONTEXT: growth alternates between X+Y (9%) and X-Y (3%) around 6%; monthly noise blurs each year a bit
            "mean_growth_near_6_pct": bool(4 <= growth.mean() <= 8),
            "growth_alternates_high_low": bool(len(set((growth > 6).tolist()[i] != (growth > 6).tolist()[i + 1] for i in range(len(growth) - 1))) == 1),
        },
    }


# ----------------------------------------------------------------------------- plots
def plot_decomposition(parts, path: Path) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    for ax, (name, values) in zip(axes, [("Observed", parts.observed), ("Trend", parts.trend), ("Seasonality (x)", parts.seasonal), ("Residual (x)", parts.resid)]):
        ax.plot(values.index, values.values, color="#2a6fb0")
        ax.set_ylabel(name)
        ax.grid(alpha=0.3)
    axes[0].set_title("TrackFlow monthly revenue, training years: trend / seasonality / residual")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_forecast(train_series, actual, recursive, one_step, metrics, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    tail = train_series.iloc[-24:]
    ax.plot(tail.index, tail / 1e6, color="#999999", label="Actual, last 2 training years")
    ax.plot(actual.index, actual / 1e6, color="black", marker="o", ms=4, label="Actual, 2 test years")
    ax.plot(recursive.index, recursive["forecast"] / 1e6, color="#d1495b", marker="s", ms=4, label="Forecast (recursive, 24 months ahead)")
    ax.fill_between(recursive.index, recursive["lower"] / 1e6, recursive["upper"] / 1e6, color="#d1495b", alpha=0.2, label="80% variability range")
    ax.plot(one_step.index, one_step["forecast"] / 1e6, color="#2a6fb0", ls="--", lw=1.2, label="Forecast (one step ahead)")
    ax.axvline(actual.index[0], color="#444444", ls=":")
    ax.set_ylabel("Revenue (million EUR)")
    ax.set_title(
        "Revenue forecast vs actual on the unseen test years\n"
        f"RMSE {metrics['rmse_pct_of_mean_monthly_revenue']:.1f}% of monthly revenue, MAPE {metrics['mape_pct']:.1f}%, "
        f"80% band covers {metrics['interval_coverage_pct']:.0f}% of months",
        fontsize=11,
    )
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------- experiment
def run_experiment(csv_path=ft.DEFAULT_CSV, out_dir=DEFAULT_OUT, seed: int = SEED) -> dict:
    """Loads, splits, engineers, trains, forecasts, scores and plots. Returns (and saves) everything reported."""
    np.random.seed(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frame, data_report = ft.load_sales(csv_path)
    train, test = ft.split_train_test(frame)
    series = frame[ft.TARGET]
    table = ft.build_features(series)
    train_f, test_f = table.loc[table.index <= train.index[-1]], table.loc[table.index >= test.index[0]]
    assert train_f.index.max() < test_f.index.min()

    model, band = fit(train_f, seed)  # only training rows
    recursive = forecast_recursive(model, band, train[ft.TARGET], test.index)
    one_step = forecast_one_step(model, band, series, test.index)
    actual = test[ft.TARGET]
    frames = {"train": train, "test": test}

    metrics = compute_metrics(actual, recursive, train_f, test_f, frames)
    one_step_metrics = compute_metrics(actual, one_step, train_f, test_f, frames)
    naive = seasonal_naive(train[ft.TARGET], test.index)
    naive_mse = float(mean_squared_error(actual, naive))
    decomposition = decompose(train[ft.TARGET])

    importances = model.named_steps["forest"].feature_importances_
    report = {
        "seed": seed,
        "model": "RandomForestRegressor(n_estimators=500, min_samples_leaf=2, max_features=0.7) inside StandardScaler pipeline",
        "data": data_report,
        "split": {
            "train": [str(train.index.min().date()), str(train.index.max().date()), len(train)],
            "test": [str(test.index.min().date()), str(test.index.max().date()), len(test)],
            "train_rows_used_after_history_warmup": len(train_f),
        },
        "metrics_recursive_forecast": metrics,
        "metrics_one_step_ahead": one_step_metrics,
        "baseline_seasonal_naive": {
            "mse_eur2": naive_mse,
            "rmse_pct_of_mean_monthly_revenue": 100 * math.sqrt(naive_mse) / float(actual.mean()),
        },
        "oob_band_log": band,
        "feature_importance": dict(sorted(zip(ft.MODEL_FEATURES, map(float, importances)), key=lambda kv: -kv[1])),
        "decomposition": {k: v for k, v in decomposition.items() if k != "parts"},
    }
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    result = recursive.join(actual.rename("actual")).join(one_step["forecast"].rename("forecast_one_step"))
    result.round(2).to_csv(out_dir / "forecast_test_years.csv")
    plot_decomposition(decomposition["parts"], out_dir / "decomposition.png")
    plot_forecast(train[ft.TARGET], actual, recursive, one_step, metrics, out_dir / "forecast.png")
    return report
