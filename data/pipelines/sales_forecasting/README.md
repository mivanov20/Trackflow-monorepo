# Sales forecasting (Random Forest, 8 years train / 2 years test)

Answers Finance's question: **can we predict TrackFlow's revenue for the coming months from the history, with an acceptable margin of error?**
Short answer: yes. On the 24 months the model never saw, the forecast is off by **3.7% of monthly revenue** (RMSE), against 9.5% for the "repeat last year" baseline.

```powershell
pip install -r data/pipelines/sales_forecasting/requirements.txt
python scripts/train_sales_forecast.py          # trains, scores, writes data/pipelines/sales_forecasting/outputs/
pytest tests/pipelines/test_sales_forecasting.py
```

| File | What it is |
|---|---|
| `data/raw/trackflow_sales.csv` | The provided dataset, unchanged: 120 monthly rows 2016-01 .. 2025-12, the `consolidated` row is the model's series |
| `features.py` | Loading and checks, null handling, the 8/2-year split, the causal features |
| `modeling.py` | Model, forecasts, metrics, decomposition, plots, `run_experiment` |
| `outputs/forecast.png` | **Prediction with its variability range** against the real 2024-2025 data |
| `outputs/decomposition.png` | Trend / seasonality / residual of the training years |
| `outputs/metrics.json`, `forecast_test_years.csv` | Every number reported here, and the month-by-month forecast |

## 1. The series (decomposition)

`seasonal_decompose` (multiplicative, 12 months) on the **training** years shows the pattern described in `CONTEXT-company.md`:

- **Seasonality:** November x1.23 and December x1.20 (the Black Friday and holiday peak, +25-35% in the context), February x0.86 (the post-peak drop, -10-15%), the other months between x0.94 and x1.01.
- **Trend:** always growing. Yearly growth alternates high/low (9.6, 1.8, 9.4, 2.9, 8.0, 2.8, 10.1%), averaging about 6.4%, the "6% +/- 3%" of the context (the monthly noise blurs each yearly figure by a point or two).
- **Residual:** small and without structure, which is why the seasonal features are enough.

`modeling.decompose` turns these into checks, and a test fails if the series stops showing them.

## 2. How the data is handled

- **Nulls and gaps:** the provided file is clean (the data report in `metrics.json` shows 0 nulls, 0 missing months, 0 non-positive values). The loader still handles a dirty file: a missing month becomes an empty row, and every gap is filled by linear interpolation in time (tested).
- **Split:** the first 8 years (2016-01 .. 2023-12, 96 months) train, the last 2 (2024-01 .. 2025-12, 24 months) test. The split is by time, never random, and rejects a series that is not 10 years. The first year of training data is only spent as history for the lags, so the model fits on 84 rows.
- **No leakage:** the scaler and the model are fitted on the training rows only; the uncertainty band comes from the training rows' out-of-bag error; the recursive forecast is given the training series and nothing else. Tests change the test years (x3) and check the trained model is bit-identical.

## 3. Features (all causal)

Every feature of month `t` is built by one function, `features.feature_row(history, month)`, that receives only the months **before** `t` (`history[-1]` is `t-1`). Training, the one-step check and the forecast all use it.

| Feature | What it captures |
|---|---|
| `lag_1`, `lag_12` (as ratios `lag_1_rel`, `lag_12_rel`) | Last month and the same month last year |
| `roll_mean_3` (`roll_mean_3_rel`), `roll_std_6` (`roll_cv_6`) | Recent trend and recent volatility (`shift(1)` semantics: the window ends at `t-1`) |
| `month_sin`, `month_cos`, `quarter` | Calendar: cyclical month (December next to January) and quarter |

The ratios divide by the trailing 12-month mean (the 12 months before `t`). **Why:** the series grows ~6% a year, the test years are higher than any training year, and a tree cannot predict above the levels it saw. So the model predicts `ratio = revenue / trailing mean` (stationary, holds the seasonal shape and the momentum) and the forecast is `ratio x trailing mean`; the trend enters through the trailing mean. The features are standardised with `StandardScaler` (fitted on training rows only).

Most important feature: `lag_12_rel` (59%), the same month last year, then the calendar signals.

## 4. Why Random Forest and not XGBoost

- **Little data:** 84 training rows. Boosting fits each tree to the previous tree's mistakes and starts fitting the noise of single months; a forest averages many decorrelated trees, which is the more stable choice (low variance) at this size. Its out-of-bag predictions also give an error estimate from the training rows alone.
- **Explainability:** Finance has to trust the number. A forest has almost no knobs to defend and `feature_importances_` says what drives it.
- **Tuning time:** this is a feasibility ticket. The forest works with sensible defaults (500 trees, `min_samples_leaf=2`, `max_features=0.7`); XGBoost needs a learning-rate / depth / rounds search to be a fair comparison.
- **What we give up:** XGBoost may be a little more accurate on large data. Here the signal is simple (trend + yearly cycle) and the error is limited by the noise in the data. It is a drop-in swap for the same features if the dashboard later needs it.

`random_state=42` everywhere (`SEED`); a test runs the experiment twice and compares every metric.

## 5. Results on the test set (24 unseen months)

Main result: the **recursive** forecast, the honest one. The model forecasts all 24 months starting right after 2023, feeding its own predictions back as lags, as in production where the future actuals do not exist yet.

| Metric | Recursive (main) | One step ahead | Baseline "repeat last year" |
|---|---|---|---|
| **MSE** | 2.26 x 10^9 EUR^2 | 1.76 x 10^9 EUR^2 | 1.54 x 10^10 EUR^2 |
| RMSE | 47,568 EUR | 41,949 EUR | |
| **RMSE as % of mean monthly revenue** (1.30 M EUR) | **3.66%** | 3.23% | 9.54% |
| MAPE | 3.20% | 2.86% | |
| **PSI** (target ratio, train vs test) | 0.161 | 0.161 | |
| **Gini** | 0.597 | 0.653 | |
| **K2 score** (D'Agostino) | 1.33 (p = 0.51) | 1.27 (p = 0.53) | |
| 80% band covers | 79% of months | 96% of months | |

### What each metric measures, and why a low MSE alone is not enough

- **MSE** is the mean of the squared errors, in EUR^2 (big misses weigh more). It is hard to read alone, so it is reported next to its square root as a share of the average monthly revenue: **the forecast is off by about 3.7% of a typical month (about 48,000 EUR)**. A low MSE alone is not enough because (a) it says nothing about *where* the errors are (a model can be good on average and wrong in the November peak that matters most), (b) it can be low simply because the model memorised the past, so it only counts on unseen months, and (c) it says nothing about whether the data the model sees today looks like the data it was trained on, or whether its errors are random.
- **PSI** (Population Stability Index) compares the distribution of a variable in training and in test: below 0.1 stable, 0.1-0.25 some shift, above 0.25 large. On the target ratio it is **0.16**, a small shift that does not threaten the model. On raw revenue and shipments it is huge (5.9 and 4.1) only because the business grew 6% a year, which is exactly why the model works with ratios. The context asks for PSI on the Los Angeles / Zaragoza mix; the provided file only has the consolidated row, so the mix cannot be measured here, and `shipments_processed` is the closest volume signal. Five bins are used because with 24 test months ten would leave empty bins.
- **Gini** (2 x AUC - 1) measures whether the model ranks months correctly: can it tell a month that sells **above** its trailing-year average from one below it (the way the context asks, a normal low-season month such as February versus the rest)? 1 is perfect, 0 is random. **0.60** is useful but not perfect: the model gets the big seasonal months right and the small differences between ordinary months are mostly noise.
- **K2 score** (D'Agostino-Pearson normality test) checks the residuals. A low K2 (p above 0.05) means the errors look like random noise centred on zero; a high one would mean the model misses a systematic pattern. **K2 = 1.33, p = 0.51**: nothing systematic is left, which is also what justifies reading the band as a variability range.

### Variability range

The band in `outputs/forecast.png` is the point forecast times `exp(q10)` .. `exp(q90)` of the model's **out-of-bag** error on the training rows (an 80% band). On the recursive test it covers 79% of the months, close to the 80% promised, so it is honest and not optimistic. It does not widen with the horizon; the model is steady over 24 months, but a forecast two years out should still be read as a range.

## 6. Limits

- Ten years is a small series; five of the 24 test months fall outside the 80% band, and 2024 grew only 4.0%, at the low end of what the training years show.
- The forecast follows the seasonal and growth pattern of the past. A new client, a lost client or a change in pricing is not in the data and will not be in the forecast.
- Only the consolidated row exists. A per-market forecast (Los Angeles about 60%, Zaragoza about 40%) needs the per-market data.
