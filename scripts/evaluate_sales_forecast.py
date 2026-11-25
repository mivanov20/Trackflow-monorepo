"""Technical evaluation of the sales forecast model (cross-validation, learning curve, MAE / RMSE, diagnosis).

    python scripts/evaluate_sales_forecast.py [--csv data/raw/trackflow_sales.csv] [--out data/eval]

Uses the training years only (2016-2023). Writes data/eval/learning_curve.png, cv_folds.png, evaluation_results.json and
evaluation_report.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.pipelines.sales_forecasting import evaluation_report, features  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default=str(features.DEFAULT_CSV))
    parser.add_argument("--out", default=str(evaluation_report.EVAL_DIR))
    args = parser.parse_args(argv)

    report = evaluation_report.run_evaluation(args.csv, args.out)
    s, d = report["summary"], report["diagnosis"]
    print(f"folds: {report['fold_verification']['folds']} (chronological, not shuffled), training sizes {report['fold_verification']['train_sizes']}")
    for part, label in (("train", "training (in-sample)"), ("train_oob", "training (out-of-bag)"), ("validation_one_step", "validation, one step"), ("validation_recursive", "validation, recursive")):
        print(f"  {label:24s} RMSE {s[part]['rmse']['mean']:>9,.0f} +/- {s[part]['rmse']['std']:>7,.0f} EUR ({s[part]['rmse_pct']['mean']:.2f}% +/- {s[part]['rmse_pct']['std']:.2f})   MAE {s[part]['mae']['mean']:>9,.0f} +/- {s[part]['mae']['std']:>7,.0f}")
    print(f"noise floor {report['noise_floor_pct']:.2f}%   diagnosis: {d['label']}")
    best = report["best_candidate"]
    print(f"best corrective candidate: {best['setting']} (recursive validation RMSE {best['validation_recursive_rmse_pct']['mean']:.2f}%)")
    print(f"saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
