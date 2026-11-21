import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from services.main import app
from services.models import Incident

from conftest import NEW_INCIDENT


@pytest.fixture
def client(incident_client):
    return incident_client


def create(client, **overrides):
    response = client.post("/api/incidents", json={**NEW_INCIDENT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# --- authentication -------------------------------------------------------------


def test_incident_routes_require_login(client):
    client.headers.pop("Authorization")
    for method, path in (("get", "/api/incidents"), ("get", "/api/incidents/summary"), ("get", "/api/incidents/1")):
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.json()["error"] == "http_error"
    assert client.post("/api/incidents", json=NEW_INCIDENT).status_code == 401
    assert client.patch("/api/incidents/1/status", json={"status": "in_progress"}).status_code == 401
    assert client.get("/api/incidents", headers={"Authorization": "Bearer invalid"}).status_code == 401


# --- create -------------------------------------------------------------------


def test_create_returns_201_with_generated_fields(client):
    body = create(client)
    assert body["id"] > 0
    assert body["status"] == "open"
    assert body["created_at"].endswith("Z") and body["created_at"] == body["updated_at"]
    assert body["branch"] == "central"


def test_create_accepts_every_branch_origin_and_category(client):
    for branch in ("central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"):
        create(client, branch=branch)
    for origin in ("customer", "branch", "internal"):
        create(client, origin=origin)
    for category in (
        "lost_parcel", "delivery_failure", "inventory_discrepancy", "carrier_issue", "returns_issue",
        "warehouse_incident", "system_failure", "client_complaint", "other",
    ):
        create(client, category=category)


@pytest.mark.parametrize("field", ["title", "description", "category", "origin", "branch"])
def test_missing_field_returns_400_naming_the_field(client, field):
    body = {k: v for k, v in NEW_INCIDENT.items() if k != field}
    response = client.post("/api/incidents", json=body)
    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "validation_error"
    assert [e["field"] for e in data["errors"]] == [field]


@pytest.mark.parametrize(
    "field,value", [("category", "nope"), ("origin", "alien"), ("branch", "mars"), ("status", "done"), ("title", "  ")]
)
def test_invalid_value_returns_400_naming_the_field(client, field, value):
    response = client.post("/api/incidents", json={**NEW_INCIDENT, field: value})
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == field


def test_malformed_json_returns_400_not_a_stack_trace(client):
    response = client.post("/api/incidents", content="{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "body"
    assert "Traceback" not in response.text


def test_empty_body_returns_400(client):
    assert client.post("/api/incidents").status_code == 400


# --- read ---------------------------------------------------------------------


def test_empty_database_returns_empty_list_and_zero_summary(client):
    assert client.get("/api/incidents").json() == []
    summary = client.get("/api/incidents/summary").json()
    assert summary["total"] == 0
    for key in ("by_status", "by_category", "by_origin", "by_branch"):
        assert summary[key] and all(count == 0 for count in summary[key].values())
    assert set(summary["by_status"]) == {"open", "in_progress", "resolved", "discarded"}
    assert set(summary["by_branch"]) == {"central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"}


def test_get_by_id_and_404(client):
    created = create(client)
    assert client.get(f"/api/incidents/{created['id']}").json() == created
    response = client.get("/api/incidents/99999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_list_filters(client):
    create(client, category="lost_parcel", origin="customer", branch="la_office")
    create(client, category="carrier_issue", origin="branch", branch="zaragoza_warehouse")
    create(client, category="carrier_issue", origin="internal", branch="central")

    assert len(client.get("/api/incidents").json()) == 3
    assert len(client.get("/api/incidents", params={"category": "carrier_issue"}).json()) == 2
    assert len(client.get("/api/incidents", params={"origin": "branch"}).json()) == 1
    assert len(client.get("/api/incidents", params={"branch": "central"}).json()) == 1
    assert len(client.get("/api/incidents", params={"status": "resolved"}).json()) == 0
    both = client.get("/api/incidents", params={"category": "carrier_issue", "origin": "internal"}).json()
    assert len(both) == 1 and both[0]["branch"] == "central"


def test_invalid_filter_value_returns_400(client):
    response = client.get("/api/incidents", params={"status": "closed"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "status"


def test_summary_counts(client):
    create(client, category="lost_parcel", origin="customer", branch="la_office")
    create(client, category="lost_parcel", origin="branch", branch="la_office")
    create(client, category="other", origin="internal", branch="central")
    summary = client.get("/api/incidents/summary").json()
    assert summary["total"] == 3
    assert summary["by_status"]["open"] == 3
    assert summary["by_category"]["lost_parcel"] == 2
    assert summary["by_origin"] == {"customer": 1, "branch": 1, "internal": 1}
    assert summary["by_branch"]["la_office"] == 2


# --- status lifecycle -----------------------------------------------------------


def patch_status(client, incident_id, status):
    return client.patch(f"/api/incidents/{incident_id}/status", json={"status": status})


def test_full_lifecycle_and_updated_at(client):
    incident = create(client)
    moved = patch_status(client, incident["id"], "in_progress")
    assert moved.status_code == 200 and moved.json()["status"] == "in_progress"
    assert moved.json()["updated_at"] >= incident["updated_at"]
    assert moved.json()["created_at"] == incident["created_at"]
    assert patch_status(client, incident["id"], "resolved").json()["status"] == "resolved"


def test_open_can_be_discarded(client):
    assert patch_status(client, create(client)["id"], "discarded").status_code == 200


@pytest.mark.parametrize(
    "steps,target",
    [
        ([], "resolved"),
        ([], "open"),
        (["in_progress"], "open"),
        (["in_progress", "resolved"], "in_progress"),
        (["in_progress", "resolved"], "discarded"),
        (["discarded"], "in_progress"),
    ],
)
def test_invalid_transitions_return_400(client, steps, target):
    incident_id = create(client)["id"]
    for step in steps:
        assert patch_status(client, incident_id, step).status_code == 200
    response = patch_status(client, incident_id, target)
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "status"


@pytest.mark.parametrize("value", ["closed", "", None, 3])
def test_status_must_be_in_enum(client, value):
    response = patch_status(client, create(client)["id"], value)
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "status"


def test_patch_without_status_returns_400_and_unknown_id_404(client):
    incident_id = create(client)["id"]
    assert client.patch(f"/api/incidents/{incident_id}/status", json={}).status_code == 400
    assert patch_status(client, 99999, "in_progress").status_code == 404


# --- errors ---------------------------------------------------------------------


def test_unhandled_exception_returns_generic_500(client, monkeypatch):
    def explode(_payload):
        raise RuntimeError("secret database password in a stack trace")

    monkeypatch.setattr("services.routers.incidents.validate_incident_payload", explode)
    with TestClient(app, raise_server_exceptions=False, headers=dict(client.headers)) as quiet_client:
        response = quiet_client.post("/api/incidents", json=NEW_INCIDENT)
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert "secret" not in response.text and "Traceback" not in response.text


def test_inventory_endpoints_keep_the_default_error_format(client):
    """The structured incident errors must not leak into the inventory API."""
    response = client.post("/inventory/orders/inbound", json={"sku_id": 1, "quantity": 1.5, "reference": "x", "warehouse": "LA"})
    assert response.status_code == 422
    assert "detail" in response.json()


# --- database constraints ---------------------------------------------------------


def test_database_rejects_values_outside_the_allowed_sets(incident_client, incident_engine):
    with Session(incident_engine) as session:
        session.add(Incident(title="t", description="d", category="bogus", status="open", origin="customer", branch="central"))
        with pytest.raises(Exception):
            session.commit()
    with incident_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM incident")).scalar() == 0
