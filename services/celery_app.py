"""The Celery application: Redis as broker and result backend, and the logging of every task.

The API is the producer (it only calls `.delay(...)` and answers 202). A worker, which is a separate process started with

    celery -A services.celery_app worker --loglevel=INFO

is the consumer. Neither imports the other's work: the API never runs a task, the worker never serves HTTP.

Settings that matter and why:

* `task_acks_late` + `task_reject_on_worker_lost`: the message is acknowledged only after the task finishes. If the worker
  dies half way, Redis hands the message to another worker instead of losing it.
* `task_track_started`: without it a running task looks `PENDING`; with it the state is `STARTED`.
* Every task has a time limit (soft, then hard), so a task that never ends cannot block the pool.
* Messages carry identifiers only; the task fetches what it needs itself.
* `visibility_timeout` is longer than the longest task, so Redis does not redeliver a message that is still being worked on.
"""

import logging
import os
import time

from celery import Celery
from celery.signals import task_failure, task_prerun, task_retry, task_success

# One Redis for the API and for the workers. Inside Docker Compose it is `redis://redis:6379/0` (the service name).
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# The longest task is the weekly performance pipeline (about a minute); 30 minutes is a hard ceiling, not an expectation.
TASK_SOFT_TIME_LIMIT = 25 * 60
TASK_TIME_LIMIT = 30 * 60
VISIBILITY_TIMEOUT = 60 * 60

app = Celery("trackflow", broker=REDIS_URL, backend=REDIS_URL, include=["services.tasks"])

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # one message at a time: a long task does not hold messages other workers could take
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT,
    task_time_limit=TASK_TIME_LIMIT,
    result_expires=24 * 60 * 60,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT, "socket_connect_timeout": 2, "socket_timeout": 5},
    # If Redis is down, `.delay()` fails within about a second so the endpoint can answer 503 instead of hanging.
    task_publish_retry=True,
    task_publish_retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
    result_backend_transport_options={"socket_connect_timeout": 2, "socket_timeout": 5},
    # Events are what Flower shows: queued (sent), started, succeeded, failed, retried.
    worker_send_task_events=True,
    task_send_sent_event=True,
)

logger = logging.getLogger("trackflow.tasks")
_started: dict[str, float] = {}


def _attempt(task) -> int:
    return int(getattr(task.request, "retries", 0) or 0) + 1


def _duration_ms(task_id: str) -> int:
    started = _started.pop(task_id, None)
    return int((time.perf_counter() - started) * 1000) if started is not None else -1


@task_prerun.connect
def log_started(task_id=None, task=None, **_):
    _started[task_id] = time.perf_counter()
    logger.info("task_id=%s task=%s attempt=%d status=started", task_id, task.name, _attempt(task))


@task_success.connect
def log_success(sender=None, **_):
    task_id = sender.request.id
    logger.info("task_id=%s task=%s attempt=%d status=success duration_ms=%d", task_id, sender.name, _attempt(sender), _duration_ms(task_id))


@task_retry.connect
def log_retry(sender=None, request=None, reason=None, **_):
    task_id = request.id
    logger.warning(
        "task_id=%s task=%s attempt=%d status=retry duration_ms=%d error=%s", task_id, sender.name, int(request.retries or 0) + 1, _duration_ms(task_id), reason
    )


@task_failure.connect
def log_failure(sender=None, task_id=None, exception=None, **_):
    # The full error message: the reason someone reads the log.
    logger.error(
        "task_id=%s task=%s attempt=%d status=failure duration_ms=%d error=%s: %s",
        task_id, sender.name, _attempt(sender), _duration_ms(task_id), type(exception).__name__, exception,
    )
