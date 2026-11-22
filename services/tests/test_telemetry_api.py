"""POST /telemetry/events: per-event validation, one bulk INSERT per batch, immutable storage and configuration."""

import importlib.util
import logging
import re
import typing
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event, text
from sqlmodel import Session, select

from services.database import get_session
from services.main import app
from services.models import TelemetryEventRecord
from services.routers.telemetry import telemetry_endpoint, validate_telemetry_config
from services.telemetry_catalogue import EVENT_CATALOGUE
from services.telemetry_schemas import MAX_EVENTS_PER_BATCH, TelemetryEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("telemetry_events_plan", REPO_ROOT / "docs" / "telemetry" / "events.py")
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)


def make_event(event_type: str = "page_viewed", properties: dict | None = None, **overrides) -> dict:
    # An event type that is not in the plan has no example: its properties come from the caller.
    example = next((e["example"] for e in plan.EVENTS if e["type"] == event_type), {})
    event = {
        "eventId": str(uuid.uuid4()),
        "timestamp": "2026-11-21T14:03:22.418Z",
        "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
        "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
        "event_type": event_type,
        "schemaVersion": plan.SCHEMA_VERSION,
        "requestId": str(uuid.uuid4()),
        "properties": dict(example if properties is None else properties),
    }
    event.update(overrides)
    return event


@pytest.fixture
def client(incident_engine):
    def get_test_session():
        with Session(incident_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def rows(incident_engine):
    def read():
        with Session(incident_engine) as session:
            return session.exec(select(TelemetryEventRecord).order_by(TelemetryEventRecord.timestamp)).all()

    return read


def post(client, events):
    return client.post("/telemetry/events", json={"events": events})


# --- the answer ----------------------------------------------------------------------------


def test_stores_a_valid_batch_and_reports_received_stored_rejected(client, rows):
    response = post(client, [make_event("page_viewed"), make_event("inbound_order_created"), make_event("outbound_order_created")])
    assert response.status_code == 200
    assert response.json() == {"received": 3, "stored": 3, "rejected": 0}
    assert len(rows()) == 3


def test_a_mixed_batch_stores_the_valid_events_and_rejects_the_rest_individually(client, rows):
    batch = [
        make_event("inbound_order_created"),
        make_event("page_viewed", eventId="not-a-uuid"),  # broken envelope
        make_event("outbound_order_created"),
        make_event("generic_button_clicked", properties={}),  # not in the plan
        make_event("page_viewed", properties={**make_event("page_viewed")["properties"], "email": "a@b.co"}),  # not allowlisted
        "not an object",
        make_event("stock_threshold_triggered"),
    ]
    response = post(client, batch)
    assert response.status_code == 200
    assert response.json() == {"received": 7, "stored": 3, "rejected": 4}
    assert sorted(r.event_type for r in rows()) == ["inbound_order_created", "outbound_order_created", "stock_threshold_triggered"]


def test_a_batch_with_only_invalid_events_is_still_a_200(client, rows):
    response = post(client, [make_event(timestamp="yesterday"), make_event("page_viewed", properties={})])
    assert response.status_code == 200
    assert response.json() == {"received": 2, "stored": 0, "rejected": 2}
    assert rows() == []


def test_empty_batch_is_accepted(client):
    assert post(client, []).json() == {"received": 0, "stored": 0, "rejected": 0}


def test_the_body_is_not_typed_as_a_list_of_events():
    """A typed body would turn one bad event into a 422 for the whole batch."""
    from services.routers.telemetry import TelemetryIngestBody

    annotation = TelemetryIngestBody.model_fields["events"].annotation
    assert typing.get_origin(annotation) is list
    assert typing.get_args(annotation) == (typing.Any,)


def test_only_a_broken_envelope_is_a_422(client):
    assert client.post("/telemetry/events", json=[make_event()]).status_code == 422
    assert client.post("/telemetry/events", json={}).status_code == 422
    assert client.post("/telemetry/events", json={"events": "nope"}).status_code == 422
    too_many = [make_event() for _ in range(MAX_EVENTS_PER_BATCH + 1)]
    assert post(client, too_many).status_code == 422
    assert post(client, too_many[:MAX_EVENTS_PER_BATCH]).json()["stored"] == MAX_EVENTS_PER_BATCH


def test_does_not_require_authentication(client):
    # sendBeacon cannot carry an Authorization header.
    assert post(client, [make_event()]).status_code == 200


# --- one INSERT per batch ------------------------------------------------------------------


def test_a_batch_is_one_insert_statement_not_one_per_event(client, incident_engine):
    statements = []

    @sa_event.listens_for(incident_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO TELEMETRY_EVENTS"):
            statements.append(statement)

    post(client, [make_event("page_viewed") for _ in range(20)])
    assert len(statements) == 1
    assert post(client, [make_event("page_viewed"), make_event(eventId="bad")]).json()["stored"] == 1
    assert len(statements) == 2


def test_a_database_failure_answers_503_so_the_browser_retries_and_stores_nothing(client, incident_engine, rows):
    with incident_engine.begin() as connection:
        connection.execute(text("DROP TABLE telemetry_events"))
    response = post(client, [make_event()])
    assert response.status_code == 503
    assert "telemetry_events" not in response.text


# --- what is stored ------------------------------------------------------------------------


def test_row_mapping_follows_the_plan(client, rows):
    event = make_event("outbound_order_created", eventId="5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21", requestId="e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84")
    post(client, [event])
    (row,) = rows()
    assert row.event_type == "outbound_order_created"
    assert row.service == "backoffice"
    assert row.level == "info"
    assert row.timestamp.isoformat().startswith("2026-11-21T14:03:22.418")
    assert row.value == Decimal(event["properties"]["quantity"])
    assert row.message == EVENT_CATALOGUE["outbound_order_created"]["message"]
    expected_tags = {
        **event["properties"],
        "_envelope": {
            "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
            "sessionId": event["sessionId"],
            "userId": event["userId"],
            "schemaVersion": plan.SCHEMA_VERSION,
            "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
        },
    }
    assert row.tags == expected_tags
    assert {"warehouse", "country", "client_id", "product_id", "product_category"} <= set(row.tags)


def test_technical_events_get_a_warn_or_error_level(client, rows):
    post(client, [make_event("frontend_error_caught"), make_event("network_request_failed"), make_event("page_viewed")])
    levels = {r.event_type: r.level for r in rows()}
    assert levels == {"frontend_error_caught": "error", "network_request_failed": "warn", "page_viewed": "info"}


def test_anonymous_events_store_null_ids_in_the_envelope(client, rows):
    post(client, [make_event("login_attempt_failed", sessionId=None, userId=None)])
    (row,) = rows()
    assert row.tags["_envelope"]["sessionId"] is None and row.tags["_envelope"]["userId"] is None
    assert row.tags["failure_reason"] == "invalid_credentials"
    assert row.value == 2


def test_every_example_of_the_plan_is_accepted_and_stored_with_its_allowlist_intact(client, rows):
    events = [make_event(e["type"]) for e in plan.EVENTS]
    response = post(client, events[:MAX_EVENTS_PER_BATCH])
    assert response.json() == {"received": len(events), "stored": len(events), "rejected": 0}
    stored = {r.event_type: r for r in rows()}
    assert set(stored) == {e["type"] for e in plan.EVENTS}
    for e in plan.EVENTS:
        tags = dict(stored[e["type"]].tags)
        tags.pop("_envelope")
        assert tags == e["example"], e["type"]


@pytest.mark.parametrize(
    "type_, properties",
    [
        ("inbound_order_created", {"warehouse": "madrid"}),  # not a warehouse of the plan
        ("outbound_order_created", {"exit_type": "gift"}),
        ("stock_threshold_triggered", {"quantity": -1}),
        ("page_viewed", {"route_template": "/search?q=secret"}),
        ("login_attempt_failed", {"failure_reason": "wrong password for ana@example.com"}),
    ],
)
def test_values_outside_the_allowlist_are_rejected(client, rows, type_, properties):
    base = make_event(type_)["properties"]
    event = make_event(type_, properties={**base, **properties})
    assert post(client, [event]).json() == {"received": 1, "stored": 0, "rejected": 1}
    assert rows() == []


def test_a_missing_required_property_and_a_foreign_schema_major_are_rejected(client):
    incomplete = make_event("inbound_order_created")
    del incomplete["properties"]["quantity"]
    assert post(client, [incomplete]).json()["rejected"] == 1
    assert post(client, [make_event(schemaVersion="2.0.0")]).json()["rejected"] == 1
    assert post(client, [make_event(schemaVersion="1.0.0")]).json()["stored"] == 1  # older minor of the same major


def test_rejections_are_logged_with_reason_codes_and_never_with_values(client, caplog):
    secret = make_event("page_viewed")
    secret["properties"]["email"] = "ana@example.com"
    with caplog.at_level(logging.INFO, logger="services.telemetry"):
        post(client, [secret, make_event("page_viewed")])
    assert "received=2 stored=1 rejected=1" in caplog.text
    assert "#0:unknown_property" in caplog.text
    assert "ana@example.com" not in caplog.text


# --- immutability and structure --------------------------------------------------------------


def test_the_table_has_the_eight_columns_and_the_three_indexes(incident_engine):
    from sqlalchemy import inspect

    inspector = inspect(incident_engine)
    columns = [c["name"] for c in inspector.get_columns("telemetry_events")]
    assert columns == ["id", "timestamp", "service", "event_type", "level", "value", "message", "tags"]
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("telemetry_events")}
    assert indexes == {
        "ix_telemetry_events_timestamp": ["timestamp"],
        "ix_telemetry_events_event_type": ["event_type"],
        "ix_telemetry_events_tags": ["tags"],
    }


def test_postgres_ddl_uses_jsonb_a_gin_index_and_a_generated_id():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    table = TelemetryEventRecord.__table__
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "tags JSONB DEFAULT '{}' NOT NULL" in ddl
    assert "timestamp TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    assert "level TEXT DEFAULT 'info' NOT NULL" in ddl
    gin = next(i for i in table.indexes if i.name == "ix_telemetry_events_tags")
    assert "USING gin (tags)" in str(CreateIndex(gin).compile(dialect=postgresql.dialect()))


def test_the_supabase_sql_file_defines_the_same_table():
    sql = (REPO_ROOT / "services" / "sql" / "telemetry_events.sql").read_text(encoding="utf-8")
    assert "id          uuid PRIMARY KEY DEFAULT gen_random_uuid()" in sql
    for column in ("timestamp   timestamptz NOT NULL", "service     text NOT NULL", "event_type  text NOT NULL", "tags        jsonb NOT NULL DEFAULT '{}'"):
        assert column in sql
    assert sql.count("CREATE INDEX IF NOT EXISTS") == 3 and "USING gin (tags)" in sql
    assert "BEFORE UPDATE OR DELETE ON telemetry_events" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql


def test_no_code_updates_or_deletes_telemetry_events():
    pattern = re.compile(r"\b(update|delete)\s*\(|\.delete\(|\bsession\.(delete|merge)\b|UPDATE\s+telemetry_events|DELETE\s+FROM\s+telemetry_events", re.I)
    for path in (REPO_ROOT / "services").rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        text_ = path.read_text(encoding="utf-8")
        if "telemetry" in path.name or "TelemetryEventRecord" in text_:
            for match in pattern.finditer(text_):
                line = text_[: match.start()].count("\n") + 1
                # models.py only mentions the words inside the trigger that REFUSES them.
                assert path.name == "models.py" and "BEFORE UPDATE OR DELETE" in text_.splitlines()[line - 1], f"{path.name}:{line}"


def test_the_event_model_of_phase_2_is_unchanged():
    assert list(TelemetryEvent.model_fields) == ["eventId", "timestamp", "sessionId", "userId", "event_type", "schemaVersion", "requestId", "properties"]
    assert TelemetryEvent.model_config.get("extra") == "forbid"


# --- configuration ---------------------------------------------------------------------------


def test_cors_preflight_allows_the_request_id_header(client):
    response = client.options(
        "/telemetry/events",
        headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-request-id"},
    )
    assert response.status_code == 200
    assert "x-request-id" in response.headers["access-control-allow-headers"].lower()


def test_endpoint_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("TELEMETRY_ENDPOINT", raising=False)
    assert telemetry_endpoint() == "/telemetry/events"
    monkeypatch.setenv("TELEMETRY_ENDPOINT", " https://collector.example/telemetry/events ")
    assert telemetry_endpoint() == "https://collector.example/telemetry/events"
    validate_telemetry_config()


def test_malformed_endpoint_fails_at_startup(monkeypatch):
    monkeypatch.setenv("TELEMETRY_ENDPOINT", "collector.example/events")
    with pytest.raises(RuntimeError, match="TELEMETRY_ENDPOINT"):
        validate_telemetry_config()
