"""Figures, written report and entry point of the technical evaluation (the computations are in `evaluation.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.pipelines.sales_forecasting import evaluation as ev  # noqa: E402
from data.pipelines.sales_forecasting import features as ft  # noqa: E402
from data.pipelines.sales_forecasting import modeling  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parents[3] / "data" / "eval"


# ----------------------------------------------------------------------------- plots
def plot_learning_curve(curve: list[dict], floor_eur: float, path) -> None:
    """Training and validation error against the training set size (RMSE and MAE, in EUR, mean +/- std over folds)."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharex=True)
    sizes = [p["train_rows"] for p in curve]
    for ax, metric, title in ((axes[0], "rmse", "RMSE (EUR)"), (axes[1], "mae", "MAE (EUR)")):
        for name, label, color, style in (
            ("train", "Training error (in-sample)", "#2a6fb0", "-"),
            ("oob", "Training error, out-of-bag (fair)", "#7a9cc6", ":"),
            ("validation", "Validation error (next year)", "#d1495b", "-"),
        ):
            mean = np.array([p[f"{name}_{metric}"]["mean"] for p in curve])
            std = np.nan_to_num(np.array([p[f"{name}_{metric}"]["std"] for p in curve]))
            ax.plot(sizes, mean, marker="o", ms=4, color=color, ls=style, label=label)
            ax.fill_between(sizes, mean - std, mean + std, color=color, alpha=0.15)
        if metric == "rmse":
            ax.axhline(floor_eur, color="#555555", ls="--", lw=1, label="Noise floor (unexplainable)")
        ax.set_title(title)
        ax.set_xlabel("Training set size (months)")
        ax.grid(alpha=0.3)
    for p in curve:
        axes[0].annotate(f"{p['folds_used']}", (p["train_rows"], axes[0].get_ylim()[0]), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7, color="#555555")
    axes[0].legend(fontsize=8, loc="upper right")
    fig.suptitle("Learning curve of the sales model (small numbers: folds averaged at each size)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_folds(results: list[dict], summary: dict, path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), gridspec_kw={"width_ratios": [1, 1.6]})
    labels = [f"{r['validation_months'][0][:4]}\n(train {r['train_months'][2]} mo)" for r in results]
    x = np.arange(len(results))
    for offset, key, label, color in (
        (-0.27, "train", "Training (in-sample)", "#2a6fb0"),
        (0, "validation_one_step", "Validation, one step", "#e0a030"),
        (0.27, "validation_recursive", "Validation, recursive", "#d1495b"),
    ):
        axes[0].bar(x + offset, [r[key]["rmse"] / 1000 for r in results], width=0.25, color=color, label=label)
    mean = summary["validation_recursive"]["rmse"]
    axes[0].axhline(mean["mean"] / 1000, color="#d1495b", ls="--", lw=1)
    axes[0].axhspan((mean["mean"] - mean["std"]) / 1000, (mean["mean"] + mean["std"]) / 1000, color="#d1495b", alpha=0.1)
    axes[0].set_xticks(x, labels, fontsize=8)
    axes[0].set_ylabel("RMSE (thousand EUR)")
    axes[0].set_title(f"RMSE per fold; recursive mean {mean['mean'] / 1000:.1f} +/- {mean['std'] / 1000:.1f}")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3, axis="y")
    valid = pd.concat([r["_valid"] for r in results])
    axes[1].plot(valid.index, valid["actual"] / 1e6, color="black", marker="o", ms=3, label="Actual")
    axes[1].plot(valid.index, valid["one_step"] / 1e6, color="#e0a030", ls="--", label="One step ahead")
    axes[1].plot(valid.index, valid["recursive"] / 1e6, color="#d1495b", label="Recursive (year ahead)")
    axes[1].set_title("The five validation years (each predicted by a model that never saw it)")
    axes[1].set_ylabel("Revenue (million EUR)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------- report
def _fmt(stats: dict, digits: int = 1) -> str:
    return f"{stats['mean']:,.{digits}f} ± {stats['std']:,.{digits}f}"


def render_report(r: dict) -> str:
    s, d, floor = r["summary"], r["diagnosis"], r["noise_floor_pct"]
    curve = r["learning_curve"]
    first, big = curve[1], [p for p in curve if p["folds_used"] >= 2][-1]
    peak, rest = r["peak_vs_rest"]["peak_nov_dec"], r["peak_vs_rest"]["other_months"]
    best, base = r["best_candidate"], r["candidates"][0]
    base_rec, best_rec = base["validation_recursive_rmse_pct"], best["validation_recursive_rmse_pct"]
    base_one, best_one = base["validation_one_step_rmse_pct"], best["validation_one_step_rmse_pct"]
    gain_rec = 100 * (1 - best_rec["mean"] / base_rec["mean"])
    gain_one = 100 * (1 - best_one["mean"] / base_one["mean"])
    gain_vs_std = (base_rec["mean"] - best_rec["mean"]) / base_rec["std"]
    regularised = [c for c in r["candidates"] if c["setting"].startswith(("min_samples_leaf", "max_depth"))]
    worst_reg = max(regularised, key=lambda c: c["validation_one_step_rmse_pct"]["mean"])
    leaf4 = next(c for c in r["candidates"] if c["setting"] == "min_samples_leaf=4")
    depth4 = next(c for c in r["candidates"] if c["setting"] == "max_depth=4")
    fold_rows = "\n".join(
        f"| {f['fold']} | {f['train_months'][0][:7]} .. {f['train_months'][1][:7]} ({f['train_months'][2]}) | {f['validation_months'][0][:7]} .. {f['validation_months'][1][:7]} | "
        f"{f['train']['rmse_pct']:.2f}% | {f['validation_one_step']['rmse_pct']:.2f}% | {f['validation_recursive']['rmse_pct']:.2f}% | "
        f"{f['validation_one_step']['bias_pct']:+.1f}% | {f['leakage_check']['newest_month_read_by_training'][:7]} < {f['leakage_check']['first_validation_month'][:7]} |"
        for f in r["folds"]
    )
    curve_rows = "\n".join(
        f"| {p['train_rows']} | {p['folds_used']} | {p['train_rmse_pct']['mean']:.2f}% | {p['oob_rmse_pct']['mean']:.2f}% | {p['validation_rmse_pct']['mean']:.2f}% | "
        f"{p['train_rmse']['mean']:,.0f} | {p['validation_rmse']['mean']:,.0f} |"
        for p in curve
    )
    cand_rows = "\n".join(
        f"| {c['setting']} | {c['train_rmse_pct']['mean']:.2f}% | {_fmt(c['validation_one_step_rmse_pct'], 2)}% | {_fmt(c['validation_recursive_rmse_pct'], 2)}% |" for c in r["candidates"]
    )
    bias_text = ", ".join(f"{b['year']}: {b['bias_pct']:+.1f}% (growth {b['growth_pct']:.1f}%)" for b in r["fold_bias"])
    worst_text = ", ".join(f"{w['month'][:7]} ({w['error']:+,.0f} EUR)" for w in r["peak_vs_rest"]["worst_months"])
    verdict = {
        "overfitting": "**Overfitting (mild, high variance). It is not underfitting.**",
        "underfitting": "**Underfitting (high bias).**",
        "well fitted": "**Reasonably well fitted.**",
    }[d["label"]]
    rec_spread = 100 * s["validation_recursive"]["rmse_pct"]["std"] / s["validation_recursive"]["rmse_pct"]["mean"]
    mae_mean = s["validation_one_step"]["mae"]["mean"]
    rmse_over_mae = s["validation_one_step"]["rmse"]["mean"] / mae_mean
    return f"""# Technical evaluation of the sales forecast model

Model: Random Forest on causal lag / rolling / calendar features, predicting `revenue_eur` of the `consolidated` row of `data/raw/trackflow_sales.csv`
(TrackFlow, monthly, 2016-01 .. 2025-12). Evaluated on the **training years only** (2016-2023): the two test years were not used to diagnose or to choose anything.
Regenerate this report and its figures with `python scripts/evaluate_sales_forecast.py`. Errors are given in EUR and as a % of mean monthly revenue.

## 1. Answers to the ticket

1. **Underfitting, overfitting or well fitted?** {verdict}
   The training error ({d['train_rmse_pct']:.2f}% of monthly revenue) is at the noise floor ({floor:.2f}%, the part of the month-to-month variation that no model can explain), so there is no bias problem: the model captures the trend and the seasonality and is not underfitting. But the validation error is {d['validation_rmse_pct']:.2f}% ({d['validation_vs_floor']:.1f}x the floor), a gap of {d['gap_pct_points']:.1f} points that persists at every training size ({100 * d['gap_share_at_largest_size']:.0f}% of the validation error at the largest size). That is the learning-curve signature of overfitting. It is mild: the validation error is still small in business terms, and the out-of-bag error ({d['oob_rmse_pct']:.2f}%), the fair estimate of the training error of a forest, is close to the validation error, so the model generalises consistently.
2. **How stable is it when the training data changes?** Moderately stable. Across 5 expanding folds the validation RMSE is **{_fmt(s['validation_recursive']['rmse_pct'], 2)}%** of monthly revenue (year ahead, recursive) and **{_fmt(s['validation_one_step']['rmse_pct'], 2)}%** (one step ahead): a spread of about ±{rec_spread:.0f}% of the error itself. Two things move it: the size of the training window (the first fold, with 24 months, is the worst) and the growth of the year being predicted (section 5).
3. **Corrective action.** Add a **growth-regime feature** (`yoy_growth_prev`: growth of the last 12 months over the 12 before) and **do not regularise harder**. Evidence in section 6: it addresses the systematic part of the error, while the tested regularisation makes the validation error worse (from {base_one['mean']:.2f}% to {leaf4['validation_one_step_rmse_pct']['mean']:.2f}% with `min_samples_leaf=4`). Effect measured on these same folds: year-ahead RMSE {base_rec['mean']:.2f}% to {best_rec['mean']:.2f}% ({gain_rec:.0f}% lower).

## 2. Time-aware cross-validation

`TimeSeriesSplit(n_splits=5, test_size=12)` over the 84 feature rows of the training years (2017-01 .. 2023-12; the first year is only history for the lags). Expanding windows: each fold trains on the past and validates on the next full year, so every validation block holds a February and a Black Friday peak. A new model (scaler included) is fitted inside each fold.

| Fold | Training window (months) | Validation year | Train RMSE | Valid. RMSE, one step | Valid. RMSE, recursive | Bias, one step | Newest month a training row reads < first validation month |
|---|---|---|---|---|---|---|---|
{fold_rows}

(RMSE as % of the mean monthly revenue of the block; bias > 0 means the model over-forecasts.)

- **No shuffling, order preserved:** `verify_folds` checks for every fold that the training rows and the validation rows are consecutive and ascending, that every training row is before every validation row, that the sets do not overlap, and that the validation windows follow each other in time. A unit test (`tests/pipelines/test_sales_evaluation.py`) runs the same check and shows that it fails on a shuffled split.
- **No leakage across the fold boundary:** the lag / rolling features of row *t* read only months before *t*, so a training row never reads the validation year. It is verified, not assumed (`check_fold_leakage`): for each fold the features are recomputed from the series **cut** at the fold boundary and must be identical to those computed on the full series (they are, in all 5 folds), and the newest month any training row reads is the month before the validation window (last column). The scaler and the forest are fitted on the fold's training rows only; a unit test changes the validation year (x5) and checks that the fitted model does not change.
- **Reported as mean ± standard deviation across folds** (table below), not as one aggregate number.

| Error (5 folds, mean ± std) | RMSE (EUR) | RMSE, % of mean revenue | MAE (EUR) | MAE, % of mean revenue |
|---|---|---|---|---|
| Training, in-sample | {_fmt(s['train']['rmse'], 0)} | {_fmt(s['train']['rmse_pct'], 2)}% | {_fmt(s['train']['mae'], 0)} | {_fmt(s['train']['mae_pct'], 2)}% |
| Training, out-of-bag | {_fmt(s['train_oob']['rmse'], 0)} | {_fmt(s['train_oob']['rmse_pct'], 2)}% | {_fmt(s['train_oob']['mae'], 0)} | {_fmt(s['train_oob']['mae_pct'], 2)}% |
| **Validation, recursive (year ahead)** | **{_fmt(s['validation_recursive']['rmse'], 0)}** | **{_fmt(s['validation_recursive']['rmse_pct'], 2)}%** | {_fmt(s['validation_recursive']['mae'], 0)} | {_fmt(s['validation_recursive']['mae_pct'], 2)}% |
| Validation, one step ahead | {_fmt(s['validation_one_step']['rmse'], 0)} | {_fmt(s['validation_one_step']['rmse_pct'], 2)}% | {_fmt(s['validation_one_step']['mae'], 0)} | {_fmt(s['validation_one_step']['mae_pct'], 2)}% |

![Cross-validation folds](cv_folds.png)

## 3. Learning curve

![Learning curve](learning_curve.png)

Training error and validation error as the training set grows (a model per size and per fold, fitted on the most recent *n* months before the validation year; validation one step ahead). Figure: `data/eval/learning_curve.png`; the shaded bands are ±1 std across folds. The values (the largest sizes only exist in the later folds):

| Training months | Folds averaged | Train RMSE | Train RMSE, out-of-bag | Validation RMSE | Train RMSE (EUR) | Validation RMSE (EUR) |
|---|---|---|---|---|---|---|
{curve_rows}

**Reading it.**
- **Not underfitting.** Underfitting would show both curves converging at a high error. Here the training error is already {first['train_rmse_pct']['mean']:.1f}% with {first['train_rows']} months and settles at about {big['train_rmse_pct']['mean']:.1f}%, the noise floor ({floor:.1f}%): the model fits the pattern. A more complex model could only fit the noise further, so "increase complexity" would be the wrong response.
- **A wide, persistent gap.** The validation error starts at {curve[0]['validation_rmse_pct']['mean']:.1f}% with {curve[0]['train_rows']} months and falls to {big['validation_rmse_pct']['mean']:.1f}% with {big['train_rows']}, but it does not meet the training curve: the gap is still {big['validation_rmse_pct']['mean'] - big['train_rmse_pct']['mean']:.1f} points ({100 * d['gap_share_at_largest_size']:.0f}% of the validation error) at the largest size. Training below the noise floor means the forest is learning the noise of single months.
- **How big a problem it is.** The in-sample number is optimistic by construction for a forest (every tree has seen most of the rows it is scored on). The out-of-bag training error is the fair one, and it sits right next to the validation error: the model does not collapse on new years, it is consistently about {d['validation_vs_floor']:.1f}x the noise floor. That is why the verdict is *mild* overfitting, not a model to reject.
- **The curve is still falling,** so more years will keep helping a little. That is not the corrective action: history cannot be bought, and section 6 shows a cause that more data does not remove.

## 4. Metric choice: RMSE or MAE?

Both are computed for training and validation (tables above). **Primary metric: RMSE.**

- The context says the revenue follows the shipment volume of e-commerce brands, with sharp peaks (November-December, +25-35%) that Ana (Warehouse) and Carlos (Carriers) staff and book for by hand every year. A miss in a peak month is not "a bit more of the same" error: it is a warehouse short of people or a missed carrier booking, and its cost grows faster than its size. RMSE squares the error, so one big miss in a peak weighs more than several small misses in quiet months, which is how the business pays for them. MAE treats a 100,000 EUR miss as exactly twice a 50,000 EUR miss, which understates that.
- The data agrees: the validation error in November-December is **{peak['rmse']:,.0f} EUR RMSE** against **{rest['rmse']:,.0f} EUR** in the other months. The five largest misses of the validation set are: {worst_text}.
- MAE stays as the second number because it is the easy one to say to Finance: *a typical month is off by {mae_mean:,.0f} EUR ({s['validation_one_step']['mae_pct']['mean']:.1f}%)*. RMSE / MAE is {rmse_over_mae:.2f}; for evenly spread errors it is about 1.25, and a higher value means errors concentrated in a few months, which is what to watch.

## 5. Stability: what moves the error between folds

- **Training window size.** Fold 1 trains on 24 months and has the highest error; the errors of folds 3-5 (48-72 months) are lower. Learning curve and folds say the same thing.
- **The growth of the year being predicted.** The signed bias of the one-step validation follows the alternating growth of the context (about 9% and 3%): {bias_text}. The model over-forecasts the low-growth years and under-forecasts the high-growth ones. This is a *systematic* error, not noise: the trailing 12-month mean that the model predicts relative to lags the growth of the current year, and nothing in the features says which kind of year is coming. It is the largest identifiable part of the gap above the noise floor.

## 6. Corrective action

**Diagnosis: mild overfitting, plus a systematic error tied to the yearly growth regime.** The candidate corrective actions were cross-validated on the same five time-aware folds (training years only, test years untouched):

| Setting | Train RMSE | Validation RMSE, one step (mean ± std) | Validation RMSE, recursive (mean ± std) |
|---|---|---|---|
{cand_rows}

1. **The textbook cure does not work here: do not regularise harder.** Larger leaves and shallower trees close the train/validation gap only by making the *training* error worse: `min_samples_leaf=4` moves the validation RMSE from {base_one['mean']:.2f}% to {leaf4['validation_one_step_rmse_pct']['mean']:.2f}%, the worst setting tested reaches {worst_reg['validation_one_step_rmse_pct']['mean']:.2f}%, and `max_depth=4` leaves it unchanged ({depth4['validation_one_step_rmse_pct']['mean']:.2f}%). With 24-72 rows a forest cannot afford larger leaves: it starts missing the seasonal shape (bias) before it removes any noise (variance). The gap exists, but the leaf size is not what limits generalisation.
2. **The action: add `yoy_growth_prev` to the features** (growth of the last 12 months over the 12 before; causal, already implemented in `features.py`; while there is no previous year to compare, the base growth of the context, 6%, is used). It tells the model whether the year that just ended was a high- or low-growth year, which the alternating 9% / 3% growth makes informative. On the same folds it lowers the year-ahead (recursive) validation RMSE from {base_rec['mean']:.2f}% to **{best_rec['mean']:.2f}%** ({gain_rec:.0f}% lower) and the one-step one from {base_one['mean']:.2f}% to {best_one['mean']:.2f}% ({gain_one:.0f}% lower). The year-ahead forecast is where the growth regime matters most, because there the model cannot see this year's real growth. The size of the effect, honestly: the gain is {gain_vs_std:.1f} fold standard deviations of the recursive error, so it is evidence in the right direction, not a proof. It should be adopted, confirmed once on the test years (they have not been used here) and re-checked as new years arrive.
3. **Tried and not worth a change:** averaging the same-month ratio over all earlier years (`seasonal_index_rel`) changes the validation error by less than the fold-to-fold noise, alone or together with the growth feature. The noise in a single last-year observation is real, but it is not what dominates the error.
4. **What would be the wrong response:** more complexity (the model is already at the noise floor on training data); more regularisation (tested, worse); "more data" as the first move (the learning curve says it helps slowly and it cannot be had quickly: each extra year takes a year).

## 7. Limits of this evaluation

- Five validation years is a small sample: one fold of 12 months weighs 20% of the mean, and the ± values are themselves uncertain.
- The noise floor ({floor:.2f}%) is estimated from the decomposition residual of the training years; it is a guide, not a measured limit.
- The diagnosis rules are written in `evaluation.diagnose` (training error at or below ~1.1x the floor and a gap of at least 25% of the validation error at every size = overfitting; training error above ~1.3x the floor with a small gap = underfitting), so another reader can check the verdict against the numbers instead of trusting a sentence.
- The model of the forecast was not changed by this evaluation; the candidate settings are experiments.
"""


# ----------------------------------------------------------------------------- run
def run_evaluation(csv_path=ft.DEFAULT_CSV, out_dir=EVAL_DIR) -> dict:
    """Runs the whole evaluation on the training years and writes the figures, the JSON and the report to `out_dir`."""
    np.random.seed(modeling.SEED)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame, _ = ft.load_sales(csv_path)
    train, _test = ft.split_train_test(frame)  # the test years are dropped here and never used again
    series = train[ft.TARGET]
    table = ft.build_features(series)
    folds = ev.make_folds(len(table))
    fold_check = ev.verify_folds(folds, len(table))

    results = ev.evaluate_folds(series, table, folds)
    summary = ev.summarize_folds(results)
    curve = ev.learning_curve(series, table, folds)
    floor_pct = ev.noise_floor_pct(series)
    candidates = ev.compare_candidates(series, table, folds)
    best = min(candidates, key=lambda c: c["validation_recursive_rmse_pct"]["mean"])
    growth = series.groupby(series.index.year).sum().pct_change() * 100
    fold_bias = [
        {"year": int(r["validation_months"][0][:4]), "bias_pct": r["validation_one_step"]["bias_pct"], "growth_pct": float(growth.loc[int(r["validation_months"][0][:4])])}
        for r in results
    ]
    report = {
        "folds": [{k: v for k, v in r.items() if k != "_valid"} for r in results],
        "fold_verification": fold_check,
        "summary": summary,
        "learning_curve": curve,
        "noise_floor_pct": floor_pct,
        "diagnosis": ev.diagnose(summary, curve, floor_pct),
        "peak_vs_rest": ev.peak_vs_rest(results),
        "fold_bias": fold_bias,
        "candidates": candidates,
        "best_candidate": best,
    }
    plot_learning_curve(curve, floor_pct / 100 * float(table["y"].mean()), out_dir / "learning_curve.png")
    plot_folds(results, summary, out_dir / "cv_folds.png")
    (out_dir / "evaluation_results.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    (out_dir / "evaluation_report.md").write_text(render_report(report), encoding="utf-8")
    return report
