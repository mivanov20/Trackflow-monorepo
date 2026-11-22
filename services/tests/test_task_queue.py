"""The task queue: Celery tasks, retries with backoff, the dead letter queue, task status and the 202 endpoint.

The tests use an in-memory broker and an embedded worker (Celery's own test worker, in a thread of the test process),
so they need no Redis. The real Redis and a separate worker process are checked by hand and described in the PR.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml
from celery.contrib.testing.worker import start_worker
from celery.result import AsyncResult
from prefect.testing.utilities import prefect_test_harness
from reporting_fixtures import EXPECTED, WEEKS, kpis, seed_events, table_rows
from sqlalchemy import select
from sqlmodel import SQLModel

from data.pipelines.weekly_performance import config
from services import tasks
from services.accounts import Role, User
from services.auth import get_current_user
from services.celery_app import app as celery_app
from services.main import app as api
from services.models import DeadLetterTask
from services.tasks import DeadLetterOnFailure

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTEMPTS: dict[str, list[float]] = {}


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def memory_broker(monkeypatch):
    """Broker and result backend in memory, for the API's `.delay` and for the embedded worker."""
    monkeypatch.setattr(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    celery_app.conf.broker_transport_options = {}
    celery_app.conf.result_backend_transport_options = {}
    celery_app._pool = None  # a fresh producer pool for the memory broker
    from kombu.transport.memory import Channel

    Channel.queues.clear()  # the memory broker lives as long as the process: one test's unconsumed messages must not reach the next
    yield
    Channel.queues.clear()


@pytest.fixture
def dead_letters(incident_engine, monkeypatch):
    """The database the worker records dead letters in."""
    monkeypatch.setattr(tasks, "_engine", lambda: incident_engine)
    return incident_engine


def rows(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(DeadLetterTask.__table__).order_by(DeadLetterTask.__table__.c.failed_at)).mappings()]


# Test tasks on the same app and the same base class as the real one, with a backoff of a fraction of a second.
@celery_app.task(bind=True, base=DeadLetterOnFailure, name="test.always_fails", retry_backoff=0.3, retry_jitter=False, max_retries=3)
def always_fails(self, reference: str) -> None:
    ATTEMPTS.setdefault(reference, []).append(time.monotonic())
    raise ValueError(f"the reference {reference} cannot be processed")


@celery_app.task(bind=True, base=DeadLetterOnFailure, name="test.fails_twice", retry_backoff=0.2, retry_jitter=False, max_retries=3)
def fails_twice(self, reference: str) -> dict:
    attempts = ATTEMPTS.setdefault(reference, [])
    attempts.append(time.monotonic())
    if len(attempts) < 3:
        raise ConnectionError("the service was unreachable")
    return {"reference": reference, "attempts": len(attempts)}


@celery_app.task(bind=True, base=DeadLetterOnFailure, name="test.slow", max_retries=3)
def slow(self, reference: str, seconds: float = 1.0) -> dict:
    time.sleep(seconds)
    return {"reference": reference}


@pytest.fixture
def worker():
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING", shutdown_timeout=30):
        yield


def wait_for(predicate, timeout=30.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out waiting for the condition")


def status_of(client, task_id):
    return client.get(f"/tasks/{task_id}").json()


# --- retries with backoff and the dead letter queue -----------------------------------------------------------------------


def test_a_task_that_keeps_failing_is_retried_with_growing_waits_then_recorded_in_the_dead_letter_queue(worker, dead_letters):
    result = always_fails.delay("ref-dlq")
    wait_for(lambda: rows(dead_letters))
    attempts = ATTEMPTS["ref-dlq"]
    assert len(attempts) == 4  # the first try and max_retries=3 retries
    gaps = [later - earlier for earlier, later in zip(attempts, attempts[1:])]
    assert gaps[0] >= 0.25 and gaps[1] >= 0.5 and gaps[2] >= 1.1, f"no immediate retries, and the wait doubles: {gaps}"
    assert gaps[1] > gaps[0] * 1.5 and gaps[2] > gaps[1] * 1.5

    (dead,) = rows(dead_letters)  # recorded once, not once per attempt
    assert dead["task_id"] == result.id and dead["task_name"] == "test.always_fails" and dead["attempt"] == 4
    assert "the reference ref-dlq cannot be processed" in dead["error_message"] and dead["error_message"].startswith("ValueError")
    assert isinstance(dead["failed_at"], datetime)
    assert dead["payload"] == {"args": ["ref-dlq"], "kwargs": {}}  # identifiers only


def test_a_task_that_recovers_within_its_retries_is_not_a_dead_letter(worker, dead_letters):
    result = fails_twice.delay("ref-recovers")
    assert wait_for(lambda: result.state == "SUCCESS" or None) and result.result == {"reference": "ref-recovers", "attempts": 3}
    assert rows(dead_letters) == []


def test_the_pipeline_task_is_configured_with_three_retries_backoff_timeouts_and_the_dead_letter_base():
    task = tasks.run_weekly_performance_pipeline
    assert isinstance(task, DeadLetterOnFailure) and task.max_retries == 3
    assert task.retry_backoff == tasks.RETRY_BACKOFF_SECONDS == 5 and task.retry_jitter is False and Exception in task.autoretry_for
    assert task.soft_time_limit and task.time_limit and task.soft_time_limit < task.time_limit  # every task has a timeout
    assert celery_app.conf.task_acks_late and celery_app.conf.task_reject_on_worker_lost and celery_app.conf.task_track_started
    assert celery_app.conf.task_serializer == "json" and celery_app.conf.worker_send_task_events and celery_app.conf.task_send_sent_event


# --- GET /tasks/{task_id} ---------------------------------------------------------------------------------------------------


def test_the_status_follows_the_task_through_pending_started_and_success(incident_client, worker):
    result = slow.delay("ref-lifecycle", 1.5)
    seen = []
    wait_for(lambda: seen.append(status_of(incident_client, result.id)["status"]) or seen[-1] == "success")
    assert seen[0] in ("pending", "started") and "started" in seen and seen[-1] == "success"
    assert seen == sorted(seen, key=["pending", "started", "success"].index)  # it only moves forward
    assert status_of(incident_client, result.id) == {"task_id": result.id, "status": "success", "result": {"reference": "ref-lifecycle"}}


def test_a_task_nobody_has_picked_up_reads_pending(incident_client):
    result = slow.delay("ref-waiting", 0.1)  # no worker is running
    assert status_of(incident_client, result.id) == {"task_id": result.id, "status": "pending", "result": None}


def test_a_failing_task_reads_retry_while_it_waits_and_failure_at_the_end_with_the_error(incident_client, worker, dead_letters):
    result = always_fails.delay("ref-status")
    seen = set()
    wait_for(lambda: seen.add(status_of(incident_client, result.id)["status"]) or "failure" in seen)
    assert "retry" in seen and "failure" in seen  # between the attempts it is retrying, not failed
    body = status_of(incident_client, result.id)
    assert body["status"] == "failure" and "ValueError" in body["result"]["error"] and "ref-status" in body["result"]["error"]


def test_the_status_endpoint_needs_a_user_a_task_id_that_is_a_uuid_and_a_reachable_backend(incident_client, monkeypatch):
    assert incident_client.get("/tasks/not-an-id").status_code == 422
    assert incident_client.get("/tasks/00000000-0000-4000-8000-000000000000").json()["status"] == "pending"  # Celery cannot tell unknown from waiting

    def broken(*_a, **_k):
        raise ConnectionError("redis is down")

    from services.routers import tasks as router

    monkeypatch.setattr(router, "AsyncResult", broken)
    assert incident_client.get("/tasks/00000000-0000-4000-8000-000000000000").status_code == 503
    incident_client.headers.pop("Authorization")
    assert incident_client.get("/tasks/00000000-0000-4000-8000-000000000000").status_code == 401


# --- the producer: the 202 endpoint -------------------------------------------------------------------------------------------


@pytest.fixture
def manager(incident_client, incident_engine):
    from data.pipelines.weekly_performance.schema import ensure_reporting

    ensure_reporting(incident_engine)
    me = incident_client.get("/auth/me").json()
    user = User(id=me["id"], email=me["email"], hashed_password="x", role=Role.manager, created_at=datetime.now(timezone.utc))
    api.dependency_overrides[get_current_user] = lambda: user
    with config.use_engine(incident_engine):
        yield incident_client
    api.dependency_overrides.pop(get_current_user, None)


def clear_runs():
    from data.pipelines.weekly_performance import schema
    from sqlalchemy import delete

    with config.reporting_engine().begin() as connection:
        connection.execute(delete(schema.pipeline_runs))


def test_the_endpoint_answers_202_with_a_task_id_in_under_200_ms_without_waiting_for_the_work(manager):
    assert manager.post("/reporting/pipeline-runs", json={}).status_code == 202  # warms up the app (imports, pools)
    clear_runs()
    started = time.perf_counter()
    response = manager.post("/reporting/pipeline-runs", json={"weeks_back": 2})
    elapsed = time.perf_counter() - started
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"task_id", "run_id", "status"} and body["status"] == "pending"
    assert elapsed < 0.2, f"answered in {elapsed * 1000:.0f} ms"  # no worker is running: it did not wait for the task
    assert AsyncResult(body["task_id"], app=celery_app).state == "PENDING"


def test_the_message_in_the_queue_carries_identifiers_and_small_parameters_never_data(manager, monkeypatch):
    sent = []
    real_delay = tasks.run_weekly_performance_pipeline.delay
    monkeypatch.setattr(tasks.run_weekly_performance_pipeline, "delay", lambda *args, **kwargs: (sent.append((args, kwargs)), real_delay(*args, **kwargs))[1])
    clear_runs()
    manager.post("/reporting/pipeline-runs", json={"week_start": "2026-11-16", "weeks_back": 1})
    ((args, kwargs),) = sent
    run_id, week_start, weeks_back, triggered_by = args
    assert kwargs == {} and week_start == "2026-11-16" and weeks_back == 1
    assert all(isinstance(value, (str, int, type(None))) for value in args)  # no frames, no files, no blobs
    message = celery_app.amqp.as_task_v2("00000000-0000-4000-8000-000000000000", tasks.RUN_WEEKLY_PERFORMANCE_PIPELINE, args=args, kwargs=kwargs)
    assert len(json.dumps(message.body, default=lambda b: b.decode() if isinstance(b, bytes) else str(b))) < 600  # a few hundred bytes


def test_if_the_broker_is_down_the_endpoint_answers_503_and_the_run_is_not_left_running(manager, monkeypatch):
    from data.pipelines.weekly_performance import runs

    clear_runs()

    def refuse(*_a, **_k):
        raise ConnectionError("Error 111 connecting to redis://localhost:6379. Connection refused.")

    monkeypatch.setattr(tasks.run_weekly_performance_pipeline, "delay", refuse)
    assert manager.post("/reporting/pipeline-runs", json={}).status_code == 503
    latest = runs.get_latest_run(config.reporting_engine())
    assert latest["status"] == "failed" and "redis://" not in (latest["error_message"] or "")
    assert manager.post("/reporting/pipeline-runs", json={}).status_code == 503  # not blocked by a zombie run


def test_the_real_task_runs_the_pipeline_in_the_worker_and_the_result_is_readable_from_the_api(manager, incident_engine, worker, monkeypatch, tmp_path):
    from services import tasks as task_module
    from data.pipelines.weekly_performance import schema, transform
    from sqlalchemy import delete

    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(transform, "TRANSFORM_VERSION", f"queue-{time.time_ns()}")
    from data.pipelines import pipeline

    monkeypatch.setattr(pipeline.window, "weeks_for_run", lambda week_start=None, weeks_back=2, now=None: [week_start] if week_start else WEEKS)
    seed_events(incident_engine)
    with incident_engine.begin() as connection:
        connection.execute(delete(schema.pipeline_runs))

    response = manager.post("/reporting/pipeline-runs", json={"weeks_back": 2})
    assert response.status_code == 202
    task_id, run_id = response.json()["task_id"], response.json()["run_id"]
    body = wait_for(lambda: (b := status_of(manager, task_id))["status"] in ("success", "failure") and b, timeout=120)
    assert body["status"] == "success", body
    assert body["result"]["run_id"] == run_id and body["result"]["status"] == "completed" and body["result"]["rows_inserted"] == 4
    assert kpis(table_rows(incident_engine)) == EXPECTED
    latest = manager.get("/reporting/pipeline-runs/latest").json()
    assert latest["run_id"] == run_id and latest["status"] == "completed"
    assert task_module.run_weekly_performance_pipeline.name == tasks.RUN_WEEKLY_PERFORMANCE_PIPELINE


# --- observability --------------------------------------------------------------------------------------------------------


def test_every_task_logs_id_attempt_status_and_duration_and_a_failure_logs_the_full_error(worker, dead_letters, caplog):
    with caplog.at_level(logging.INFO, logger="trackflow.tasks"):
        done = slow.delay("ref-log", 0.2)
        failed = always_fails.delay("ref-log-fail")
        wait_for(lambda: done.state == "SUCCESS" and rows(dead_letters) and caplog.text.count("status=dead_letter") >= 1)
    lines = [r.getMessage() for r in caplog.records if r.name == "trackflow.tasks"]
    success = [line for line in lines if f"task_id={done.id}" in line and "status=success" in line]
    assert success and "attempt=1" in success[0] and "duration_ms=" in success[0] and "task=test.slow" in success[0]
    assert any(f"task_id={done.id}" in line and "status=started" in line for line in lines)
    attempts = [line for line in lines if f"task_id={failed.id}" in line and ("status=retry" in line or "status=failure" in line)]
    assert [re.search(r"attempt=(\d)", line).group(1) for line in attempts] == ["1", "2", "3", "4"]
    final = attempts[-1]
    assert "status=failure" in final and "error=ValueError: the reference ref-log-fail cannot be processed" in final  # the full message
    assert any("status=dead_letter" in line and f"task_id={failed.id}" in line for line in lines)


# --- infrastructure and separation ------------------------------------------------------------------------------------------


def test_compose_runs_redis_with_noeviction_flower_on_5555_and_a_worker_in_its_own_service():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    redis, worker_service, flower, api_service = services["redis"], services["worker"], services["flower"], services["api"]
    assert redis["image"].startswith("redis:") and "--maxmemory-policy" in redis["command"] and redis["command"][redis["command"].index("--maxmemory-policy") + 1] == "noeviction"
    assert any(str(port).endswith(":6379") for port in redis["ports"])
    assert flower["image"].startswith("mher/flower") and any(str(port).endswith(":5555") for port in flower["ports"])
    assert worker_service["command"][:4] == ["celery", "-A", "services.celery_app", "worker"]  # a different process from uvicorn
    assert "uvicorn" not in " ".join(worker_service["command"]) and "celery" not in " ".join(api_service.get("command", []))
    for name, service in (("api", api_service), ("worker", worker_service)):
        assert "REDIS_URL" in service["environment"] and "redis://redis:6379" in service["environment"]["REDIS_URL"], name
    assert "REDIS_URL" in str(flower["environment"]) or "CELERY_BROKER_URL" in flower["environment"]
    assert worker_service["depends_on"]["redis"]["condition"] == "service_healthy" and worker_service["image"] == api_service["image"]
    assert "redis-data" in compose["volumes"]


def test_the_api_never_runs_a_worker_and_the_url_comes_from_the_environment():
    for path in (REPO_ROOT / "services").rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        code = path.read_text(encoding="utf-8")
        for forbidden in ("start_worker", "worker_main", "task_always_eager", "WorkController"):
            assert forbidden not in code, f"{path.name} runs a worker or an eager task inside the API"
    source = (REPO_ROOT / "services" / "celery_app.py").read_text(encoding="utf-8")
    assert 'os.getenv("REDIS_URL"' in source and "broker=REDIS_URL, backend=REDIS_URL" in source


def test_the_readme_documents_how_to_start_and_stop_the_worker():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Task queue and worker (Celery + Redis)")[1].split("- **Not included:**")[0]
    for needle in ("Start it", "Stop it", "celery -A services.celery_app worker", "docker compose stop worker", "Flower", "dead_letter_tasks", "REDIS_URL", "202"):
        assert needle in section, needle


def test_the_dead_letter_table_has_the_fields_the_ticket_asks_for_and_its_sql_matches():
    table = DeadLetterTask.__table__
    assert {"task_id", "attempt", "error_message", "failed_at"} <= {c.name for c in table.columns} and table.name == "dead_letter_tasks"
    sql = (REPO_ROOT / "services" / "sql" / "dead_letter_tasks.sql").read_text(encoding="utf-8")
    for needle in ("task_id       text not null", "attempt       integer not null", "error_message text not null", "failed_at     timestamptz not null default now()", "ix_dead_letter_tasks_task_id"):
        assert needle in sql
