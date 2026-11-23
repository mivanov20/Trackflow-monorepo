"""Centralized incident manager endpoints (`/api/incidents`)."""

import logging
from typing import Any, Callable, Coroutine

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from trackflow_shared.incidents import (
    BRANCHES,
    CATEGORIES,
    ORIGINS,
    STATUSES,
    FieldError,
    validate_filter,
    validate_incident_payload,
    validate_status_value,
    validate_transition,
)

from ..auth import get_current_user, require_staff
from ..database import SERVICE_UNAVAILABLE_MESSAGE, commit_session, get_session
from ..models import Incident, utc_now
from ..schemas import IncidentRead

logger = logging.getLogger("services.incidents")

GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment."


def error_response(
    status_code: int,
    error: str,
    message: str,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the structured JSON error used by every incident endpoint."""
    body: dict[str, Any] = {"error": error, "message": message}
    if errors:
        body["errors"] = [e.to_dict() for e in errors]
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def validation_failed(errors: list[FieldError]) -> JSONResponse:
    """400 response naming each problematic field in plain language."""
    return error_response(400, "validation_error", "Some fields need your attention.", errors)


class IncidentRoute(APIRoute):
    """Route class giving incident endpoints their own error format.

    The rest of the service keeps FastAPI's default errors, which existing inventory
    clients and tests rely on, so the structured 400/404/500 bodies are scoped to this router.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                errors = []
                for issue in exc.errors():
                    field = ".".join(str(part) for part in issue["loc"][1:])
                    if not field or field.isdigit():  # malformed JSON reports a character offset
                        errors.append(FieldError("body", "The request body is not valid JSON."))
                    else:
                        errors.append(FieldError(field, "This value is not valid."))
                return validation_failed(errors)
            except StarletteHTTPException as exc:
                code = {404: "not_found", 409: "conflict", 503: "service_unavailable"}.get(exc.status_code, "http_error")
                return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)
            except SQLAlchemyError:
                # Fallo de lectura en la base: 503 genérico, el detalle queda solo en el log.
                logger.exception("Database error on %s %s", request.method, request.url.path)
                return error_response(503, "service_unavailable", SERVICE_UNAVAILABLE_MESSAGE)
            except Exception:
                # Log the real error server-side; never send a stack trace to the client.
                logger.exception("Unhandled error on %s %s", request.method, request.url.path)
                return error_response(500, "internal_error", GENERIC_ERROR_MESSAGE)

        return handler


router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_staff)],
    route_class=IncidentRoute,
)


def _not_found(incident_id: int) -> JSONResponse:
    return error_response(404, "not_found", f"Incident {incident_id} was not found.")


@router.post("", status_code=201, response_model=IncidentRead)
def create_incident(payload: Any = Body(default=None), session: Session = Depends(get_session)):
    """Register an incident; invalid or missing fields return 400 naming each field."""
    clean, errors = validate_incident_payload(payload)
    if errors:
        return validation_failed(errors)
    incident = Incident(**clean)
    session.add(incident)
    commit_session(session)
    session.refresh(incident)
    return incident


@router.get("", response_model=list[IncidentRead])
def list_incidents(
    status: str | None = Query(default=None),
    origin: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    category: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """List incidents (newest first), optionally filtered; an empty table returns []."""
    filters = {"status": status, "origin": origin, "branch": branch, "category": category}
    errors = [e for field, value in filters.items() if (e := validate_filter(field, value))]
    if errors:
        return validation_failed(errors)
    statement = select(Incident)
    for field, value in filters.items():
        if value is not None:
            statement = statement.where(getattr(Incident, field) == value)
    return session.exec(statement.order_by(Incident.created_at.desc(), Incident.id.desc())).all()


def _counts(session: Session, column, keys) -> dict[str, int]:
    """Count incidents per `column`, listing every allowed key (zero when absent)."""
    counts = dict.fromkeys(keys, 0)
    for value, total in session.exec(select(column, func.count()).group_by(column)).all():
        counts[value] = total
    return counts


@router.get("/summary")
def incident_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Aggregated totals by status, category, origin and branch (all zero when empty)."""
    return {
        "total": session.exec(select(func.count()).select_from(Incident)).one(),
        "by_status": _counts(session, Incident.status, STATUSES),
        "by_category": _counts(session, Incident.category, CATEGORIES),
        "by_origin": _counts(session, Incident.origin, ORIGINS),
        "by_branch": _counts(session, Incident.branch, tuple(BRANCHES)),
    }


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    return incident or _not_found(incident_id)


@router.patch("/{incident_id}/status", response_model=IncidentRead)
def update_status(incident_id: int, payload: Any = Body(default=None), session: Session = Depends(get_session)):
    """Move an incident along its lifecycle; invalid values or transitions return 400."""
    incident = session.get(Incident, incident_id)
    if incident is None:
        return _not_found(incident_id)
    new_status = payload.get("status") if isinstance(payload, dict) else None
    error = validate_status_value(new_status) or validate_transition(incident.status, new_status)
    if error:
        return validation_failed([error])
    incident.status = new_status
    incident.updated_at = utc_now()
    session.add(incident)
    commit_session(session)
    session.refresh(incident)
    return incident
