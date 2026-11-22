"""Trains and evaluates the TrackFlow sales forecast.

    python scripts/train_sales_forecast.py [--csv data/raw/trackflow_sales.csv] [--out data/pipelines/sales_forecasting/outputs]

Loads the consolidated monthly revenue, trains on the first 8 years (2016-2023), forecasts the last 2 years
(2024-2025) that the model never saw, prints MSE / PSI / Gini / K2 and saves the metrics, the forecast table and the
plots (forecast with its variability range, and the trend / seasonality / residual decomposition).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.pipelines.sales_forecasting import features, modeling  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default=str(features.DEFAULT_CSV))
    parser.add_argument("--out", default=str(modeling.DEFAULT_OUT))
    parser.add_argument("--seed", type=int, default=modeling.SEED)
    args = parser.parse_args(argv)

    report = modeling.run_experiment(args.csv, args.out, args.seed)
    m, o, n = report["metrics_recursive_forecast"], report["metrics_one_step_ahead"], report["baseline_seasonal_naive"]
    print(f"split  train {report['split']['train']}  test {report['split']['test']}  seed {report['seed']}")
    print("\nTest-set metrics (24 unseen months), recursive forecast:")
    print(f"  MSE   {m['mse_eur2']:,.0f} EUR^2   (RMSE {m['rmse_eur']:,.0f} EUR = {m['rmse_pct_of_mean_monthly_revenue']:.2f}% of mean monthly revenue; MAPE {m['mape_pct']:.2f}%)")
    print(f"  PSI   {m['psi_target_ratio']:.3f} on the target ratio   ({m['psi_revenue_eur']:.3f} on raw revenue, {m['psi_shipments']:.3f} on shipments)")
    print(f"  Gini  {m['gini']:.3f}")
    print(f"  K2    {m['k2_statistic']:.2f}  (p = {m['k2_p_value']:.3f}; residuals normal at 5%: {m['residuals_look_normal_at_5pct']})")
    print(f"  80% band covers {m['interval_coverage_pct']:.0f}% of the test months")
    print(f"\nOne step ahead: RMSE {o['rmse_pct_of_mean_monthly_revenue']:.2f}%, MAPE {o['mape_pct']:.2f}%, Gini {o['gini']:.3f}")
    print(f"Seasonal-naive baseline: RMSE {n['rmse_pct_of_mean_monthly_revenue']:.2f}% of mean monthly revenue")
    print("\nDecomposition vs CONTEXT:", report["decomposition"]["checks"])
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
