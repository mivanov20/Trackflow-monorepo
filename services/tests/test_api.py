import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-only-secret-with-at-least-32-characters"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from services import auth
from services.database import get_session
from services.main import app

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        for table in reversed(SQLModel.metadata.sorted_tables):
            session.exec(table.delete())
        session.commit()

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def auth_headers(client):
    registered = client.post(
        "/auth/register",
        json={"name": "Test Operative", "email": "operative@example.com", "password": "correct-horse-42"},
    )
    assert registered.status_code == 201
    login = client.post(
        "/auth/login", json={"email": "operative@example.com", "password": "correct-horse-42"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_sku(client, headers, *, warehouse="LA"):
    response = client.post(
        "/inventory/products",
        headers=headers,
        json={
            "name": "Classic White Sneaker - Size 42",
            "sku": f"CLT-SNK-W-42-{warehouse}",
            "client_name": "PureStep Footwear",
            "category": "fashion",
            "warehouse": warehouse,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_registration_login_and_protected_routes(client, auth_headers):
    assert client.get("/inventory/products").status_code == 401
    assert client.get("/inventory/products", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/inventory/products", headers=auth_headers).status_code == 200
    duplicate = client.post(
        "/auth/register",
        json={"name": "Again", "email": "OPERATIVE@example.com", "password": "correct-horse-42"},
    )
    assert duplicate.status_code == 409
    assert client.post("/auth/login", json={"email": "operative@example.com", "password": "wrong-password"}).status_code == 401


def test_stock_is_computed_per_warehouse_and_exit_has_exact_error(client, auth_headers):
    la_sku = create_sku(client, auth_headers, warehouse="LA")
    zgz_sku = create_sku(client, auth_headers, warehouse="ZGZ")
    receipt = {"sku_id": la_sku["id"], "quantity": 8, "reference": "PO-2024-0098", "warehouse": "LA"}
    assert client.post("/inventory/orders/inbound", headers=auth_headers, json=receipt).status_code == 201
    assert client.post("/inventory/orders/inbound", headers=auth_headers, json={**receipt, "quantity": 5, "warehouse": "ZGZ", "sku_id": zgz_sku["id"]}).status_code == 201
    same_sku_other_warehouse = client.post(
        "/inventory/orders/inbound",
        headers=auth_headers,
        json={"sku_id": la_sku["id"], "quantity": 6, "reference": "PO-SAME-SKU-ZGZ", "warehouse": "ZGZ"},
    )
    assert same_sku_other_warehouse.status_code == 201
    assert client.get(f"/inventory/products/{la_sku['id']}", headers=auth_headers).json()["current_stock"] == 8
    assert client.get(f"/inventory/products/{zgz_sku['id']}", headers=auth_headers).json()["current_stock"] == 5

    fractional_entry = client.post(
        "/inventory/orders/inbound",
        headers=auth_headers,
        json={"sku_id": la_sku["id"], "quantity": 1.5, "reference": "PO-FRACTIONAL", "warehouse": "LA"},
    )
    assert fractional_entry.status_code == 422

    response = client.post(
        "/inventory/orders/outbound",
        headers=auth_headers,
        json={"sku_id": la_sku["id"], "quantity": 9, "exit_type": "dispatch", "tracking_number": "1Z123", "warehouse": "LA"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient stock for SKU 'CLT-SNK-W-42-LA'. Available: 8, requested: 9."


def test_dispatch_tracking_and_order_history(client, auth_headers):
    product = create_sku(client, auth_headers)
    receipt = client.post(
        "/inventory/orders/inbound",
        headers=auth_headers,
        json={"sku_id": product["id"], "quantity": 4, "reference": "GR-LA-0234", "warehouse": "LA"},
    ).json()
    assert receipt["order_type"] == "inbound"
    assert receipt["user_uuid"]

    missing_tracking = client.post(
        "/inventory/orders/outbound",
        headers=auth_headers,
        json={"sku_id": product["id"], "quantity": 1, "exit_type": "dispatch", "warehouse": "LA"},
    )
    assert missing_tracking.status_code == 422

    outbound = client.post(
        "/inventory/orders/outbound",
        headers=auth_headers,
        json={"sku_id": product["id"], "quantity": 1, "exit_type": "dispatch", "tracking_number": "1Z999AA10123456784", "warehouse": "LA"},
    )
    assert outbound.status_code == 201
    loss = client.post(
        "/inventory/orders/outbound",
        headers=auth_headers,
        json={"sku_id": product["id"], "quantity": 1, "exit_type": "loss", "tracking_number": None, "warehouse": "LA"},
    )
    assert loss.status_code == 201
    assert loss.json()["tracking_number"] is None
    history = client.get("/inventory/orders", headers=auth_headers).json()
    assert {order["order_type"] for order in history} == {"inbound", "outbound"}
    assert all(order["sku"]["name"] == product["name"] and order["user_uuid"] for order in history)