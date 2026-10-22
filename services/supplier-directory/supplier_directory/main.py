"""FastAPI application for the TrackFlow supplier directory."""

from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    Supplier,
    SupplierCategory,
    SupplierCreate,
    SupplierRateUpdate,
    SupplierStatusUpdate,
)
from .repository import SupplierRepository, get_db_path
from .seed import seed_repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the database once and load the initial suppliers if it is empty."""
    repo = SupplierRepository(get_db_path())
    seed_repository(repo)
    app.state.repository = repo
    yield
    repo.close()


app = FastAPI(
    title="TrackFlow Supplier Directory",
    description="Single source of truth for TrackFlow procurement suppliers.",
    version="0.1.0",
    lifespan=lifespan,
)

# The backoffice is a static site served from another local port.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_repository() -> Iterator[SupplierRepository]:
    yield app.state.repository


def _get_or_404(repo: SupplierRepository, supplier_id: int) -> Supplier:
    supplier = repo.get(supplier_id)
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Supplier {supplier_id} not found")
    return supplier


@app.get("/categories", response_model=list[str])
def list_categories() -> list[str]:
    """Valid product categories, so clients do not hardcode them."""
    return [category.value for category in SupplierCategory]


@app.post("/suppliers", response_model=Supplier, status_code=status.HTTP_201_CREATED)
def create_supplier(data: SupplierCreate, repo: SupplierRepository = Depends(get_repository)):
    return repo.create(data)


@app.get("/suppliers", response_model=list[Supplier])
def list_suppliers(
    country: str | None = Query(default=None, description="Filter by country"),
    category: str | None = Query(default=None, description="Filter by product category"),
    repo: SupplierRepository = Depends(get_repository),
):
    return repo.list(country=country, category=category)


@app.get("/suppliers/{supplier_id}", response_model=Supplier)
def get_supplier(supplier_id: int, repo: SupplierRepository = Depends(get_repository)):
    return _get_or_404(repo, supplier_id)


@app.patch("/suppliers/{supplier_id}/rate", response_model=Supplier)
def update_rate(
    supplier_id: int,
    body: SupplierRateUpdate,
    repo: SupplierRepository = Depends(get_repository),
):
    _get_or_404(repo, supplier_id)
    return repo.update_rate(supplier_id, body.rate_per_shipment)


@app.patch("/suppliers/{supplier_id}/status", response_model=Supplier)
def update_status(
    supplier_id: int,
    body: SupplierStatusUpdate,
    repo: SupplierRepository = Depends(get_repository),
):
    _get_or_404(repo, supplier_id)
    return repo.update_status(supplier_id, body.status)


@app.delete("/suppliers/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: int, repo: SupplierRepository = Depends(get_repository)):
    if not repo.delete(supplier_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Supplier {supplier_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def serve() -> None:
    """Run the API with uvicorn (`uv run serve`)."""
    import uvicorn

    uvicorn.run("supplier_directory.main:app", host="127.0.0.1", port=8000)
