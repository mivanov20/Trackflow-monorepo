import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .accounts import AccountStoreError
from .auth import ACCOUNT_SERVICE_UNAVAILABLE, router as auth_router
from .database import create_db_and_tables, ensure_user_uid_columns
from .routers.incidents import router as incidents_router
from .routers.inventory import router as inventory_router
from .routers.profiles import router as profiles_router
from .routers.users import router as users_router
from .security import validate_security_config


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
