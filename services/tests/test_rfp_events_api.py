"""The SSE stream of RFP notifications (Real Time Systems, part 1), tested on the wire.

The tests start the real application on a local port (uvicorn in a thread) and read `GET /rfp/events` with a streaming HTTP client, so what is asserted is what a browser's `fetch` would receive: the
response headers, the `event:` line, the `data:` JSON, the keep-alive comments and the `id:` used for replay. The notification is raised by the REAL intake: a PDF is uploaded through the API and the event
arrives when the classifier accepts it. No model or agent is involved anywhere in this part.
"""

import asyncio
import json
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlmodel import Session, SQLModel, create_engine

from data.pipelines.rfp_intake import notifications
from services import events as events_module
from services.database import get_session
from services.events import EventBus, frame
from services.main import app

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "pipelines" / "fixtures" / "rfp-requests"
TICKET_FIELDS = {"ticket_id", "rfp_id", "client_name", "client_country", "services_requested", "departments_needed", "status", "created_at"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    """The real app on a local port, with a file database (the intake runs in a background thread of the server) and a registered account."""
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw-rfp"))
    monkeypatch.setenv("RFP_AUTO_DRAFT", "false")
    monkeypatch.setenv("RFP_SSE_KEEPALIVE_SECONDS", "0.3")
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    base = f"http://127.0.0.1:{port}"
    credentials = {"email": "commercial@example.com", "password": "correct-horse-42"}
    assert httpx.post(f"{base}/auth/register", json={"name": "Commercial Team", **credentials}).status_code == 201
    token = httpx.post(f"{base}/auth/login", json=credentials).json()["access_token"]
    yield {"base": base, "token": token, "headers": {"Authorization": f"Bearer {token}"}, "engine": engine}
    server.should_exit = True
    thread.join(timeout=15)
    app.dependency_overrides.clear()


def open_stream(live, headers=None, timeout=10.0):
    merged = {**live["headers"], **(headers or {})}
    return httpx.stream("GET", f"{live['base']}/rfp/events", headers=merged, timeout=httpx.Timeout(timeout, read=timeout))


def frames(response, stop_after=None, max_frames=60):
    """Reads the stream frame by frame: `[{event, data, id, comment, retry}]`, until `stop_after(frames)` says enough."""
    found, current = [], {}
    if not hasattr(response, "_sse_lines"):
        response._sse_lines = response.iter_lines()  # one iterator for the whole response: the next call continues where this one stopped
    for line in response._sse_lines:
        if line == "":
            if current:
                found.append(current)
                current = {}
                if stop_after and stop_after(found) or len(found) >= max_frames:
                    break
            continue
        if line.startswith(":"):
            current["comment"] = line[1:].strip()
            continue
        name, _, value = line.partition(":")
        current[name] = value.lstrip(" ")
    return found


def upload(live, number: int) -> dict:
    content = (FIXTURES / f"trackflow-request-{number}.pdf").read_bytes()
    response = httpx.post(f"{live['base']}/rfp/tickets", headers=live["headers"], files={"file": (f"trackflow-request-{number}.pdf", content, "application/pdf")}, timeout=30)
    assert response.status_code == 202
    return response.json()


def ticket_events(found):
    return [item for item in found if item.get("event") == "rfp_ticket_created"]


# ------------------------------------------------------------------------------------------------ the wire format
def test_the_response_is_an_event_stream_with_the_headers_a_proxy_needs(live):
    with open_stream(live) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in response.headers["cache-control"] and "no-transform" in response.headers["cache-control"]
        assert response.headers["connection"].lower() == "keep-alive" and response.headers["x-accel-buffering"] == "no"
        first = frames(response, stop_after=lambda found: True)[0]
        assert first.get("retry") == "3000" and first["comment"] == "connected"  # the client is told how long the server suggests waiting and that the stream is open


def test_a_new_rfp_ticket_arrives_as_a_named_event_with_the_ticket_fields_as_json(live):
    with open_stream(live) as response:
        frames(response, stop_after=lambda found: True)  # connected
        created = upload(live, 2)  # Luna Cosmetics: the real intake runs after the response
        found = ticket_events(frames(response, stop_after=lambda items: bool(ticket_events(items))))
    assert len(found) == 1
    item = found[0]
    assert item["event"] == "rfp_ticket_created" and "id" in item  # a NAMED event, not the generic "message" type
    payload = json.loads(item["data"])  # `data:` is the ticket's JSON itself, not an envelope
    assert set(payload) == TICKET_FIELDS and "event" not in payload
    assert payload["ticket_id"] == created["ticket_id"] and payload["rfp_id"] == created["rfp_id"] and payload["status"] == "analyzing"  # it enters the system as `analyzing`
    assert (payload["client_name"], payload["client_country"]) == ("Luna Cosmetics", "US")
    assert payload["services_requested"] == ["warehousing", "last_mile"] and payload["departments_needed"] == ["warehouse", "lastmile"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", payload["created_at"])


def test_the_event_says_what_the_api_says_about_the_same_ticket(live):
    with open_stream(live) as response:
        frames(response, stop_after=lambda found: True)
        created = upload(live, 1)
        payload = json.loads(ticket_events(frames(response, stop_after=lambda items: bool(ticket_events(items))))[0]["data"])
    shown = httpx.get(f"{live['base']}/rfp/tickets/{created['ticket_id']}", headers=live["headers"]).json()
    assert (payload["client_name"], payload["client_country"], payload["rfp_id"]) == (shown["metadata"]["client_name"], shown["metadata"]["client_country"], shown["rfp_id"])  # same names, same values
    assert payload["services_requested"] == shown["metadata"]["services_requested"] and payload["departments_needed"] == shown["metadata"]["departments_needed"] == ["warehouse", "reverse"]
    from datetime import datetime

    assert datetime.fromisoformat(shown["created_at"]).tzinfo is not None  # the API says the offset too: the same instant as the event's `...Z`
    assert datetime.fromisoformat(shown["created_at"]).replace(microsecond=0) == datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))


def test_a_document_that_is_not_an_rfp_notifies_nobody(live):
    with open_stream(live) as response:
        frames(response, stop_after=lambda found: True)
        upload(live, 3)  # the carrier pitch: the classifier rejects it
        found = frames(response, stop_after=lambda items: sum(1 for item in items if item.get("comment") == "keep-alive") >= 4)
    assert ticket_events(found) == []  # only keep-alive frames in that time


def test_every_connected_client_receives_the_event(live):
    with open_stream(live) as first, open_stream(live) as second:
        frames(first, stop_after=lambda found: True)
        frames(second, stop_after=lambda found: True)
        created = upload(live, 2)
        stop = lambda items: bool(ticket_events(items))  # noqa: E731
        one, two = ticket_events(frames(first, stop_after=stop)), ticket_events(frames(second, stop_after=stop))
    assert json.loads(one[0]["data"])["ticket_id"] == json.loads(two[0]["data"])["ticket_id"] == created["ticket_id"]


# ------------------------------------------------------------------------------------------------ keep-alive, lifetime
def test_keep_alive_comment_frames_keep_an_idle_connection_open(live):
    with open_stream(live) as response:
        found = frames(response, stop_after=lambda items: sum(1 for item in items if item.get("comment") == "keep-alive") >= 2)
    assert sum(1 for item in found if item.get("comment") == "keep-alive") >= 2 and all("event" not in item for item in found)


def test_the_stream_closes_by_itself_after_its_maximum_lifetime(live, monkeypatch):
    monkeypatch.setenv("RFP_SSE_MAX_SECONDS", "1")
    started = time.time()
    with open_stream(live) as response:
        found = frames(response)  # reads until the server ends the stream
    assert 0.9 <= time.time() - started < 6 and found  # the client then reconnects (and presents its token again)


# ------------------------------------------------------------------------------------------------ authentication
def test_there_is_no_stream_without_the_jwt_of_the_backoffice(live):
    with httpx.stream("GET", f"{live['base']}/rfp/events") as response:
        assert response.status_code == 401 and not response.headers["content-type"].startswith("text/event-stream")
        assert b"event:" not in response.read()
    with httpx.stream("GET", f"{live['base']}/rfp/events", headers={"Authorization": "Bearer not-a-real-token"}) as response:
        assert response.status_code == 401 and not response.headers["content-type"].startswith("text/event-stream")


def test_the_token_goes_in_the_authorization_header_not_in_the_url(live):
    with httpx.stream("GET", f"{live['base']}/rfp/events", params={"token": live["token"], "access_token": live["token"]}) as response:
        assert response.status_code == 401


def test_the_browser_may_send_the_headers_the_stream_needs(live):
    answer = httpx.options(f"{live['base']}/rfp/events", headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "accept,authorization,last-event-id"})  # exactly the headers the dashboard's fetch sends
    allowed = answer.headers["access-control-allow-headers"].lower()
    assert answer.status_code == 200 and "authorization" in allowed and "last-event-id" in allowed


# ------------------------------------------------------------------------------------------------ recovery: Last-Event-ID replay
def publish(count: int) -> list[str]:
    return [events_module.bus.publish("rfp_ticket_created", {"ticket_id": f"tkt_{index:04d}", "rfp_id": f"rfp_{index:04d}", "status": "analyzing"}) for index in range(count)]


def test_a_client_that_reconnects_with_last_event_id_receives_only_what_it_missed(live):
    first, second, third = publish(3)
    with open_stream(live, headers={"Last-Event-ID": first}) as response:
        found = ticket_events(frames(response, stop_after=lambda items: len(ticket_events(items)) >= 2))
    assert [item["id"] for item in found] == [second, third]  # not the first one again
    assert [json.loads(item["data"])["ticket_id"] for item in found] == ["tkt_0001", "tkt_0002"]


def test_an_id_from_another_run_replays_nothing_and_a_fresh_event_still_arrives(live):
    publish(2)
    with open_stream(live, headers={"Last-Event-ID": "deadbeef-1"}) as response:
        frames(response, stop_after=lambda found: True)
        late = events_module.bus.publish("rfp_ticket_created", {"ticket_id": "tkt_late", "status": "analyzing"})
        found = ticket_events(frames(response, stop_after=lambda items: bool(ticket_events(items))))
    assert [item["id"] for item in found] == [late]  # the client recovers the rest by refetching the list


def test_events_published_during_the_connection_follow_the_replay_without_gaps_or_repeats(live):
    ids = publish(2)
    with open_stream(live, headers={"Last-Event-ID": ids[0]}) as response:
        frames(response, stop_after=lambda found: any(item.get("id") == ids[1] for item in found))
        live_ids = [events_module.bus.publish("rfp_ticket_created", {"ticket_id": f"tkt_live{n}", "status": "analyzing"}) for n in range(2)]
        found = ticket_events(frames(response, stop_after=lambda items: len(ticket_events(items)) >= 2))
    assert [item["id"] for item in found] == live_ids


# ------------------------------------------------------------------------------------------------ the bus itself
def test_the_bus_keeps_a_bounded_buffer_and_ids_that_identify_the_run():
    bus = EventBus(buffer_size=3)
    ids = [bus.publish("rfp_ticket_created", {"n": n}) for n in range(5)]
    assert all(item.startswith(f"{bus.boot}-") for item in ids) and len(set(ids)) == 5
    assert [item.data["n"] for item in bus.missed_since(ids[0])] == [2, 3, 4]  # the buffer holds the last three: what fell off cannot be replayed
    assert bus.missed_since(None) == [] and bus.missed_since("other-1") == [] and bus.missed_since(f"{bus.boot}-x") == []


def test_publishing_from_another_thread_reaches_a_subscriber_in_the_event_loop():
    bus = EventBus()

    async def scenario():
        subscription = bus.subscribe()
        threading.Thread(target=lambda: bus.publish("rfp_ticket_created", {"ticket_id": "tkt_1"})).start()  # the intake publishes from a worker thread
        item = await asyncio.wait_for(subscription.queue.get(), timeout=5)
        bus.unsubscribe(subscription)
        return item, bus.subscriber_count

    item, remaining = asyncio.run(scenario())
    assert item.event == "rfp_ticket_created" and item.data == {"ticket_id": "tkt_1"} and remaining == 0


def test_a_client_that_cannot_keep_up_is_dropped_instead_of_growing_without_bound(monkeypatch):
    monkeypatch.setattr(events_module, "QUEUE_LIMIT", 2)
    bus = EventBus()

    async def scenario():
        subscription = bus.subscribe()
        for number in range(5):
            bus.publish("rfp_ticket_created", {"n": number})
        await asyncio.sleep(0.05)
        return subscription.overflowed, bus.subscriber_count

    assert asyncio.run(scenario()) == (True, 0)


def test_the_frame_is_a_named_event_with_single_line_json():
    bus = EventBus()
    bus.publish("rfp_ticket_created", {"ticket_id": "tkt_0225", "client_name": "Ñandú\nSA"})
    text = frame(bus.missed_since(f"{bus.boot}-0")[0])
    assert text.startswith(f"id: {bus.boot}-1\nevent: rfp_ticket_created\ndata: {{") and text.endswith("}\n\n") and text.count("\n") == 4  # a newline in a value is escaped, not a new SSE line
    assert json.loads(text.split("data: ", 1)[1]) == {"ticket_id": "tkt_0225", "client_name": "Ñandú\nSA"}


# ------------------------------------------------------------------------------------------------ the notification contract and the absence of a model
def test_a_notification_that_fails_to_publish_never_breaks_the_intake(caplog):
    notifications.set_publisher(lambda event, payload: (_ for _ in ()).throw(RuntimeError("broker down")))
    try:
        notifications.publish("rfp_ticket_created", {"ticket_id": "x"})  # does not raise
    finally:
        notifications.set_publisher(events_module.bus.publish)
    assert "could not be published" in caplog.text


def test_this_part_has_no_model_or_agent_anywhere():
    root = Path(__file__).resolve().parents[2]
    files = [root / "services" / "events.py", root / "services" / "routers" / "rfp_events.py", root / "data" / "pipelines" / "rfp_intake" / "notifications.py"]
    banned = re.compile(r"\b(model_client|llm|openai|anthropic|langgraph|langchain|rag|agent)\b", re.IGNORECASE)
    for path in files:
        imports = [line for line in path.read_text(encoding="utf-8").splitlines() if line.lstrip().startswith(("import ", "from "))]
        assert imports and not [line for line in imports if banned.search(line)], path.name  # a communication layer: nothing it imports calls a model or an agent


def test_the_intake_with_a_model_that_must_not_be_called_still_notifies(live, monkeypatch):
    from data.process import model_client

    def forbidden(*args, **kwargs):
        raise AssertionError("the notification flow must not call a model")

    monkeypatch.setattr(model_client, "post", forbidden)
    with open_stream(live) as response:
        frames(response, stop_after=lambda found: True)
        upload(live, 1)
        assert ticket_events(frames(response, stop_after=lambda items: bool(ticket_events(items))))
