import logging
import os
from collections.abc import Generator

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("services.database")

SERVICE_UNAVAILABLE_MESSAGE = "The service is temporarily unavailable. Please try again in a moment."
CONFLICT_MESSAGE = "This change conflicts with existing data. Refresh the page and try again."

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must point to the TrackFlow Supabase PostgreSQL database.")

engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_options)


def create_db_and_tables() -> None:
    """Crea las tablas de inventario definidas por SQLModel."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Proporciona una sesión de base de datos por solicitud."""
    with Session(engine) as session:
        yield session


def commit_session(session: Session) -> None:
    """Confirma la transacción; ante un fallo hace rollback y lanza un error HTTP limpio.

    El detalle real (que puede incluir host o usuario de la base) solo se registra en el servidor.
    """
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        logger.warning("Integrity error while committing: %s", type(error).__name__)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CONFLICT_MESSAGE) from error
    except SQLAlchemyError as error:
        session.rollback()
        logger.exception("Database error while committing")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE) from error