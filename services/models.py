from datetime import datetime, timezone

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel
from trackflow_shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES


def utc_now() -> datetime:
    """Devuelve la hora UTC actual para registrar movimientos."""
    return datetime.now(timezone.utc)


class SKU(SQLModel, table=True):
    __tablename__ = "sku"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sku: str = Field(index=True)
    client_name: str = Field(index=True)
    category: str = Field(index=True)
    warehouse: str = Field(index=True)


class StockEntry(SQLModel, table=True):
    __tablename__ = "stockentry"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    reference: str
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)


class StockExit(SQLModel, table=True):
    __tablename__ = "stockexit"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    exit_type: str
    tracking_number: str | None = None
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)

def _allowed_values(column: str, values) -> CheckConstraint:
    """Build a CHECK constraint restricting `column` to `values`."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"ck_incident_{column}")


class Incident(SQLModel, table=True):
    """Operational incident; status, origin, category and branch are constrained in the database."""

    __tablename__ = "incident"
    __table_args__ = (
        _allowed_values("status", STATUSES),
        _allowed_values("origin", ORIGINS),
        _allowed_values("category", CATEGORIES),
        _allowed_values("branch", tuple(BRANCHES)),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=120)
    description: str
    category: str = Field(index=True)
    status: str = Field(index=True)
    origin: str = Field(index=True)
    branch: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class IncidentSeedLedger(SQLModel, table=True):
    """CSV ids already imported by `scripts/seed_incidents.py`.

    The analyzer's `incident_id` is not part of the incident model, so this ledger is
    what makes the seed script idempotent.
    """

    __tablename__ = "incident_seed_ledger"

    source_id: str = Field(primary_key=True)
