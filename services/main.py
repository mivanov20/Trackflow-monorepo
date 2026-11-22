import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .accounts import AccountStoreError
from .auth import ACCOUNT_SERVICE_UNAVAILABLE, router as auth_router
from .database import SERVICE_UNAVAILABLE_MESSAGE, create_db_and_tables, engine, ensure_user_uid_columns
from .routers.incident_analysis import router as incident_analysis_router, validate_analysis_config
from .routers.incidents import router as incidents_router
from .routers.inventory import router as inventory_router
from .routers.agent import router as agent_router
from .routers.knowledge import router as knowledge_router
from .routers.rfp import router as rfp_router
from .routers.rfp_events import router as rfp_events_router
from .routers.chat import router as chat_router
from .chat import store as chat_store
from .events import bus as event_bus
from .routers.profiles import router as profiles_router
from .reporting.router import router as reporting_router
from .routers.tasks import router as tasks_router
from .routers.telemetry import router as telemetry_router, validate_telemetry_config
from .routers.users import router as users_router
from .agent import validate_agent
from .mailer import validate_email_config
from .security import validate_security_config
from data.pipelines.weekly_performance.schema import ensure_reporting
from data.pipelines.rfp_intake import notifications as rfp_notifications, proposal_store as rfp_proposal_store, store as rfp_store
from sqlmodel import Session

logger = logging.getLogger("services")

GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment."


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Valida la configuración de seguridad y prepara la base de datos al iniciar el servicio."""
    validate_security_config()
    rfp_notifications.set_publisher(event_bus.publish)  # the intake raises `rfp_ticket_created`; the SSE stream delivers it
    validate_email_config()
    validate_analysis_config()
    validate_telemetry_config()
    validate_agent()  # compiles the agent graph: a structural mistake stops the API here, not on a user's question
    create_db_and_tables()
    ensure_user_uid_columns()
    rfp_proposal_store.ensure_columns(engine)  # Part 2 columns on the Part 1 tables of an existing database
    try:
        ensure_reporting(engine)
    except Exception:  # a missing right on the `reporting` schema must not take the whole API down
        logger.exception("The reporting schema could not be prepared; the reporting endpoints will fail until it exists.")
    try:
        orphans = chat_store.interrupt_orphans(engine)  # a chat message left streaming by a previous process keeps the text it had and becomes `interrupted`
        if orphans:
            logger.warning("%d chat message(s) were still streaming when the service stopped; they are marked as interrupted.", orphans)
    except Exception:  # the chat tables are checked by the first request; a startup check must not take the API down
        logger.exception("Chat messages could not be checked at startup.")
    try:
        with Session(engine) as session:
            interrupted = rfp_store.interrupt_stale(session) + rfp_proposal_store.interrupt_stale(session)
        if interrupted:
            logger.warning("%d RFP ticket(s) were still analyzing when the service stopped; they are marked as failed so they can be reprocessed.", interrupted)
    except Exception:  # the intake tables are checked by the first request; a startup check must not take the API down
        logger.exception("RFP tickets could not be checked at startup.")
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
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        # 3001: backoffice; 3000: app de candidatos (el sitio público en 3000 no llama a la API)
        "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000,http://127.0.0.1:3000",
    ).split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"],  # Last-Event-ID: the dashboard's stream asks for what it missed
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(inventory_router)
app.include_router(incidents_router)
app.include_router(incident_analysis_router)
app.include_router(telemetry_router)
app.include_router(reporting_router)
app.include_router(tasks_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(rfp_router)
app.include_router(rfp_events_router)
app.include_router(chat_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Expone una señal básica de disponibilidad del servicio."""
    return {"status": "ok"}
