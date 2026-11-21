r"""Rename the inventory column `user_uuid` to `user_uid` (one-off, idempotent).

Run it from the repository root. DATABASE_URL is read from the environment or, if it is not
set, from `services/.env` (the same file the API uses). In PowerShell:

    .\services\.venv\Scripts\python.exe scripts\migrate_user_uid.py --dry-run   # only shows what would change
    .\services\.venv\Scripts\python.exe scripts\migrate_user_uid.py             # applies it

`SQLModel.metadata.create_all` never alters existing tables, so databases created before the
rename need this once. Running it again changes nothing. Users and profiles are not touched:
they live in TinyDB, not in PostgreSQL.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

TABLES = ("stockentry", "stockexit")  # fixed names, never user input


def migrate(engine: Engine, dry_run: bool = False) -> list[str]:
    """Rename the column (and its index) in every table that still has the old name.

    With `dry_run=True` nothing is executed; the returned list is what would change.
    """
    changes: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:  # one transaction: all tables or none
        for table in TABLES:
            if table not in existing_tables:
                continue
            columns = {column["name"] for column in inspector.get_columns(table)}
            if "user_uuid" not in columns or "user_uid" in columns:
                continue
            if dry_run:
                changes.append(f"{table}: user_uuid -> user_uid")
                continue
            connection.execute(text(f"ALTER TABLE {table} RENAME COLUMN user_uuid TO user_uid"))
            if engine.dialect.name == "postgresql":
                connection.execute(text(f"ALTER INDEX IF EXISTS ix_{table}_user_uuid RENAME TO ix_{table}_user_uid"))
            else:  # SQLite cannot rename an index: recreate it
                connection.execute(text(f"DROP INDEX IF EXISTS ix_{table}_user_uuid"))
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_uid ON {table} (user_uid)"))
            changes.append(f"{table}: user_uuid -> user_uid")
    return changes


def load_env_file() -> None:
    """Load `services/.env` when it exists; real environment variables always take precedence."""
    env_file = REPO_ROOT / "services" / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv  # installed with uvicorn[standard]

        load_dotenv(env_file, override=False)


def main(argv: list[str] | None = None) -> int:
    """Run the migration; exit code 0 on success, 1 on any failure."""
    arguments = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in arguments
    unknown = [argument for argument in arguments if argument != "--dry-run"]
    if unknown:
        print(f"Unknown argument(s): {' '.join(unknown)}. The only option is --dry-run.", file=sys.stderr)
        return 1

    load_env_file()
    if not os.getenv("DATABASE_URL"):
        print(
            "DATABASE_URL is not set. Put it in services/.env (DATABASE_URL=...) or set it in this terminal, "
            "using the same database URL the API uses.",
            file=sys.stderr,
        )
        return 1
    from services.database import engine

    try:
        changes = migrate(engine, dry_run=dry_run)
    except SQLAlchemyError as error:
        # Only the type: SQLAlchemy messages can embed the database host or user.
        print(f"Migration failed ({type(error).__name__}); nothing was changed.", file=sys.stderr)
        return 1
    if not changes:
        print("Nothing to migrate: the database is already up to date.")
    elif dry_run:
        print("Would change (dry run, nothing was modified):")
        print("\n".join(changes))
    else:
        print("\n".join(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
