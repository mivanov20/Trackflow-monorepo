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
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlmodel import Session  # noqa: E402
from trackflow_shared.incidents import CSV_REQUIRED_FIELDS, process_csv_row  # noqa: E402

from services.models import Incident, IncidentSeedLedger  # noqa: E402

DEFAULT_CSV = REPO_ROOT / "data" / "raw" / "incidents-trackflow.csv"


@dataclass
class SeedReport:
    """Outcome of one seed run."""

    inserted: int = 0
    already_loaded: int = 0
    invalid: list[tuple[int, str]] = field(default_factory=list)  # (CSV line, reasons)


class SeedError(Exception):
    """Critical seed failure. The message is safe to print: no credentials or connection details."""


def read_csv_rows(csv_path: Path) -> list[tuple[int, dict[str, str]]]:
    """Read every data row as `(CSV line number, row)` after checking the header.

    Raises `SeedError` when the file cannot be read, is not UTF-8, is malformed, lacks a
    required column or has no data rows, so nothing is written to the database.
    """
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [name for name in CSV_REQUIRED_FIELDS if name not in (reader.fieldnames or [])]
            if missing:
                raise SeedError(f"The CSV header is missing required column(s): {', '.join(missing)}.")
            rows = list(enumerate(reader, start=2))
    except OSError as error:
        raise SeedError(f"Could not read the CSV file: {error.strerror or 'I/O error'}.") from error
    except UnicodeDecodeError as error:
        raise SeedError("The CSV file is not valid UTF-8 text. Re-export it as UTF-8 and try again.") from error
    except csv.Error as error:
        raise SeedError(f"The CSV file is malformed: {error}.") from error
    if not rows:
        raise SeedError("The CSV file has a header but no data rows.")
    return rows


def seed_incidents(csv_path: Path, engine: Engine) -> SeedReport:
    """Load `csv_path` into the database behind `engine` and return what happened."""
    rows = read_csv_rows(csv_path)
    report = SeedReport()
    with Session(engine) as session:
        for line_number, row in rows:
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
            try:
                session.commit()
            except SQLAlchemyError as error:
                session.rollback()
                raise SeedError(
                    f"The database rejected CSV line {line_number} ({type(error).__name__}). "
                    f"{report.inserted} incident(s) were inserted before the failure; "
                    "rows already loaded are skipped, so the script can be run again safely."
                ) from error
            report.inserted += 1
    return report


def print_report(report: SeedReport) -> None:
    """Print the outcome of a seed run to stdout."""
    print(f"Inserted {report.inserted} incident(s); {report.already_loaded} already loaded (skipped).")
    if report.invalid:
        print(f"{len(report.invalid)} invalid record(s) were NOT inserted:")
        for line_number, reasons in report.invalid:
            print(f"  line {line_number}: {reasons}")
    else:
        print("No invalid records.")


def main(argv: list[str]) -> int:
    """Run the seed from the command line; returns the process exit code (0 ok, 1 failure)."""
    csv_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 1
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. Point it at the same database the API uses.", file=sys.stderr)
        return 1

    from services.database import create_db_and_tables, engine  # needs DATABASE_URL

    # Database errors can embed host or user names, so only the error type is printed.
    try:
        create_db_and_tables()
    except SQLAlchemyError as error:
        print(
            f"Could not prepare the database ({type(error).__name__}). "
            "Check DATABASE_URL and that the database is reachable.",
            file=sys.stderr,
        )
        return 1

    try:
        report = seed_incidents(csv_path, engine)
    except SeedError as error:
        print(f"Seed failed: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError as error:
        print(
            f"Seed failed: a database error interrupted the import ({type(error).__name__}). "
            "Rows already loaded are skipped, so it is safe to run the script again.",
            file=sys.stderr,
        )
        return 1

    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
