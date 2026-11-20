from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import get_session
from ..models import SKU, StockEntry, StockExit
from ..schemas import InventoryOrderRead, SKUCreate, SKURead, StockEntryCreate, StockExitCreate

router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)])


def current_stock(session: Session, sku_id: int, warehouse: str) -> int:
    """Calcula el stock neto de un SKU en un almacén específico."""
    entries = session.exec(
        select(func.coalesce(func.sum(StockEntry.quantity), 0)).where(
            StockEntry.sku_id == sku_id, StockEntry.warehouse == warehouse
        )
    ).one()
    exits = session.exec(
        select(func.coalesce(func.sum(StockExit.quantity), 0)).where(
            StockExit.sku_id == sku_id, StockExit.warehouse == warehouse
        )
    ).one()
    return int(entries - exits)


def sku_read(session: Session, sku: SKU) -> SKURead:
    """Crea la respuesta SKU con stock calculado para su almacén."""
    return SKURead.model_validate(
        {**sku.model_dump(), "current_stock": current_stock(session, sku.id, sku.warehouse)}
    )


def _sku_or_404(session: Session, sku_id: int, *, lock: bool = False) -> SKU:
    """Busca un SKU y opcionalmente bloquea su fila durante una salida."""
    statement = select(SKU).where(SKU.id == sku_id)
    if lock:
        statement = statement.with_for_update()
    sku = session.exec(statement).first()
    if sku is None:
        raise HTTPException(status_code=404, detail=f"SKU {sku_id} was not found.")
    return sku


def _order_read(session: Session, movement: StockEntry | StockExit, order_type: str) -> dict[str, Any]:
    """Serializa un movimiento con datos del SKU y stock actual."""
    sku = _sku_or_404(session, movement.sku_id)
    return {
        "id": movement.id,
        "sku_id": movement.sku_id,
        "quantity": movement.quantity,
        "order_type": order_type,
        "reference": movement.reference if isinstance(movement, StockEntry) else None,
        "exit_type": movement.exit_type if isinstance(movement, StockExit) else None,
        "tracking_number": movement.tracking_number if isinstance(movement, StockExit) else None,
        "warehouse": movement.warehouse,
        "created_at": movement.created_at,
        "user_uuid": movement.user_uuid,
        "sku": sku_read(session, sku),
    }


@router.get("/products", response_model=list[SKURead])
def list_products(session: Session = Depends(get_session)) -> list[SKURead]:
    """Lista todos los SKU con stock por almacén."""
    products = session.exec(select(SKU).order_by(SKU.name, SKU.warehouse)).all()
    return [sku_read(session, product) for product in products]


@router.post("/products", response_model=SKURead, status_code=status.HTTP_201_CREATED)
def create_product(payload: SKUCreate, session: Session = Depends(get_session)) -> SKURead:
    """Registra un SKU y devuelve su stock calculado inicial."""
    product = SKU.model_validate(payload)
    session.add(product)
    session.commit()
    session.refresh(product)
    return sku_read(session, product)


@router.get("/products/{sku_id}", response_model=SKURead)
def get_product(sku_id: int, session: Session = Depends(get_session)) -> SKURead:
    """Devuelve un SKU con stock calculado para su almacén."""
    return sku_read(session, _sku_or_404(session, sku_id))


@router.post("/orders/inbound", response_model=InventoryOrderRead, status_code=status.HTTP_201_CREATED)
def create_stock_entry(
    payload: StockEntryCreate,
    session: Session = Depends(get_session),
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Registra una recepción de inventario asociada al usuario autenticado."""
    _sku_or_404(session, payload.sku_id)
    movement = StockEntry(**payload.model_dump(), user_uuid=user["user_uuid"])
    session.add(movement)
    session.commit()
    session.refresh(movement)
    return _order_read(session, movement, "inbound")


@router.post("/orders/outbound", response_model=InventoryOrderRead, status_code=status.HTTP_201_CREATED)
def create_stock_exit(
    payload: StockExitCreate,
    session: Session = Depends(get_session),
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Registra una salida tras validar stock y tipo de movimiento."""
    sku = _sku_or_404(session, payload.sku_id, lock=True)
    available = current_stock(session, sku.id, payload.warehouse)
    if payload.quantity > available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock for SKU '{sku.sku}'. Available: {available}, requested: {payload.quantity}.",
        )
    movement = StockExit(**payload.model_dump(), user_uuid=user["user_uuid"])
    session.add(movement)
    session.commit()
    session.refresh(movement)
    return _order_read(session, movement, "outbound")


@router.get("/orders", response_model=list[InventoryOrderRead])
def list_orders(session: Session = Depends(get_session)) -> list[InventoryOrderRead]:
    """Lista entradas y salidas ordenadas por fecha de creación."""
    entries = session.exec(select(StockEntry).order_by(StockEntry.created_at.desc())).all()
    exits = session.exec(select(StockExit).order_by(StockExit.created_at.desc())).all()
    movements = [
        _order_read(session, movement, order_type)
        for movement, order_type in [*((entry, "inbound") for entry in entries), *((exit_, "outbound") for exit_ in exits)]
    ]
    movements.sort(key=lambda movement: movement["created_at"], reverse=True)
    return movements