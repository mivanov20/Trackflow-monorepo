"""Rename the inventory column `user_uuid` to `user_uid` (one-off, idempotent).

Usage (repository root, with DATABASE_URL set as for the API):

    services/.venv/Scripts/python scripts/migrate_user_uid.py

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


def migrate(engine: Engine) -> list[str]:
    """Rename the column (and its index) in every table that still has the old name."""
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
            connection.execute(text(f"ALTER TABLE {table} RENAME COLUMN user_uuid TO user_uid"))
            if engine.dialect.name == "postgresql":
                connection.execute(text(f"ALTER INDEX IF EXISTS ix_{table}_user_uuid RENAME TO ix_{table}_user_uid"))
            else:  # SQLite cannot rename an index: recreate it
                connection.execute(text(f"DROP INDEX IF EXISTS ix_{table}_user_uuid"))
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_uid ON {table} (user_uid)"))
            changes.append(f"{table}: user_uuid -> user_uid")
    return changes


def main() -> int:
    """Run the migration; exit code 0 on success, 1 on any failure."""
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. Point it at the same database the API uses.", file=sys.stderr)
        return 1
    from services.database import engine

    try:
        changes = migrate(engine)
    except SQLAlchemyError as error:
        # Only the type: SQLAlchemy messages can embed the database host or user.
        print(f"Migration failed ({type(error).__name__}); nothing was changed.", file=sys.stderr)
        return 1
    print("\n".join(changes) if changes else "Nothing to migrate: the database is already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
