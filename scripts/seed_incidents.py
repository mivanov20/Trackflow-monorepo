"""Seed the incident manager with the historical incidents from the analyzer CSV.

Usage (from the repository root, with DATABASE_URL set as for the API):

    services/.venv/Scripts/python scripts/seed_incidents.py [path/to/incidents-trackflow.csv]

Every valid row is loaded as a `customer` incident after the shared CSV -> model
transformations. Invalid rows are not inserted and are listed at the end. The script is
idempotent: the CSV `incident_id` of each imported row is kept in a ledger table, so
running it again inserts nothing new.
"""

import csv
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.engine import Engine  # noqa: E402
from sqlmodel import Session  # noqa: E402
from trackflow_shared.incidents import process_csv_row  # noqa: E402

from services.models import Incident, IncidentSeedLedger  # noqa: E402

DEFAULT_CSV = REPO_ROOT / "data" / "raw" / "incidents-trackflow.csv"


@dataclass
class SeedReport:
    """Outcome of one seed run."""

    inserted: int = 0
    already_loaded: int = 0
    invalid: list[tuple[int, str]] = field(default_factory=list)  # (CSV line, reasons)


def seed_incidents(csv_path: Path, engine: Engine) -> SeedReport:
    """Load `csv_path` into the database behind `engine` and return what happened."""
    report = SeedReport()
    with csv_path.open(newline="", encoding="utf-8-sig") as handle, Session(engine) as session:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            incident, reasons = process_csv_row(row)
            if incident is None:
                report.invalid.append((line_number, "; ".join(reasons)))
                continue

            source_id = row["incident_id"].strip()
            if session.get(IncidentSeedLedger, source_id) is not None:
                report.already_loaded += 1
                continue

            session.add(Incident(**incident))
            session.add(IncidentSeedLedger(source_id=source_id))
            session.commit()
            report.inserted += 1
    return report


def print_report(report: SeedReport) -> None:
    print(f"Inserted {report.inserted} incident(s); {report.already_loaded} already loaded (skipped).")
    if report.invalid:
        print(f"{len(report.invalid)} invalid record(s) were NOT inserted:")
        for line_number, reasons in report.invalid:
            print(f"  line {line_number}: {reasons}")
    else:
        print("No invalid records.")


def main(argv: list[str]) -> int:
    csv_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 1
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. Point it at the same database the API uses.", file=sys.stderr)
        return 1

    from services.database import create_db_and_tables, engine  # needs DATABASE_URL

    create_db_and_tables()
    print_report(seed_incidents(csv_path, engine))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
