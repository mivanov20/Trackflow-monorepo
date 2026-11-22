"""Tests of the technical evaluation: chronological folds, no leakage across the fold boundary, metrics, diagnosis."""

import numpy as np
import pandas as pd
import pytest

from data.pipelines.sales_forecasting import evaluation as ev
from data.pipelines.sales_forecasting import features as ft
from data.pipelines.sales_forecasting import modeling


@pytest.fixture(scope="module")
def series():
    frame, _ = ft.load_sales()
    train, _test = ft.split_train_test(frame)
    return train[ft.TARGET]  # the evaluation only ever sees the training years


@pytest.fixture(scope="module")
def table(series):
    return ft.build_features(series)


@pytest.fixture(scope="module")
def folds(table):
    return ev.make_folds(len(table))


# ------------------------------------------------------------------ the folds keep the chronological order
def test_there_are_at_least_five_folds_over_the_training_years(folds, table):
    assert len(folds) >= 5
    assert len(table) == 84 and table.index.max() == pd.Timestamp("2023-12-01")  # never a test year


def test_every_fold_preserves_the_chronological_order_of_the_data(folds, table):
    months = table.index
    for train, test in folds:
        assert (np.diff(train) == 1).all() and (np.diff(test) == 1).all()  # ascending and consecutive: not shuffled
        assert months[train].is_monotonic_increasing and months[test].is_monotonic_increasing
        assert months[train].max() < months[test].min()  # nothing of the validation block is before the training block


def test_no_index_of_a_later_fold_appears_before_one_of_an_earlier_fold(folds, table):
    for (_, earlier_test), (later_train, later_test) in zip(folds, folds[1:]):
        assert earlier_test.max() < later_test.min()  # the validation windows follow each other in time
        assert later_train.max() >= earlier_test.max()  # the later fold has seen the earlier validation year as past
        assert not set(earlier_test) & set(later_test)
    firsts = [test.min() for _, test in folds]
    assert firsts == sorted(firsts)  # fold k+1 never starts before fold k


def test_verify_folds_accepts_the_real_folds_and_reports_them(folds, table):
    report = ev.verify_folds(folds, len(table))
    assert report["folds"] == 5 and report["chronological"] and not report["shuffled"]
    assert report["train_sizes"] == sorted(report["train_sizes"])  # expanding windows
    assert all(size == 12 for size in report["validation_sizes"])  # a full seasonal cycle per fold


def test_verify_folds_rejects_a_shuffled_split(folds, table):
    rng = np.random.default_rng(0)
    shuffled = [(rng.permutation(train), test) for train, test in folds]
    with pytest.raises(AssertionError):
        ev.verify_folds(shuffled, len(table))


def test_verify_folds_rejects_a_training_row_after_a_validation_row(folds, table):
    train, test = folds[2]
    leaky = list(folds)
    leaky[2] = (np.append(train, test[0]), test)  # the first validation month leaks into training
    with pytest.raises(AssertionError):
        ev.verify_folds(leaky, len(table))


def test_verify_folds_rejects_overlapping_validation_windows(folds, table):
    broken = list(folds)
    broken[3] = (broken[3][0], folds[2][1])  # same validation window as the fold before
    with pytest.raises(AssertionError):
        ev.verify_folds(broken, len(table))


# ------------------------------------------------------------------ no leakage across the fold boundary
def test_the_features_of_every_training_row_are_the_same_when_the_validation_window_is_removed(series, table, folds):
    for train, test in folds:
        check = ev.check_fold_leakage(series, table, train, test)
        assert check["features_identical_with_validation_removed"]
        assert check["months_between_training_end_and_validation_start"] == 1  # the validation block starts right after
        assert check["newest_month_read_by_training"] < check["first_validation_month"]


def test_a_feature_computed_on_the_full_series_before_splitting_would_be_caught(series, table, folds):
    """The classic mistake: centred rolling stats computed on the whole series peek into the next fold. The check must fail."""
    train, test = folds[1]
    leaky = table.copy()
    leaky["roll_mean_3"] = series.rolling(3, center=True).mean().reindex(leaky.index)  # looks one month ahead
    leaky["lag_1_rel"] = (series.shift(-1) / leaky["roll_mean_12"]).reindex(leaky.index).fillna(1.0)  # reads the future
    with pytest.raises(AssertionError):
        ev.check_fold_leakage(series, leaky, train, test)


def test_changing_the_validation_year_does_not_change_the_model_fitted_in_the_fold(series, table, folds):
    train_idx, test_idx = folds[2]
    first_validation_month = table.index[test_idx[0]]

    def fitted(s):
        t = ft.build_features(s.loc[s.index < first_validation_month])
        model, band = modeling.fit(t)
        return model.predict(t[ft.MODEL_FEATURES]), band

    changed = series.copy()
    changed.loc[changed.index >= first_validation_month] *= 5  # wreck the validation year and everything after it
    (p1, b1), (p2, b2) = fitted(series), fitted(changed)
    np.testing.assert_array_equal(p1, p2)
    assert b1 == b2


def test_the_extra_features_of_the_corrective_experiments_are_causal_too(series):
    position = 70
    month = series.index[position]
    for builder in (ft.yoy_growth_prev, ft.seasonal_index_rel):
        before = builder(series.iloc[:position].to_numpy())
        changed = series.copy()
        changed.iloc[position:] *= 10
        assert before == builder(changed.iloc[:position].to_numpy())
    row = ft.feature_row(series.iloc[:position].to_numpy(), month)
    assert row["yoy_growth_prev"] == pytest.approx(series.iloc[position - 12 : position].mean() / series.iloc[position - 24 : position - 12].mean() - 1)
    assert ft.yoy_growth_prev(series.iloc[:20].to_numpy()) == ft.BASE_GROWTH  # no previous year yet


# ------------------------------------------------------------------ metrics, curve and diagnosis
def test_mae_and_rmse_and_why_rmse_punishes_one_big_miss():
    actual = np.array([100.0, 100.0, 100.0, 100.0])
    spread, concentrated = np.array([110.0, 90.0, 110.0, 90.0]), np.array([100.0, 100.0, 100.0, 140.0])
    assert ev.mae(actual, spread) == pytest.approx(10) and ev.mae(actual, concentrated) == pytest.approx(10)  # same MAE
    assert ev.rmse(actual, spread) == pytest.approx(10) and ev.rmse(actual, concentrated) == pytest.approx(20)  # RMSE sees the big miss
    assert ev.error_metrics(actual, concentrated)["bias_pct"] == pytest.approx(10)  # over-forecast


@pytest.fixture(scope="module")
def evaluation(tmp_path_factory):
    from data.pipelines.sales_forecasting import evaluation_report

    return evaluation_report.run_evaluation(out_dir=tmp_path_factory.mktemp("eval")), tmp_path_factory.getbasetemp()


def test_the_evaluation_reports_mean_and_std_across_folds_for_both_metrics(evaluation):
    report, _ = evaluation
    for part in ("train", "validation_recursive", "validation_one_step"):
        for metric in ("mae", "rmse", "rmse_pct"):
            stats = report["summary"][part][metric]
            assert stats["std"] > 0 and stats["mean"] > 0
    assert len(report["folds"]) == 5 and all(f["leakage_check"]["features_identical_with_validation_removed"] for f in report["folds"])


def test_the_learning_curve_has_a_training_and_a_validation_error_per_size(evaluation):
    report, _ = evaluation
    curve = report["learning_curve"]
    assert [p["train_rows"] for p in curve] == sorted(p["train_rows"] for p in curve) and len(curve) >= 5
    assert all(p["validation_rmse"]["mean"] > p["train_rmse"]["mean"] for p in curve)  # a forest fits its own training rows better
    assert curve[0]["validation_rmse"]["mean"] > curve[-1]["validation_rmse"]["mean"]  # more data helps


def test_the_diagnosis_follows_the_written_rules_and_the_report_states_it(evaluation, tmp_path):
    report, _ = evaluation
    d = report["diagnosis"]
    if d["label"] == "overfitting":
        assert d["train_rmse_pct"] <= 1.1 * d["noise_floor_pct"] and d["gap_share_at_largest_size"] >= 0.25
    elif d["label"] == "underfitting":
        assert d["train_rmse_pct"] > 1.3 * d["noise_floor_pct"]
    from data.pipelines.sales_forecasting import evaluation_report

    text = evaluation_report.render_report(report)
    assert d["label"] in text.lower() and "learning_curve.png" in text and "±" in text


def test_the_figures_and_the_report_are_saved(tmp_path):
    from data.pipelines.sales_forecasting import evaluation_report

    evaluation_report.run_evaluation(out_dir=tmp_path)
    for name in ("learning_curve.png", "cv_folds.png", "evaluation_report.md", "evaluation_results.json"):
        assert (tmp_path / name).stat().st_size > 0


def test_diagnose_tells_underfitting_overfitting_and_a_good_fit_apart():
    def summary(train, valid):
        return {"train": {"rmse_pct": {"mean": train}}, "validation_one_step": {"rmse_pct": {"mean": valid}}, "train_oob": {"rmse_pct": {"mean": valid}}}

    def curve(train, valid):
        return [{"folds_used": 3, "train_rmse_pct": {"mean": train}, "validation_rmse_pct": {"mean": v}} for v in (valid * 1.3, valid)]

    assert ev.diagnose(summary(6.0, 6.5), curve(6.0, 6.5), floor_pct=2.5)["label"] == "underfitting"  # both high and close
    assert ev.diagnose(summary(2.0, 4.5), curve(2.0, 4.5), floor_pct=2.5)["label"] == "overfitting"  # low train, wide gap
    assert ev.diagnose(summary(2.6, 3.0), curve(2.6, 3.0), floor_pct=2.5)["label"] == "well fitted"  # low and close
