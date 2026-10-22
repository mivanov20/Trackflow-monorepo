"""TinyDB persistence for suppliers."""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from tinydb import TinyDB

from .models import Supplier, SupplierCreate, SupplierStatus

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = SERVICE_ROOT / "data" / "suppliers.json"


def get_db_path() -> Path:
    """Return the database file path, overridable with SUPPLIERS_DB_PATH."""
    return Path(os.environ.get("SUPPLIERS_DB_PATH", DEFAULT_DB_PATH))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SupplierRepository:
    """Thin wrapper around a TinyDB file; the lock keeps threaded requests safe."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(path, indent=2)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _to_model(doc) -> Supplier:
        return Supplier(id=doc.doc_id, **doc)

    def create(self, data: SupplierCreate) -> Supplier:
        """Insert a supplier and stamp `updated_at`."""
        record = data.model_dump(mode="json")
        record["updated_at"] = _now().isoformat()
        with self._lock:
            doc_id = self._db.insert(record)
            return self._to_model(self._db.get(doc_id=doc_id))

    def list(self, country: str | None = None, category: str | None = None) -> list[Supplier]:
        """Return suppliers, optionally filtered by country and/or category (case-insensitive)."""
        with self._lock:
            suppliers = [self._to_model(doc) for doc in self._db.all()]
        if country:
            suppliers = [s for s in suppliers if s.country.casefold() == country.strip().casefold()]
        if category:
            wanted = category.strip().casefold()
            suppliers = [s for s in suppliers if any(c.value.casefold() == wanted for c in s.categories)]
        return suppliers

    def get(self, supplier_id: int) -> Supplier | None:
        with self._lock:
            doc = self._db.get(doc_id=supplier_id)
        return self._to_model(doc) if doc else None

    def update_rate(self, supplier_id: int, rate_per_shipment: float) -> Supplier | None:
        """Change the rate and record when it happened (rate traceability)."""
        return self._update(supplier_id, {"rate_per_shipment": rate_per_shipment}, touch=True)

    def update_status(self, supplier_id: int, status: SupplierStatus) -> Supplier | None:
        return self._update(supplier_id, {"status": status.value}, touch=False)

    def _update(self, supplier_id: int, fields: dict, touch: bool) -> Supplier | None:
        """Apply `fields`; `updated_at` (last rate update) only moves when `touch` is set."""
        with self._lock:
            if not self._db.contains(doc_id=supplier_id):
                return None
            if touch:
                fields = {**fields, "updated_at": _now().isoformat()}
            self._db.update(fields, doc_ids=[supplier_id])
            return self._to_model(self._db.get(doc_id=supplier_id))

    def delete(self, supplier_id: int) -> bool:
        with self._lock:
            if not self._db.contains(doc_id=supplier_id):
                return False
            self._db.remove(doc_ids=[supplier_id])
            return True

    def names(self) -> set[str]:
        """Lower-cased supplier names, used by the seeder to avoid duplicates."""
        with self._lock:
            return {doc["name"].casefold() for doc in self._db.all()}
