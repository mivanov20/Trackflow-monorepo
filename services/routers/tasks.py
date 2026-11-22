"""`GET /tasks/{task_id}`: where an enqueued task is, and its result when it has one.

The status comes from the Celery result backend (Redis), not from the worker: the API and the worker never talk to each
other, they only share the broker. Celery cannot tell an unknown id from a task that has not started, so both read `pending`.
"""

from typing import Any, Literal

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from ..accounts import User
from ..auth import get_current_user
from ..celery_app import app as celery_app
from ..error_text import describe_error

router = APIRouter(prefix="/tasks", tags=["tasks"])

# The Celery states, in lower case. RETRY is the wait between attempts after a failure: it is not final.
STATUS_BY_STATE = {
    "PENDING": "pending",
    "RECEIVED": "pending",
    "STARTED": "started",
    "RETRY": "retry",
    "SUCCESS": "success",
    "FAILURE": "failure",
    "REVOKED": "failure",
}
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class TaskStatus(BaseModel):
    task_id: str
    status: Literal["pending", "started", "retry", "success", "failure"]
    result: Any | None = None


@router.get("/{task_id}", response_model=TaskStatus)
def get_task_status(task_id: str = Path(pattern=UUID_PATTERN), _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The status of a task (`pending`, `started`, `retry`, `success`, `failure`) and, once it has one, its result.

    A successful task returns what it returned; a failed or retrying one returns `{"error": "..."}`.
    """
    try:
        outcome = AsyncResult(task_id, app=celery_app)
        state = outcome.state
        value = outcome.result if state in ("SUCCESS", "FAILURE", "RETRY", "REVOKED") else None
    except Exception as error:  # noqa: BLE001 - the broker or the backend is unreachable
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The task queue is temporarily unavailable.") from error
    if isinstance(value, BaseException):
        value = {"error": describe_error(value)}
    return {"task_id": task_id, "status": STATUS_BY_STATE.get(state, "pending"), "result": value}
