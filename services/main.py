import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import _jwt_secret, router as auth_router
from .database import create_db_and_tables
from .routers.incidents import router as incidents_router
from .routers.inventory import router as inventory_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Valida la clave y prepara la base de datos al iniciar el servicio."""
    _jwt_secret()
    create_db_and_tables()
    yield


app = FastAPI(title="TrackFlow Inventory API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(inventory_router)
app.include_router(incidents_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Expone una señal básica de disponibilidad del servicio."""
    return {"status": "ok"}