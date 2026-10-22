import time

import pytest
from fastapi.testclient import TestClient

from supplier_directory.main import app
from supplier_directory.seed import SUPPLIERS_SEED

SEED_COUNT = len(SUPPLIERS_SEED)

NEW_SUPPLIER = {
    "name": "Test Carrier",
    "country": "Spain",
    "categories": ["carrier_last_mile"],
    "rate_per_shipment": 5.5,
    "currency": "EUR",
    "status": "active",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "suppliers.json"))
    with TestClient(app) as test_client:
        yield test_client


def test_database_starts_seeded(client):
    assert len(client.get("/suppliers").json()) == SEED_COUNT == 15


def test_create_returns_201_with_id_and_system_timestamp(client):
    response = client.post("/suppliers", json=NEW_SUPPLIER)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["updated_at"]
    assert body["service_zone"] is None and body["contact_email"] is None and body["notes"] is None


def test_create_accepts_optional_fields(client):
    body = {**NEW_SUPPLIER, "service_zone": "Aragón", "contact_email": "ops@example.es", "notes": "Pilot"}
    created = client.post("/suppliers", json=body).json()
    assert (created["service_zone"], created["contact_email"], created["notes"]) == ("Aragón", "ops@example.es", "Pilot")


def test_client_cannot_set_updated_at(client):
    response = client.post("/suppliers", json={**NEW_SUPPLIER, "updated_at": "2000-01-01T00:00:00Z"})
    assert response.status_code == 201
    assert not response.json()["updated_at"].startswith("2000")


@pytest.mark.parametrize(
    "patch",
    [
        {"name": ""},
        {"country": "Portugal"},
        {"categories": []},
        {"categories": ["unicorns"]},
        {"rate_per_shipment": 0},
        {"rate_per_shipment": -3},
        {"status": "retired"},
        {"currency": "USD"},  # Spain must be EUR
        {"contact_email": "not-an-email"},
        {"currency": "GBP"},
    ],
)
def test_create_rejects_invalid_input(client, patch):
    assert client.post("/suppliers", json={**NEW_SUPPLIER, **patch}).status_code == 422


def test_usa_supplier_must_use_usd(client):
    usa = {**NEW_SUPPLIER, "country": "USA", "currency": "USD"}
    assert client.post("/suppliers", json=usa).status_code == 201
    assert client.post("/suppliers", json={**usa, "currency": "EUR"}).status_code == 422


@pytest.mark.parametrize("missing", ["name", "country", "categories", "rate_per_shipment", "currency", "status"])
def test_create_rejects_missing_required_field(client, missing):
    body = {k: v for k, v in NEW_SUPPLIER.items() if k != missing}
    assert client.post("/suppliers", json=body).status_code == 422


def test_dual_category_carrier_is_valid(client):
    body = {**NEW_SUPPLIER, "categories": ["carrier_last_mile", "carrier_international"]}
    assert client.post("/suppliers", json=body).json()["categories"] == ["carrier_last_mile", "carrier_international"]


def test_list_filters_by_country_and_category(client):
    usa = client.get("/suppliers", params={"country": "USA"}).json()
    spain = client.get("/suppliers", params={"country": "Spain"}).json()
    assert len(usa) == 9 and all(s["country"] == "USA" for s in usa)
    assert len(spain) == 6 and all(s["country"] == "Spain" for s in spain)

    international = client.get("/suppliers", params={"category": "carrier_international"}).json()
    assert {s["name"] for s in international} == {"DHL Express USA", "DHL Express España"}

    spain_carriers = client.get("/suppliers", params={"country": "spain", "category": "carrier_last_mile"}).json()
    assert {s["name"] for s in spain_carriers} == {"MRW España", "SEUR", "DHL Express España", "Nacex"}

    assert client.get("/suppliers", params={"country": "Atlantis"}).json() == []


def test_get_by_id_and_404(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.get(f"/suppliers/{created['id']}").json() == created
    assert client.get("/suppliers/99999").status_code == 404


def test_rate_update_records_timestamp(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    time.sleep(0.01)
    response = client.patch(f"/suppliers/{created['id']}/rate", json={"rate_per_shipment": 6.25})
    assert response.status_code == 200
    body = response.json()
    assert body["rate_per_shipment"] == 6.25
    assert body["updated_at"] > created["updated_at"]


def test_status_change_does_not_touch_rate_timestamp(client):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    time.sleep(0.01)
    body = client.patch(f"/suppliers/{created['id']}/status", json={"status": "suspended"}).json()
    assert body["status"] == "suspended"
    assert body["updated_at"] == created["updated_at"]


@pytest.mark.parametrize("rate", [0, -1])
def test_rate_update_rejects_non_positive(client, rate):
    created = client.post("/suppliers", json=NEW_SUPPLIER).json()
    assert client.patch(f"/suppliers/{created['id']}/rate", json={"rate_per_shipment": rate}).status_code == 422
    assert client.get(f"/suppliers/{created['id']}").json()["rate_per_shipment"] == 5.5


def test_rate_update_404(client):
    assert client.patch("/suppliers/99999/rate", json={"rate_per_shipment": 2}).status_code == 404


def test_suspension_keeps_the_supplier(client):
    laser_ship = next(s for s in client.get("/suppliers").json() if s["name"] == "Laser Ship")
    assert laser_ship["status"] == "suspended"
    reactivated = client.patch(f"/suppliers/{laser_ship['id']}/status", json={"status": "active"}).json()
    assert reactivated["status"] == "active"


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
        created = first.post("/suppliers", json={**NEW_SUPPLIER, "name": "Persistent Carrier"}).json()
    with TestClient(app) as second:
        assert second.get(f"/suppliers/{created['id']}").json()["name"] == "Persistent Carrier"
        assert len(second.get("/suppliers").json()) == SEED_COUNT + 1


def test_categories_endpoint_lists_the_eight_valid_values(client):
    assert client.get("/categories").json() == [
        "carrier_last_mile",
        "carrier_international",
        "warehouse_supplies",
        "packaging_materials",
        "reverse_logistics",
        "fleet_maintenance",
        "it_and_wms_software",
        "cleaning_and_facilities",
    ]
