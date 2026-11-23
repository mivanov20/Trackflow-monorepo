"""AUTH-01: usuarios, perfiles, login JWT y restricción de rutas (401 sin token, 403 sobre cuentas ajenas)."""

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services import accounts, security
from services.database import get_session
from services.accounts import Role
from services.main import app

PASSWORD = "correct-horse-42"
SECRET = "test-only-secret-with-at-least-32-characters"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Cliente sin sesión: cuentas en un TinyDB temporal e inventario/incidentes en SQLite en memoria."""
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def session_for_test():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_for_test
    with TestClient(app) as client:
        client.store_path = tmp_path / "users.json"
        yield client
    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)


def register(api, email="alice@example.com", **extra):
    response = api.post("/users", json={"email": email, "password": PASSWORD, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def auth(api, email):
    response = api.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_admin(email):
    accounts.update_user(accounts.get_user_by_email(email).id, role=Role.admin)


# --- registro, roles y contraseñas ----------------------------------------------


def test_register_creates_user_with_default_role_and_linked_profile(api):
    created = register(api, name="Alice Ng", phone="+1 555 0100", address="1 Main St")
    assert created["role"] == "user" and created["is_active"] is True
    assert created["profile"] == {
        "id": created["profile"]["id"],
        "user_id": created["id"],
        "name": "Alice Ng",
        "phone": "+1 555 0100",
        "address": "1 Main St",
    }
    assert "hashed_password" not in created and "password" not in created


def test_profile_fields_are_stored_on_profile_not_on_user(api):
    register(api, name="Alice Ng", phone="+1 555 0100", address="1 Main St")
    stored = json.loads(api.store_path.read_text(encoding="utf-8"))
    (user_doc,) = stored["users"].values()
    (profile_doc,) = stored["profiles"].values()
    assert not {"name", "phone", "address"} & set(user_doc)
    assert {"id", "email", "hashed_password", "is_active", "role", "created_at"} <= set(user_doc)
    assert profile_doc["user_id"] == user_doc["id"] and profile_doc["name"] == "Alice Ng"


def test_password_is_hashed_with_bcrypt_and_never_stored_in_plain_text(api):
    register(api)
    raw = api.store_path.read_text(encoding="utf-8")
    assert PASSWORD not in raw
    (user_doc,) = json.loads(raw)["users"].values()
    assert user_doc["hashed_password"].startswith("$2")
    assert security.verify_password(PASSWORD, user_doc["hashed_password"]) == (True, False)


def test_registration_cannot_choose_a_role(api):
    response = api.post("/users", json={"email": "evil@example.com", "password": PASSWORD, "role": "admin"})
    assert response.status_code == 422
    response = api.post("/users", json={"email": "evil@example.com", "password": PASSWORD, "is_active": True})
    assert response.status_code == 422
    assert accounts.get_user_by_email("evil@example.com") is None


def test_duplicate_email_is_rejected_case_insensitively(api):
    register(api, "alice@example.com")
    again = api.post("/users", json={"email": "ALICE@Example.com", "password": PASSWORD})
    assert again.status_code == 409


def test_password_rules_are_enforced(api):
    assert api.post("/users", json={"email": "a@example.com", "password": "short"}).status_code == 422
    too_long = api.post("/users", json={"email": "a@example.com", "password": "x" * 73})
    assert too_long.status_code == 422  # bcrypt solo procesa 72 bytes


def test_role_enum_rejects_anything_but_admin_manager_user():
    assert {role.value for role in Role} == {"admin", "manager", "user"}
    with pytest.raises(ValueError):
        Role("superuser")


def test_legacy_register_alias_still_works(api):
    response = api.post("/auth/register", json={"name": "Old Client", "email": "old@example.com", "password": PASSWORD})
    assert response.status_code == 201
    assert response.json()["profile"]["name"] == "Old Client"


# --- login y token --------------------------------------------------------------


def test_login_returns_a_signed_jwt_carrying_the_user_id(api):
    created = register(api, name="Alice Ng")
    response = api.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == created["id"] and claims["exp"] > claims["iat"]
    assert body["token_type"] == "bearer" and body["user"]["name"] == "Alice Ng"
    assert "hashed_password" not in json.dumps(body)


def test_wrong_password_and_unknown_email_get_the_same_401(api):
    register(api)
    wrong = api.post("/auth/login", json={"email": "alice@example.com", "password": "not-the-password"})
    unknown = api.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_token_expiry_comes_from_the_environment(api, monkeypatch):
    register(api)
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "5")
    body = api.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD}).json()
    claims = jwt.get_unverified_claims(body["access_token"])
    assert claims["exp"] - claims["iat"] == 300 and body["expires_in"] == 300


def test_swagger_form_login_returns_a_working_token(api):
    register(api)
    response = api.post("/auth/token", data={"username": "alice@example.com", "password": PASSWORD})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert api.get("/profiles/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert api.post("/auth/token", data={"username": "alice@example.com", "password": "bad"}).status_code == 401
    assert api.post("/auth/token", data={"username": "alice@example.com"}).status_code == 422


def test_invalid_expired_and_unsigned_tokens_are_rejected(api):
    created = register(api)
    now = datetime.now(timezone.utc)
    expired = jwt.encode({"sub": created["id"], "exp": now - timedelta(minutes=1)}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": created["id"], "exp": now + timedelta(hours=1)}, "x" * 40, algorithm="HS256")
    no_expiry = jwt.encode({"sub": created["id"]}, SECRET, algorithm="HS256")
    unknown_user = jwt.encode({"sub": "ghost", "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256")
    for token in (expired, forged, no_expiry, unknown_user, "not-a-jwt"):
        response = api.get("/profiles/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token


def test_security_settings_are_validated(monkeypatch):
    security.validate_security_config()
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError):
        security.validate_security_config()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "none")
    with pytest.raises(RuntimeError):
        security.validate_security_config()
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "soon")
    with pytest.raises(RuntimeError):
        security.validate_security_config()


# --- rutas protegidas (401) -----------------------------------------------------

PROTECTED = [
    ("GET", "/auth/me"),
    ("GET", "/users"),
    ("GET", "/users/someone"),
    ("PUT", "/users/someone"),
    ("DELETE", "/users/someone"),
    ("GET", "/profiles/me"),
    ("PUT", "/profiles/me"),
    ("GET", "/inventory/products"),
    ("GET", "/inventory/products/1"),
    ("POST", "/inventory/products"),
    ("POST", "/inventory/orders/inbound"),
    ("POST", "/inventory/orders/outbound"),
    ("GET", "/inventory/orders"),
    ("GET", "/api/incidents"),
    ("POST", "/api/incidents"),
    ("GET", "/api/incidents/summary"),
    ("GET", "/api/incidents/1"),
    ("PATCH", "/api/incidents/1/status"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_routes_return_401_without_a_valid_token(api, method, path):
    assert api.request(method, path, json={}).status_code == 401
    assert api.request(method, path, json={}, headers={"Authorization": "Bearer garbage"}).status_code == 401


@pytest.mark.parametrize(("method", "path"), [("GET", "/health"), ("POST", "/auth/login"), ("POST", "/users")])
def test_public_routes_do_not_demand_a_token(api, method, path):
    assert api.request(method, path, json={}).status_code != 401 or path == "/auth/login"


def test_existing_routes_still_work_with_a_valid_token(api):
    register(api)
    make_admin("alice@example.com")  # self-registration gives role `user`; the operational APIs are for staff
    headers = auth(api, "alice@example.com")
    assert api.get("/inventory/products", headers=headers).status_code == 200
    assert api.get("/inventory/orders", headers=headers).status_code == 200
    assert api.get("/api/incidents", headers=headers).status_code == 200
    assert api.get("/api/incidents/summary", headers=headers).status_code == 200


# --- 403: cuentas ajenas ---------------------------------------------------------


def test_a_user_cannot_read_update_or_delete_another_account(api):
    alice, bob = register(api, "alice@example.com"), register(api, "bob@example.com")
    alice_headers = auth(api, "alice@example.com")
    assert api.get(f"/users/{bob['id']}", headers=alice_headers).status_code == 403
    assert api.put(f"/users/{bob['id']}", json={"email": "hacked@example.com"}, headers=alice_headers).status_code == 403
    assert api.delete(f"/users/{bob['id']}", headers=alice_headers).status_code == 403
    assert api.get("/users/does-not-exist", headers=alice_headers).status_code == 403  # no revela qué ids existen
    assert accounts.get_user_by_id(bob["id"]).email == "bob@example.com"
    assert api.get(f"/users/{alice['id']}", headers=alice_headers).status_code == 200


def test_listing_all_users_is_admin_only_and_hides_hashes(api):
    register(api, "alice@example.com")
    register(api, "root@example.com")
    make_admin("root@example.com")
    assert api.get("/users", headers=auth(api, "alice@example.com")).status_code == 403
    listing = api.get("/users", headers=auth(api, "root@example.com"))
    assert listing.status_code == 200 and len(listing.json()) == 2
    assert "hashed_password" not in listing.text


def test_only_an_admin_can_change_roles_or_account_status(api):
    alice, bob = register(api, "alice@example.com"), register(api, "bob@example.com")
    register(api, "root@example.com")
    make_admin("root@example.com")
    alice_headers, root_headers = auth(api, "alice@example.com"), auth(api, "root@example.com")

    assert api.put(f"/users/{alice['id']}", json={"role": "admin"}, headers=alice_headers).status_code == 403
    assert api.put(f"/users/{alice['id']}", json={"is_active": False}, headers=alice_headers).status_code == 403
    promoted = api.put(f"/users/{bob['id']}", json={"role": "manager"}, headers=root_headers)
    assert promoted.status_code == 200 and promoted.json()["role"] == "manager"
    assert api.put(f"/users/{bob['id']}", json={"role": "superuser"}, headers=root_headers).status_code == 422


def test_users_update_their_own_credentials(api):
    alice, _ = register(api, "alice@example.com"), register(api, "bob@example.com")
    headers = auth(api, "alice@example.com")
    renamed = api.put(f"/users/{alice['id']}", json={"email": "Alice.New@example.com"}, headers=headers)
    assert renamed.status_code == 200 and renamed.json()["email"] == "alice.new@example.com"
    assert api.put(f"/users/{alice['id']}", json={"email": "bob@example.com"}, headers=headers).status_code == 409
    assert api.put(f"/users/{alice['id']}", json={}, headers=headers).status_code == 422

    changed = api.put(f"/users/{alice['id']}", json={"password": "a-brand-new-pass-1"}, headers=headers)
    assert changed.status_code == 200
    assert api.post("/auth/login", json={"email": "alice.new@example.com", "password": PASSWORD}).status_code == 401
    assert api.post("/auth/login", json={"email": "alice.new@example.com", "password": "a-brand-new-pass-1"}).status_code == 200


def test_admin_can_manage_any_account_and_gets_404_for_unknown_ids(api):
    alice = register(api, "alice@example.com")
    register(api, "root@example.com")
    make_admin("root@example.com")
    root_headers = auth(api, "root@example.com")
    assert api.get(f"/users/{alice['id']}", headers=root_headers).status_code == 200
    assert api.put(f"/users/{alice['id']}", json={"email": "a2@example.com"}, headers=root_headers).status_code == 200
    assert api.get("/users/ghost", headers=root_headers).status_code == 404
    assert api.put("/users/ghost", json={"email": "x@example.com"}, headers=root_headers).status_code == 404
    assert api.delete("/users/ghost", headers=root_headers).status_code == 404
    assert api.delete(f"/users/{alice['id']}", headers=root_headers).status_code == 204


def test_disabled_accounts_cannot_log_in_or_use_existing_tokens(api):
    alice = register(api, "alice@example.com")
    register(api, "root@example.com")
    make_admin("root@example.com")
    alice_headers = auth(api, "alice@example.com")
    api.put(f"/users/{alice['id']}", json={"is_active": False}, headers=auth(api, "root@example.com"))
    assert api.get("/profiles/me", headers=alice_headers).status_code == 403
    assert api.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD}).status_code == 403


def test_the_last_active_admin_cannot_be_removed(api):
    root = register(api, "root@example.com")
    make_admin("root@example.com")
    headers = auth(api, "root@example.com")
    assert api.put(f"/users/{root['id']}", json={"role": "user"}, headers=headers).status_code == 409
    assert api.put(f"/users/{root['id']}", json={"is_active": False}, headers=headers).status_code == 409
    assert api.delete(f"/users/{root['id']}", headers=headers).status_code == 409
    register(api, "second@example.com")
    make_admin("second@example.com")
    assert api.put(f"/users/{root['id']}", json={"role": "user"}, headers=headers).status_code == 200


# --- perfiles --------------------------------------------------------------------


def test_profile_me_reads_and_updates_only_the_callers_profile(api):
    register(api, "alice@example.com", name="Alice")
    register(api, "bob@example.com", name="Bob")
    alice_headers = auth(api, "alice@example.com")
    assert api.get("/profiles/me", headers=alice_headers).json()["name"] == "Alice"

    updated = api.put(
        "/profiles/me", json={"name": "Alice Ng", "phone": "+34 600 000 000", "address": "Calle 1"}, headers=alice_headers
    )
    assert updated.status_code == 200 and updated.json()["phone"] == "+34 600 000 000"
    assert api.get("/profiles/me", headers=auth(api, "bob@example.com")).json()["name"] == "Bob"

    # No hay forma de apuntar a otro perfil: user_id/id no se aceptan en el cuerpo.
    assert api.put("/profiles/me", json={"user_id": "someone-else", "name": "X"}, headers=alice_headers).status_code == 422
    assert api.put("/profiles/me", json={}, headers=alice_headers).status_code == 422
    assert api.put("/profiles/me", json={"name": ""}, headers=alice_headers).status_code == 422


def test_deleting_a_user_removes_the_profile_and_invalidates_the_token(api):
    alice = register(api, "alice@example.com")
    headers = auth(api, "alice@example.com")
    assert api.delete(f"/users/{alice['id']}", headers=headers).status_code == 204
    assert accounts.get_user_by_id(alice["id"]) is None
    assert accounts.get_profile_by_user_id(alice["id"]) is None
    assert api.get("/profiles/me", headers=headers).status_code == 401


# --- almacenamiento y migración ---------------------------------------------------


def test_users_and_profiles_stay_out_of_postgresql():
    tables = set(SQLModel.metadata.tables)
    assert not tables & {"user", "users", "profile", "profiles", "account", "accounts"}
    assert {"sku", "stockentry", "stockexit", "incident"} <= tables


def test_concurrent_registrations_with_one_email_create_a_single_account(api):
    results = []

    def attempt():
        try:
            accounts.create_user("race@example.com", PASSWORD)
            results.append("ok")
        except accounts.EmailAlreadyRegisteredError:
            results.append("duplicate")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert results.count("ok") == 1 and results.count("duplicate") == 7
    assert len(accounts.list_users()) == 1


def test_legacy_accounts_are_migrated_and_rehashed_on_first_login(api):
    salt = "00" * 16
    digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), bytes.fromhex(salt), security.LEGACY_PBKDF2_ITERATIONS).hex()
    api.store_path.write_text(
        json.dumps(
            {
                "_default": {
                    "1": {"user_uuid": "legacy-id", "name": "Old Operative", "email": "old@example.com", "password_hash": f"{salt}${digest}"}
                }
            }
        ),
        encoding="utf-8",
    )
    response = api.post("/auth/login", json={"email": "old@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert jwt.get_unverified_claims(response.json()["access_token"])["sub"] == "legacy-id"  # inventario sigue enlazado

    stored = json.loads(api.store_path.read_text(encoding="utf-8"))
    assert stored.get("_default", {}) == {}
    (user_doc,) = stored["users"].values()
    assert user_doc["hashed_password"].startswith("$2") and user_doc["role"] == "user"
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert api.get("/profiles/me", headers=headers).json()["name"] == "Old Operative"
    assert api.post("/auth/login", json={"email": "old@example.com", "password": PASSWORD}).status_code == 200


def test_unreadable_account_store_returns_a_clean_503(api):
    api.store_path.write_text("{not json", encoding="utf-8")
    response = api.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert response.status_code == 503
    assert response.json() == {"detail": "The account service is temporarily unavailable. Please try again in a moment."}


# --- GET /auth/me (usado por el frontend) -------------------------------------------


def test_auth_me_returns_email_from_user_and_data_from_profile(api):
    created = register(api, "alice@example.com", name="Alice Ng", phone="+1 555 0100", address="1 Main St")
    me = api.get("/auth/me", headers=auth(api, "alice@example.com"))
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == created["id"] and body["email"] == "alice@example.com" and body["role"] == "user"
    assert body["profile"]["name"] == "Alice Ng" and body["profile"]["phone"] == "+1 555 0100"
    assert "hashed_password" not in me.text


def test_auth_me_reflects_profile_updates(api):
    register(api, "alice@example.com", name="Alice")
    headers = auth(api, "alice@example.com")
    api.put("/profiles/me", json={"name": "Alice Ng", "address": "Calle 1"}, headers=headers)
    assert api.get("/auth/me", headers=headers).json()["profile"]["address"] == "Calle 1"


def test_cors_allows_both_frontend_origins_and_the_methods_they_use(api):
    for origin in ("http://localhost:3001", "http://localhost:3000"):
        preflight = api.options(
            "/profiles/me",
            headers={"Origin": origin, "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"},
        )
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == origin
