from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

Warehouse = Literal["LA", "ZGZ"]
Category = Literal["fashion", "electronics", "cosmetics"]
ExitType = Literal["dispatch", "loss"]


class SKUCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=100)
    client_name: str = Field(min_length=1, max_length=160)
    category: Category
    warehouse: Warehouse


class SKURead(SKUCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    current_stock: int


class StockEntryCreate(BaseModel):
    sku_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, strict=True)
    reference: str = Field(min_length=1, max_length=120)
    warehouse: Warehouse


class StockExitCreate(BaseModel):
    sku_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, strict=True)
    exit_type: ExitType
    tracking_number: str | None = Field(default=None, max_length=120)
    warehouse: Warehouse

    @model_validator(mode="after")
    def validate_tracking_number(self) -> "StockExitCreate":
        """Exige tracking para despachos y lo prohíbe en pérdidas."""
        if self.exit_type == "dispatch" and not self.tracking_number:
            raise ValueError("tracking_number is required for dispatch exits")
        if self.exit_type == "loss" and self.tracking_number is not None:
            raise ValueError("tracking_number must be null for loss exits")
        return self


class InventoryOrderRead(BaseModel):
    id: int
    sku_id: int
    quantity: int
    order_type: Literal["inbound", "outbound"]
    reference: str | None = None
    exit_type: ExitType | None = None
    tracking_number: str | None = None
    warehouse: Warehouse
    created_at: datetime
    user_uid: str
    sku: SKURead

class IncidentRead(BaseModel):
    """API representation of an incident (timestamps are UTC, ISO 8601 with a Z suffix)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    category: str
    status: str
    origin: str
    branch: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _as_utc(self, value: datetime) -> str:
        if value.tzinfo is None:  # SQLite may return naive values; they are stored as UTC
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
