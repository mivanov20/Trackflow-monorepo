import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .accounts import AccountStoreError
from .auth import ACCOUNT_SERVICE_UNAVAILABLE, router as auth_router
from .database import SERVICE_UNAVAILABLE_MESSAGE, create_db_and_tables, ensure_user_uid_columns
from .routers.incidents import router as incidents_router
from .routers.inventory import router as inventory_router
from .routers.profiles import router as profiles_router
from .routers.users import router as users_router
from .security import validate_security_config

logger = logging.getLogger("services")

GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment."


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Valida la configuración de seguridad y prepara la base de datos al iniciar el servicio."""
    validate_security_config()
    create_db_and_tables()
    ensure_user_uid_columns()
    yield


app = FastAPI(title="TrackFlow Inventory API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(AccountStoreError)
async def account_store_error_handler(_: Request, __: AccountStoreError) -> JSONResponse:
    """Un archivo de cuentas ilegible devuelve 503 genérico; el detalle ya quedó en el log."""
    return JSONResponse(status_code=503, content={"detail": ACCOUNT_SERVICE_UNAVAILABLE})


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
    """Responde 503 genérico ante fallos de base de datos sin exponer detalles internos."""
    logger.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=503, content={"detail": SERVICE_UNAVAILABLE_MESSAGE})


@app.middleware("http")
async def unhandled_error_middleware(request: Request, call_next):
    """Convierte cualquier excepción no controlada en un 500 JSON genérico.

    Se registra antes que CORS para que la respuesta de error conserve las cabeceras CORS
    y el navegador pueda leer el mensaje en lugar de reportar un fallo de red.
    """
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": GENERIC_ERROR_MESSAGE})


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(inventory_router)
app.include_router(incidents_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Expone una señal básica de disponibilidad del servicio."""
    return {"status": "ok"}
