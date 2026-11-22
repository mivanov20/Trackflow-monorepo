"""Stub receiver POST /telemetry/events: envelope validation, count, logging and configuration."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from services.main import app
from services.routers.telemetry import telemetry_endpoint, validate_telemetry_config
from services.telemetry_schemas import MAX_EVENTS_PER_BATCH, TelemetryEvent


def make_event(event_type: str = "inbound_order_created", **overrides) -> dict:
    event = {
        "eventId": str(uuid.uuid4()),
        "timestamp": "2026-11-21T14:03:22.418Z",
        "sessionId": str(uuid.uuid4()),
        "userId": str(uuid.uuid4()),
        "event_type": event_type,
        "schemaVersion": "1.1.0",
        "requestId": str(uuid.uuid4()),
        "properties": {"quantity": 5},
    }
    event.update(overrides)
    return event


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_accepts_a_batch_and_returns_the_count(client):
    events = [make_event("inbound_order_created"), make_event("page_viewed"), make_event("outbound_order_created")]
    response = client.post("/telemetry/events", json={"events": events})
    assert response.status_code == 200
    assert response.json() == {"received": 3}


def test_logs_the_number_of_events_and_each_type(client, caplog):
    with caplog.at_level(logging.INFO, logger="services.telemetry"):
        client.post("/telemetry/events", json={"events": [make_event("page_viewed"), make_event("flow_started")]})
    assert "2 event(s) [page_viewed, flow_started]" in caplog.text


def test_empty_batch_is_accepted(client):
    assert client.post("/telemetry/events", json={"events": []}).json() == {"received": 0}


def test_anonymous_events_may_have_null_session_and_user(client):
    event = make_event("login_attempt_failed", sessionId=None, userId=None)
    assert client.post("/telemetry/events", json={"events": [event]}).json() == {"received": 1}


def test_does_not_require_authentication(client):
    # The stub is open on purpose: sendBeacon cannot carry an Authorization header. Phase 3 adds auth.
    assert client.post("/telemetry/events", json={"events": [make_event()]}).status_code == 200


@pytest.mark.parametrize(
    "field",
    ["eventId", "timestamp", "sessionId", "userId", "event_type", "schemaVersion", "requestId", "properties"],
)
def test_every_envelope_field_is_required(client, field):
    event = make_event()
    del event[field]
    assert client.post("/telemetry/events", json={"events": [event]}).status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"eventId": "not-a-uuid"},
        {"requestId": "123"},
        {"timestamp": "yesterday"},
        {"timestamp": "2026-11-21T14:03:22"},  # no timezone
        {"event_type": "InboundOrderCreated"},
        {"event_type": "inbound"},
        {"event_type": "inbound_order_created\nforged log line"},
        {"schemaVersion": "v1"},
        {"properties": "text"},
        {"unexpected": True},
    ],
)
def test_malformed_envelope_is_rejected(client, overrides):
    assert client.post("/telemetry/events", json={"events": [make_event(**overrides)]}).status_code == 422


def test_batch_size_is_limited(client):
    events = [make_event() for _ in range(MAX_EVENTS_PER_BATCH + 1)]
    assert client.post("/telemetry/events", json={"events": events}).status_code == 422
    events = events[:MAX_EVENTS_PER_BATCH]
    assert client.post("/telemetry/events", json={"events": events}).json() == {"received": MAX_EVENTS_PER_BATCH}


def test_body_must_be_an_object_with_events(client):
    assert client.post("/telemetry/events", json=[make_event()]).status_code == 422
    assert client.post("/telemetry/events", json={}).status_code == 422


def test_model_is_the_standard_envelope():
    assert list(TelemetryEvent.model_fields) == [
        "eventId", "timestamp", "sessionId", "userId", "event_type", "schemaVersion", "requestId", "properties",
    ]


def test_cors_preflight_allows_the_request_id_header(client):
    response = client.options(
        "/telemetry/events",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-request-id",
        },
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
