import json

import pytest
from fastapi.testclient import TestClient

from supplier_directory.main import app
from supplier_directory.repository import SERVICE_ROOT

SEED_COUNT = len(json.loads((SERVICE_ROOT / "data" / "suppliers_seed.json").read_text(encoding="utf-8")))

NEW_SUPPLIER = {
    "name": "Test Supplier",
    "country": "Portugal",
    "categories": ["Packaging"],
    "unit_rate_eur": 1.5,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "suppliers.json"))
    with TestClient(app) as test_client:
        yield test_client


def test_database_starts_seeded(client):
    assert len(client.get("/suppliers").json()) == SEED_COUNT


def test_create_returns_201_with_id_and_system_timestamp(client):
    response = client.post("/suppliers", json=NEW_SUPPLIER)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["status"] == "active"
    assert body["updated_at"]


def test_client_cannot_set_updated_at(client):
    response = client.post("/suppliers", json={**NEW_SUPPLIER, "updated_at": "2000-01-01T00:00:00Z"})
    assert response.status_code == 201
    assert not response.json()["updated_at"].startswith("2000")


@pytest.mark.parametrize(
    "patch",
    [
        {"country": ""},
        {"name": ""},
        {"categories": []},
        {"categories": ["Unicorns"]},
        {"unit_rate_eur": 0},
        {"unit_rate_eur": -3},
        {"status": "retired"},
    ],
)
def test_create_rejects_invalid_input(client, patch):
    assert client.post("/suppliers", json={**NEW_SUPPLIER, **patch}).status_code == 422


def test_create_rejects_missing_country(client):
    body = {k: v for k, v in NEW_SUPPLIER.items() if k != "country"}
    assert client.post("/suppliers", json=body).status_code == 422


def test_list_filters_by_country_and_category(client):
    spain = client.get("/suppliers", params={"country": "Spain"}).json()
    assert spain and all(s["country"] == "Spain" for s in spain)

    pallets = client.get("/suppliers", params={"category": "Pallets"}).json()
    assert pallets and all("Pallets" in s["categories"] for s in pallets)

    both = client.get("/suppliers", params={"country": "spain", "category": "pallets"}).json()
    assert len(both) == 1 and both[0]["name"] == "Iberpalet S.A."

    assert client.get("/suppliers", params={"country": "Atlantis"}).json() == []


def test_get_by_id_and_404(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.get(f"/suppliers/{created['id']}").json() == created
    assert client.get("/suppliers/99999").status_code == 404


def test_rate_update_records_timestamp(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    response = client.patch(f"/suppliers/{created['id']}/rate", json={"unit_rate_eur": 2.25})
    assert response.status_code == 200
    body = response.json()
    assert body["unit_rate_eur"] == 2.25
    assert body["updated_at"] > created["updated_at"]


@pytest.mark.parametrize("rate", [0, -1])
def test_rate_update_rejects_non_positive(client, rate):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.patch(f"/suppliers/{created['id']}/rate", json={"unit_rate_eur": rate}).status_code == 422
    assert client.get(f"/suppliers/{created['id']}").json()["unit_rate_eur"] == 1.5


def test_rate_update_404(client):
    assert client.patch("/suppliers/99999/rate", json={"unit_rate_eur": 2}).status_code == 404


def test_status_update(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    response = client.patch(f"/suppliers/{created['id']}/status", json={"status": "suspended"})
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"


def test_status_update_rejects_other_values_and_404(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.patch(f"/suppliers/{created['id']}/status", json={"status": "banned"}).status_code == 422
    assert client.patch("/suppliers/99999/status", json={"status": "active"}).status_code == 404


def test_delete_and_404(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.delete(f"/suppliers/{created['id']}").status_code == 204
    assert client.get(f"/suppliers/{created['id']}").status_code == 404
    assert client.delete(f"/suppliers/{created['id']}").status_code == 404


def test_data_persists_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "suppliers.json"))
    with TestClient(app) as first:
        created = first.post("/suppliers", json=NEW_SUPPLIER).json()
    with TestClient(app) as second:
        assert second.get(f"/suppliers/{created['id']}").json()["name"] == "Test Supplier"
        assert len(second.get("/suppliers").json()) == SEED_COUNT + 1


def test_categories_endpoint(client):
    assert "Pallets" in client.get("/categories").json()
