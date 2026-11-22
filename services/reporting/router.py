"""Endpoints of the Weekly Warehouse & Client Performance pipeline (design section 5).

They validate, authenticate and serialise. Every behaviour lives in `data/pipelines/`: reading the KPI table, reading the
run log and opening a run are functions imported from there, so nothing of the pipeline is duplicated in `services/`.
Starting a run does not run it here: the endpoint enqueues a Celery task and answers 202 with its id, and a worker, which is
a separate process, does the work (`GET /tasks/{task_id}` follows it).
This module is separate from `services/telemetry/`, which keeps serving engineering.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from data.pipelines import pipeline
from data.pipelines.weekly_performance import config, queries, runs

from ..accounts import User
from ..auth import get_current_user
from ..database import get_session
from ..tasks import run_weekly_performance_pipeline

router = APIRouter(prefix="/reporting", tags=["reporting"])

# Starting a run spends compute, so it needs one of the roles that run operations.
TRIGGER_ROLES = ("admin", "manager")


class WeeklyPerformanceEntry(BaseModel):
    warehouse: str
    client_id: str
    inbound_units_count: int
    outbound_orders_count: int
    stockout_events_count: int
    discrepancy_events_count: int
    discrepancy_rate: float


class WeeklyPerformanceResponse(BaseModel):
    """The contract of the company CONTEXT: one week and all its warehouse and client rows."""

    week_start: date
    entries: list[WeeklyPerformanceEntry]


class PipelineRunResponse(BaseModel):
    run_id: str
    pipeline_name: str
    trigger: str
    triggered_by: str | None
    status: str
    started_at: str
    finished_at: str | None
    window_start: str
    window_end: str
    source_max_timestamp: str | None
    records_extracted: int | None
    duplicates_dropped: int | None
    records_discarded: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_unchanged: int | None
    rows_deleted: int | None
    optional_steps_failed: int
    error_stage: str | None
    error_type: str | None
    error_message: str | None
    prefect_flow_run_id: str | None


class TriggerRequest(BaseModel):
    week_start: date | None = Field(default=None, description="Recompute this one week (a Monday). Omit for the last complete weeks.")
    weeks_back: int = Field(default=config.DEFAULT_WEEKS_BACK, ge=1, le=config.MAX_WEEKS_BACK)


class TriggerResponse(BaseModel):
    task_id: str
    run_id: str
    status: str = "pending"


@router.get("/weekly-warehouse-client-performance", response_model=WeeklyPerformanceResponse)
def weekly_warehouse_client_performance(
    week_start: date | None = Query(default=None, description="A Monday, YYYY-MM-DD. Default: the most recent computed week."),
    session: Session = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> dict:
    """KPI rows (Inbound Volume, Outbound Throughput, Stockout Frequency, Discrepancy Rate) per warehouse and client."""
    if week_start is not None and week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start must be a Monday.")
    result = queries.get_weekly_performance(session.get_bind(), week_start)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No week has been computed yet.")
    return result


@router.get("/pipeline-runs/latest", response_model=PipelineRunResponse)
def latest_pipeline_run(session: Session = Depends(get_session), _user: User = Depends(get_current_user)) -> dict:
    """Status and metadata of the most recent run of the pipeline."""
    run = runs.get_latest_run(session.get_bind())
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The pipeline has not run yet.")
    return run


@router.post("/pipeline-runs", status_code=status.HTTP_202_ACCEPTED, response_model=TriggerResponse)
def trigger_pipeline_run(
    body: TriggerRequest | None = Body(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Enqueues a manual run and answers at once with the `task_id` of the Celery task and the `run_id` of the pipeline run.

    Follow the task with `GET /tasks/{task_id}` and the pipeline with `GET /reporting/pipeline-runs/latest`.
    """
    if user.role not in TRIGGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can start the pipeline.")
    options = body or TriggerRequest()
    engine = session.get_bind()
    try:
        run_id = pipeline.open_manual_run(engine, user.id, options.week_start, options.weeks_back)
    except runs.RunAlreadyRunning as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A run is already in progress.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        # The message holds identifiers and two small parameters: the worker reads the data itself.
        task = run_weekly_performance_pipeline.delay(run_id, options.week_start.isoformat() if options.week_start else None, options.weeks_back, user.id)
    except Exception as error:  # noqa: BLE001 - the broker is down: nobody will run this, so the run must not stay `running`
        runs.fail_run_if_running(engine, uuid.UUID(run_id), error)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The task queue is temporarily unavailable.") from error
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"task_id": task.id, "run_id": run_id, "status": "pending"})
