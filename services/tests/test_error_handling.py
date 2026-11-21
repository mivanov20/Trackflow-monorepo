"""Errores de infraestructura: respuestas limpias, códigos correctos y sin fugas de detalles internos."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session

from services.main import app

from conftest import NEW_INCIDENT

SECRET = "password authentication failed for user secret_user at db.internal"
ORIGIN = "http://localhost:3001"


def _db_failure(*_args, **_kwargs):
    raise OperationalError("SELECT 1", {}, Exception(SECRET))


def _quiet(client: TestClient) -> TestClient:
    """Cliente que devuelve las respuestas 500 en lugar de relanzar la excepción."""
    return TestClient(app, raise_server_exceptions=False, headers=dict(client.headers))


def _assert_no_leak(response) -> None:
    assert "secret_user" not in response.text
    assert "db.internal" not in response.text
    assert "Traceback" not in response.text


def _sku_payload() -> dict:
    return {"name": "Mug", "sku": "MUG-1", "client_name": "Acme", "category": "fashion", "warehouse": "LA"}


def test_inventory_commit_failure_returns_503_and_rolls_back(incident_client, monkeypatch):
    rolled_back = []
    monkeypatch.setattr(Session, "commit", _db_failure)
    monkeypatch.setattr(Session, "rollback", lambda self: rolled_back.append(True))
    response = incident_client.post("/inventory/products", json=_sku_payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "The service is temporarily unavailable. Please try again in a moment."}
    assert rolled_back
    _assert_no_leak(response)


def test_inventory_integrity_error_returns_409(incident_client, monkeypatch):
    def conflict(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception(SECRET))

    monkeypatch.setattr(Session, "commit", conflict)
    response = incident_client.post("/inventory/products", json=_sku_payload())
    assert response.status_code == 409
    assert "detail" in response.json()
    _assert_no_leak(response)


def test_inventory_read_failure_returns_503_with_cors(incident_client, monkeypatch):
    monkeypatch.setattr(Session, "exec", _db_failure)
    response = incident_client.get("/inventory/products", headers={"Origin": ORIGIN})
    assert response.status_code == 503
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    _assert_no_leak(response)


def test_unexpected_inventory_error_returns_generic_500_json_with_cors(incident_client, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("services.routers.inventory.select", explode)
    with _quiet(incident_client) as client:
        response = client.get("/inventory/products", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong on our side. Please try again in a moment."}
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    _assert_no_leak(response)


def test_corrupted_account_store_returns_503(incident_client, tmp_path):
    (tmp_path / "users.json").write_text("{not json", encoding="utf-8")
    response = incident_client.post("/auth/login", json={"email": "operative@example.com", "password": "x"})
    assert response.status_code == 503
    assert response.json()["detail"].startswith("The account service is temporarily unavailable")
    _assert_no_leak(response)


def test_incident_read_failure_returns_structured_503(incident_client, monkeypatch):
    monkeypatch.setattr(Session, "exec", _db_failure)
    response = incident_client.get("/api/incidents")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    _assert_no_leak(response)


def test_incident_commit_failure_returns_structured_503(incident_client, monkeypatch):
    monkeypatch.setattr(Session, "commit", _db_failure)
    response = incident_client.post("/api/incidents", json=NEW_INCIDENT)
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    _assert_no_leak(response)
