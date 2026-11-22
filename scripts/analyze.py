"""Analyse the after-sales incident file and print a summary for the CX team.

Usage (from anywhere, no installation needed, Python 3.11+, standard library only):

    python scripts/analyze.py incidents-trackflow.csv
    python analyze.py incidents-trackflow.csv            # when run from the scripts/ folder

The validation rules and the metrics live in `packages/shared/trackflow_shared/incident_analysis.py`
and are the same ones the API uses (`POST /api/incidents/analyze`).

The file contains customer emails and must stay on this machine. The script never prints, logs or
exports an individual email (or any other cell value): invalid records are reported by line number
and rule only, and unexpected errors are reported without details.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "shared"))

from trackflow_shared.incident_analysis import InvalidFileError, analyze_path, write_results_csv  # noqa: E402
from trackflow_shared.incident_report import console_supports_unicode, render_report  # noqa: E402

RESULTS_FILE = "results.csv"


def ask_export() -> bool:
    """Ask whether to export; anything but yes/no asks again, and a closed input counts as no."""
    while True:
        try:
            answer = input("Export results to CSV? [y / n]: ").strip().lower()
        except EOFError:
            print()
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def export_results(result, path: Path) -> int:
    """Write the results CSV (one row per metric); returns the number of metrics written."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        return write_results_csv(result, handle)


def main(argv: list[str] | None = None) -> int:
    """Run the analysis; returns the exit code (0 ok, 1 when the file cannot be analysed or results cannot be saved)."""
    parser = argparse.ArgumentParser(prog="analyze.py", description="Analyse an incident CSV file and print a summary.")
    parser.add_argument("csv_path", help="path to the incidents CSV file, for example incidents-trackflow.csv")
    parser.add_argument("--export", action="store_true", help=f"write {RESULTS_FILE} without asking")
    args = parser.parse_args(argv)

    path = Path(args.csv_path)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    try:
        result = analyze_path(str(path), path.name)
    except InvalidFileError as error:
        print(f"Cannot analyse {path.name}: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Cannot read {path.name}: {error.strerror or 'I/O error'}.", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - last resort; the message must never carry file content
        print(
            f"Unexpected error while analysing the file ({type(error).__name__}). "
            "No details are shown so that customer data is never exposed.",
            file=sys.stderr,
        )
        return 1

    print(render_report(result, unicode_ok=console_supports_unicode()))

    if args.export or ask_export():
        target = Path(RESULTS_FILE)
        try:
            count = export_results(result, target)
        except OSError as error:
            print(f"Could not write {RESULTS_FILE}: {error.strerror or 'I/O error'}.", file=sys.stderr)
            return 1
        print(f"Results saved to {target} ({count} metrics, one per row).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
