"""Seeder: loads the initial supplier directory into TinyDB (`uv run seed`)."""

import json
from pathlib import Path

from .models import SupplierCreate
from .repository import SERVICE_ROOT, SupplierRepository, get_db_path

SEED_FILE = SERVICE_ROOT / "data" / "suppliers_seed.json"


def seed_repository(repo: SupplierRepository, seed_file: Path = SEED_FILE) -> int:
    """Insert seed suppliers that are not already stored; return how many were inserted."""
    entries = json.loads(seed_file.read_text(encoding="utf-8"))
    existing = repo.names()
    inserted = 0
    for entry in entries:
        supplier = SupplierCreate(**entry)
        if supplier.name.casefold() in existing:
            continue
        repo.create(supplier)
        existing.add(supplier.name.casefold())
        inserted += 1
    return inserted


def main() -> None:
    """Entry point for `uv run seed`."""
    repo = SupplierRepository(get_db_path())
    try:
        inserted = seed_repository(repo)
        total = len(repo.list())
    finally:
        repo.close()
    print(f"Seeder finished: {inserted} supplier(s) inserted, {total} in the directory.")


if __name__ == "__main__":
    main()
