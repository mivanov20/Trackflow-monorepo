"""Tests of the sales forecast: the 8-year / 2-year split, causal features, and the metrics."""

import numpy as np
import pandas as pd
import pytest

from data.pipelines.sales_forecasting import features as ft
from data.pipelines.sales_forecasting import modeling


@pytest.fixture(scope="module")
def frame():
    frame, _ = ft.load_sales()
    return frame


@pytest.fixture(scope="module")
def series(frame):
    return frame[ft.TARGET]


# ------------------------------------------------------------------ the data matches CONTEXT
def test_the_dataset_matches_the_context(frame):
    assert len(frame) == 120
    assert str(frame.index.min().date()) == "2016-01-01" and str(frame.index.max().date()) == "2025-12-01"
    assert (frame.index == pd.date_range("2016-01-01", "2025-12-01", freq="MS")).all()  # no missing months
    assert (frame[ft.TARGET] > 0).all()
    assert not frame.isna().any().any()


def test_missing_values_and_missing_months_are_filled_not_crashed(tmp_path):
    text = ft.DEFAULT_CSV.read_text(encoding="utf-8").splitlines()
    text[10] = text[10].split(",")[0] + ",,,," + "consolidated"  # an empty row: month kept, values empty
    del text[20]  # a missing month
    path = tmp_path / "dirty.csv"
    path.write_text("\n".join(text), encoding="utf-8")
    frame, report = ft.load_sales(path)
    assert len(frame) == 120 and not frame.isna().any().any()
    assert report["values_filled"] > 0 and len(report["missing_months"]) == 1


# ------------------------------------------------------------------ split: 8 years train, 2 years test, no mixing
def test_split_is_eight_years_for_training_and_the_two_most_recent_for_testing(frame):
    train, test = ft.split_train_test(frame)
    assert len(train) == 96 and len(test) == 24
    assert train.index.min().year == 2016 and train.index.max().year == 2023
    assert test.index.min().year == 2024 and test.index.max().year == 2025
    assert train.index.max() < test.index.min()  # time order: the test years come after every training month


def test_no_month_is_in_both_sets_and_together_they_are_the_whole_series(frame):
    train, test = ft.split_train_test(frame)
    assert not set(train.index) & set(test.index)
    assert train.index.append(test.index).equals(frame.index)


def test_the_split_rejects_a_series_that_is_not_ten_years(frame):
    with pytest.raises(ValueError):
        ft.split_train_test(frame.iloc[:100])


def test_the_model_rows_and_the_scaler_use_training_months_only(frame, series):
    train, test = ft.split_train_test(frame)
    table = ft.build_features(series)
    train_f = table.loc[table.index <= train.index[-1]]
    assert train_f.index.max() <= train.index.max()
    model, _ = modeling.fit(train_f)
    scaler = model.named_steps["scale"]
    assert scaler.n_samples_seen_ == len(train_f)  # the scaler never saw a test row
    np.testing.assert_allclose(scaler.mean_, train_f[ft.MODEL_FEATURES].mean().to_numpy())


def test_changing_the_test_years_does_not_change_the_trained_model(frame, series):
    train, test = ft.split_train_test(frame)
    changed = series.copy()
    changed.loc[test.index] = changed.loc[test.index] * 3  # wreck the test years

    def trained(s):
        table = ft.build_features(s)
        model, band = modeling.fit(table.loc[table.index <= train.index[-1]])
        return model.predict(table.loc[table.index <= train.index[-1], ft.MODEL_FEATURES]), band

    (p1, b1), (p2, b2) = trained(series), trained(changed)
    np.testing.assert_array_equal(p1, p2)  # same model: the test years had no influence on training
    assert b1 == b2


def test_the_recursive_forecast_does_not_read_the_test_actuals(frame, series):
    train, test = ft.split_train_test(frame)
    table = ft.build_features(series)
    model, band = modeling.fit(table.loc[table.index <= train.index[-1]])
    one = modeling.forecast_recursive(model, band, train[ft.TARGET], test.index)
    wrecked = train[ft.TARGET]  # the function is only given the training series: nothing else can leak in
    two = modeling.forecast_recursive(model, band, wrecked, test.index)
    pd.testing.assert_frame_equal(one, two)


# ------------------------------------------------------------------ features are causal
def test_lag_features_equal_the_raw_value_of_the_past_never_the_current_or_future(series):
    table = ft.build_features(series)
    for month in table.index:
        position = series.index.get_loc(month)
        assert table.loc[month, "lag_1"] == series.iloc[position - 1]
        assert table.loc[month, "lag_12"] == series.iloc[position - 12]
        assert table.loc[month, "lag_1"] != series.iloc[position]


def test_rolling_features_use_only_the_months_before_the_row(series):
    table = ft.build_features(series)
    shifted = series.shift(1)  # what a pandas pipeline must do: shift(1) BEFORE rolling()
    for column, expected in {
        "roll_mean_3": shifted.rolling(3).mean(),
        "roll_mean_12": shifted.rolling(12).mean(),
        "roll_std_6": shifted.rolling(6).std(),
    }.items():
        np.testing.assert_allclose(table[column], expected.loc[table.index], err_msg=column)


def test_a_feature_row_never_depends_on_the_value_of_its_own_month_or_later(series):
    position = 60
    month = series.index[position]
    before = ft.feature_row(series.iloc[:position].to_numpy(), month)
    changed = series.copy()
    changed.iloc[position:] = changed.iloc[position:] * 10  # the present and the whole future change
    after = ft.feature_row(changed.iloc[:position].to_numpy(), month)
    assert before == after


def test_changing_the_future_leaves_the_features_of_the_past_untouched(series):
    base = ft.build_features(series)
    changed = series.copy()
    changed.iloc[100:] = changed.iloc[100:] * 5
    after = ft.build_features(changed)
    past = base.index[: 100 - ft.MIN_HISTORY]  # rows whose month is before the first changed month
    pd.testing.assert_frame_equal(base.loc[past, ft.RAW_COLUMNS + ft.MODEL_FEATURES], after.loc[past, ft.RAW_COLUMNS + ft.MODEL_FEATURES])


def test_calendar_features_are_cyclical_and_the_row_needs_a_full_year_of_history():
    december = ft.feature_row(np.arange(1, 25, dtype=float), pd.Timestamp("2024-12-01"))
    january = ft.feature_row(np.arange(1, 25, dtype=float), pd.Timestamp("2025-01-01"))
    assert december["quarter"] == 4 and january["quarter"] == 1
    assert np.hypot(december["month_sin"], december["month_cos"]) == pytest.approx(1)
    assert january["month_sin"] == pytest.approx(0) and january["month_cos"] == pytest.approx(1)
    with pytest.raises(ValueError):
        ft.feature_row(np.arange(11, dtype=float), pd.Timestamp("2025-01-01"))


# ------------------------------------------------------------------ metrics and the experiment
def test_psi_is_zero_for_the_same_distribution_and_large_for_a_shifted_one():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 500)
    assert modeling.psi(base, base) == pytest.approx(0, abs=1e-6)
    assert modeling.psi(base, base + 3) > 0.25


def test_gini_is_one_for_a_perfect_ranking_and_zero_for_a_constant_score():
    assert modeling.gini([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1)
    assert modeling.gini([0, 0, 1, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0)


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    return modeling.run_experiment(out_dir=tmp_path_factory.mktemp("forecast"))


def test_the_experiment_reports_all_four_metrics_on_the_test_set_and_beats_the_naive_baseline(experiment):
    m = experiment["metrics_recursive_forecast"]
    for key in ("mse_eur2", "psi_target_ratio", "gini", "k2_statistic"):
        assert np.isfinite(m[key]), key
    assert experiment["split"]["test"][2] == 24 and experiment["split"]["train"][2] == 96
    assert m["rmse_pct_of_mean_monthly_revenue"] < experiment["baseline_seasonal_naive"]["rmse_pct_of_mean_monthly_revenue"]
    assert m["rmse_pct_of_mean_monthly_revenue"] < 8  # an acceptable margin of error for a monthly revenue forecast
    assert m["gini"] > 0.4
    assert 60 <= m["interval_coverage_pct"] <= 100


def test_the_experiment_is_reproducible_and_saves_the_plot_and_the_tables(experiment, tmp_path):
    again = modeling.run_experiment(out_dir=tmp_path)
    assert again["metrics_recursive_forecast"] == experiment["metrics_recursive_forecast"]  # fixed seed
    for name in ("forecast.png", "decomposition.png", "metrics.json", "forecast_test_years.csv"):
        assert (tmp_path / name).stat().st_size > 0
    assert all(experiment["decomposition"]["checks"].values())  # the series shows what CONTEXT describes
