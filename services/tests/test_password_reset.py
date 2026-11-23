"""AUTH-03: recuperación de contraseña (forgot/reset) y cambio con sesión (change), más el envío con Resend."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
import jwt
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services import accounts, mailer, security
from services.database import get_session
from services.main import app

PASSWORD = "correct-horse-42"
NEW_PASSWORD = "a-brand-new-pass-7"
SECRET = "test-only-secret-with-at-least-32-characters"
GENERIC = {"message": "If that address is registered, you will receive a reset link shortly."}
INVALID_LINK = {"detail": "This reset link is invalid or has expired. Request a new one."}


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    """Cada test parte de una configuración conocida y sin límite de reenvío."""
    monkeypatch.setenv("PASSWORD_RESET_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("FRONTEND_BASE_URL", "http://localhost:3001")
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Cliente sin sesión: cuentas en un TinyDB temporal e inventario en SQLite en memoria."""
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


@pytest.fixture
def outbox(monkeypatch):
    """Sustituye el envío real: guarda los correos que la API habría mandado."""
    sent: list[mailer.EmailMessage] = []
    monkeypatch.setattr("services.auth.send_email", lambda message: sent.append(message) or True)
    return sent


def register(api, email="alice@example.com", **extra):
    response = api.post("/users", json={"email": email, "password": PASSWORD, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def login(api, email="alice@example.com", password=PASSWORD):
    return api.post("/auth/login", json={"email": email, "password": password})


def headers_for(api, email="alice@example.com", password=PASSWORD):
    response = login(api, email, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def request_reset(api, outbox, email="alice@example.com"):
    """Pide el enlace y devuelve el token del último correo enviado."""
    before = len(outbox)
    assert api.post("/auth/forgot-password", json={"email": email}).status_code == 200
    assert len(outbox) == before + 1, "no se envió el correo"
    return re.search(r"token=([\w.\-]+)", outbox[-1].text).group(1)


# --- POST /auth/forgot-password -------------------------------------------------


def test_forgot_password_answers_identically_for_known_and_unknown_emails(api, outbox):
    register(api)
    known = api.post("/auth/forgot-password", json={"email": "alice@example.com"})
    unknown = api.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == GENERIC
    assert len(outbox) == 1  # solo la cuenta real recibe correo


def test_forgot_password_sends_the_reset_link_to_a_registered_address(api, outbox):
    user = register(api, name="Alice Ng")
    token = request_reset(api, outbox)
    message = outbox[0]
    assert message.to == "alice@example.com"
    assert f"http://localhost:3001/reset-password?token={token}" in message.text
    assert f"http://localhost:3001/reset-password?token={token}" in message.html
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["sub"] == user["id"] and claims["purpose"] == "password_reset" and claims["jti"]
    assert "Alice Ng" in message.text


def test_email_lookup_ignores_case_and_surrounding_spaces(api, outbox):
    register(api)
    assert api.post("/auth/forgot-password", json={"email": "ALICE@Example.com"}).status_code == 200
    assert len(outbox) == 1


def test_forgot_password_rejects_a_malformed_address_without_sending(api, outbox):
    assert api.post("/auth/forgot-password", json={"email": "not-an-email"}).status_code == 422
    assert outbox == []


def test_disabled_accounts_get_the_same_answer_but_no_email(api, outbox):
    user = register(api)
    accounts.update_user(user["id"], is_active=False)
    response = api.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert response.status_code == 200 and response.json() == GENERIC
    assert outbox == []


def test_cooldown_limits_how_often_one_account_can_be_emailed(api, outbox, monkeypatch):
    register(api)
    monkeypatch.setenv("PASSWORD_RESET_COOLDOWN_SECONDS", "60")
    first = api.post("/auth/forgot-password", json={"email": "alice@example.com"})
    second = api.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert len(outbox) == 1


def test_a_failed_delivery_does_not_change_the_response(api, monkeypatch):
    register(api)
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    response = api.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert response.status_code == 200 and response.json() == GENERIC


# --- POST /auth/reset-password --------------------------------------------------


def test_reset_updates_the_password_and_the_token_works_only_once(api, outbox):
    register(api)
    token = request_reset(api, outbox)
    done = api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    assert done.status_code == 200
    assert login(api, password=PASSWORD).status_code == 401
    assert login(api, password=NEW_PASSWORD).status_code == 200

    again = api.post("/auth/reset-password", json={"token": token, "new_password": "yet-another-pass-9"})
    assert again.status_code == 400 and again.json() == INVALID_LINK
    assert login(api, password=NEW_PASSWORD).status_code == 200  # el segundo intento no cambió nada


def test_a_newer_link_cancels_the_older_one(api, outbox):
    register(api)
    old = request_reset(api, outbox)
    new = request_reset(api, outbox)
    assert api.post("/auth/reset-password", json={"token": old, "new_password": NEW_PASSWORD}).status_code == 400
    assert api.post("/auth/reset-password", json={"token": new, "new_password": NEW_PASSWORD}).status_code == 200


def test_using_one_link_cancels_the_other_pending_links(api, outbox):
    register(api)
    first = request_reset(api, outbox)
    accounts_user = accounts.get_user_by_email("alice@example.com")
    # Un segundo enlace emitido directamente en el almacén, como si se hubiera pedido en otro momento.
    extra = security.create_reset_token(accounts_user.id)
    accounts.register_reset_request(accounts_user.id, extra.jti, extra.expires_at, 0)
    assert api.post("/auth/reset-password", json={"token": extra.token, "new_password": NEW_PASSWORD}).status_code == 200
    assert api.post("/auth/reset-password", json={"token": first, "new_password": "yet-another-pass-9"}).status_code == 400


def test_expired_tokens_are_rejected(api):
    user = register(api)
    now = datetime.now(timezone.utc)
    # 1) El JWT ya caducó aunque el registro del servidor siga vigente.
    accounts.register_reset_request(user["id"], "jti-expired-jwt", now + timedelta(minutes=30), 0)
    expired_jwt = jwt.encode(
        {"sub": user["id"], "purpose": "password_reset", "jti": "jti-expired-jwt", "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
        SECRET,
        algorithm="HS256",
    )
    # 2) El JWT es válido pero el registro del servidor ya caducó.
    accounts.register_reset_request(user["id"], "jti-expired-record", now - timedelta(minutes=1), 0)
    valid_jwt_expired_record = jwt.encode(
        {"sub": user["id"], "purpose": "password_reset", "jti": "jti-expired-record", "iat": now, "exp": now + timedelta(minutes=30)},
        SECRET,
        algorithm="HS256",
    )
    for token in (expired_jwt, valid_jwt_expired_record):
        response = api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
        assert response.status_code == 400 and response.json() == INVALID_LINK
    assert login(api, password=PASSWORD).status_code == 200


def test_the_token_lifetime_comes_from_the_environment(api, outbox, monkeypatch):
    register(api)
    monkeypatch.setenv("PASSWORD_RESET_EXPIRE_MINUTES", "15")
    token = request_reset(api, outbox)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert "15 minutes" in outbox[0].text


def test_forged_wrongly_purposed_and_unknown_tokens_are_rejected(api):
    user = register(api)
    now = datetime.now(timezone.utc)
    session_token = security.create_access_token(user["id"])
    forged = jwt.encode(
        {"sub": user["id"], "purpose": "password_reset", "jti": "forged-jti", "exp": now + timedelta(hours=1)},
        "x" * 40,
        algorithm="HS256",
    )
    signed_but_unknown = security.create_reset_token(user["id"]).token  # firma válida, pero el servidor no lo emitió
    for token in (session_token, forged, signed_but_unknown, "x" * 30):
        response = api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
        assert response.status_code == 400 and response.json() == INVALID_LINK, token[:20]
    assert login(api, password=PASSWORD).status_code == 200


def test_a_reset_token_never_works_as_a_session(api, outbox):
    register(api)
    token = request_reset(api, outbox)
    response = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_an_invalid_new_password_is_rejected_without_spending_the_token(api, outbox):
    register(api)
    token = request_reset(api, outbox)
    for bad in ("short", "x" * 73):
        assert api.post("/auth/reset-password", json={"token": token, "new_password": bad}).status_code == 422
    assert api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200


def test_a_reset_for_a_disabled_account_fails(api, outbox):
    user = register(api)
    token = request_reset(api, outbox)
    accounts.update_user(user["id"], is_active=False)
    assert api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400


def test_a_reset_for_a_deleted_account_fails(api, outbox):
    user = register(api)
    token = request_reset(api, outbox)
    assert accounts.delete_user(user["id"]) is True
    assert api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400


def test_a_reset_signs_out_sessions_issued_before_it(api, outbox):
    user = register(api)
    now = datetime.now(timezone.utc)
    old_session = jwt.encode(
        {"sub": user["id"], "iat": now - timedelta(minutes=10), "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256"
    )
    old_headers = {"Authorization": f"Bearer {old_session}"}
    assert api.get("/auth/me", headers=old_headers).status_code == 200

    token = request_reset(api, outbox)
    assert api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
    stale = api.get("/auth/me", headers=old_headers)
    assert stale.status_code == 401 and "password was changed" in stale.json()["detail"]
    assert api.get("/auth/me", headers=headers_for(api, password=NEW_PASSWORD)).status_code == 200


def test_the_store_keeps_only_a_hash_of_the_token_id_and_marks_it_used(api, outbox):
    register(api)
    token = request_reset(api, outbox)
    jti = jwt.decode(token, options={"verify_signature": False})["jti"]
    raw = api.store_path.read_text(encoding="utf-8")
    assert token not in raw and jti not in raw and NEW_PASSWORD not in raw
    (record,) = json.loads(raw)["password_resets"].values()
    assert record["jti_hash"] == security.hash_jti(jti) and record["used_at"] is None

    api.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    raw_after = api.store_path.read_text(encoding="utf-8")
    assert NEW_PASSWORD not in raw_after
    (record_after,) = json.loads(raw_after)["password_resets"].values()
    assert record_after["used_at"] is not None


# --- POST /auth/change-password -------------------------------------------------


def test_change_password_requires_a_session(api):
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert api.post("/auth/change-password", json=body).status_code == 401
    assert api.post("/auth/change-password", json=body, headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_change_password_rejects_a_wrong_current_password_with_400(api):
    register(api)
    headers = headers_for(api)
    response = api.post("/auth/change-password", json={"current_password": "not-my-password", "new_password": NEW_PASSWORD}, headers=headers)
    assert response.status_code == 400 and response.json() == {"detail": "Your current password is incorrect."}
    assert login(api, password=PASSWORD).status_code == 200


def test_change_password_updates_the_password_and_keeps_this_session_alive(api):
    register(api)
    headers = headers_for(api)
    response = api.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Your password was changed." and body["expires_in"] > 0
    assert login(api, password=PASSWORD).status_code == 401
    assert login(api, password=NEW_PASSWORD).status_code == 200
    assert api.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200


def test_change_password_signs_out_other_older_sessions(api):
    user = register(api)
    now = datetime.now(timezone.utc)
    other_device = jwt.encode(
        {"sub": user["id"], "iat": now - timedelta(minutes=30), "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256"
    )
    api.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers_for(api))
    assert api.get("/auth/me", headers={"Authorization": f"Bearer {other_device}"}).status_code == 401


def test_change_password_validates_the_new_password(api):
    register(api)
    headers = headers_for(api)
    same = api.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": PASSWORD}, headers=headers)
    assert same.status_code == 400 and "different" in same.json()["detail"]
    for bad in ("short", "x" * 73):
        assert api.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": bad}, headers=headers).status_code == 422
    assert login(api, password=PASSWORD).status_code == 200


def test_change_password_also_works_for_accounts_with_an_old_hash_format(api):
    import hashlib

    salt = "00" * 16
    digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), bytes.fromhex(salt), security.LEGACY_PBKDF2_ITERATIONS).hex()
    api.store_path.write_text(
        json.dumps({"_default": {"1": {"user_uuid": "legacy-id", "name": "Old", "email": "old@example.com", "password_hash": f"{salt}${digest}"}}}),
        encoding="utf-8",
    )
    headers = {"Authorization": f"Bearer {security.create_access_token('legacy-id')}"}
    response = api.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers)
    assert response.status_code == 200
    assert login(api, "old@example.com", NEW_PASSWORD).status_code == 200


# --- correo (Resend) ------------------------------------------------------------


def test_reset_email_has_the_link_the_expiry_and_a_mobile_friendly_layout():
    link = "http://localhost:3001/reset-password?token=abc.def-ghi"
    message = mailer.build_reset_email("alice@example.com", "Alice", link, 30)
    assert link in message.text and link in message.html
    assert "30 minutes" in message.text and "30 minutes" in message.html
    assert 'name="viewport"' in message.html and "max-width:480px" in message.html and "font-size:16px" in message.html
    assert message.to == "alice@example.com" and message.subject


def test_reset_email_escapes_the_name_and_link_in_html():
    message = mailer.build_reset_email("a@example.com", "<script>alert(1)</script>", 'http://x/?a="b"&c=<d>', 30)
    assert "<script>" not in message.html and "&lt;script&gt;" in message.html
    assert '"b"' not in message.html


def test_resend_receives_the_expected_request(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "TrackFlow <onboarding@resend.dev>")
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return httpx.Response(200, json={"id": "email-1"})

    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    message = mailer.build_reset_email("alice@example.com", "Alice", "http://localhost:3001/reset-password?token=t", 30)
    assert mailer.send_email(message) is True
    ((url, headers, body, timeout),) = calls
    assert url == "https://api.resend.com/emails" and headers["Authorization"] == "Bearer re_test_key"
    assert body["from"] == "TrackFlow <onboarding@resend.dev>" and body["to"] == ["alice@example.com"]
    assert body["subject"] == message.subject and body["html"] == message.html and body["text"] == message.text
    assert timeout > 0


def test_a_resend_error_is_logged_without_secrets_and_never_raised(monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_super_secret_key")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(403, json={"message": "Domain not verified for alice@example.com"}))
    message = mailer.build_reset_email("alice@example.com", "Alice", "http://localhost:3001/reset-password?token=SECRET-TOKEN-VALUE", 30)
    with caplog.at_level(logging.ERROR):
        assert mailer.send_email(message) is False
    assert "HTTP 403" in caplog.text
    for secret in ("re_super_secret_key", "SECRET-TOKEN-VALUE", "alice@example.com"):
        assert secret not in caplog.text


def test_network_failures_are_logged_and_never_raised(monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")))
    with caplog.at_level(logging.ERROR):
        assert mailer.send_email(mailer.build_reset_email("a@example.com", "", "http://x/y", 30)) is False
    assert "ConnectTimeout" in caplog.text


def test_console_mode_writes_the_link_to_the_log(monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    with caplog.at_level(logging.WARNING):
        assert mailer.send_email(mailer.build_reset_email("a@example.com", "", "http://x/reset?token=T", 30)) is True
    assert "http://x/reset?token=T" in caplog.text


def test_without_a_provider_nothing_is_sent_and_a_warning_is_logged(monkeypatch, caplog):
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: pytest.fail("no debería llamar a ningún servicio"))
    with caplog.at_level(logging.WARNING):
        assert mailer.send_email(mailer.build_reset_email("a@example.com", "", "http://x/y", 30)) is False
    assert "not configured" in caplog.text


def test_email_settings_are_validated_at_startup(monkeypatch):
    mailer.validate_email_config()  # sin proveedor: válido
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        mailer.validate_email_config()
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    mailer.validate_email_config()
    monkeypatch.setenv("EMAIL_PROVIDER", "pigeon")
    with pytest.raises(RuntimeError, match="EMAIL_PROVIDER"):
        mailer.validate_email_config()


def test_reset_settings_are_validated(monkeypatch):
    security.validate_security_config()
    for name, value in (
        ("PASSWORD_RESET_EXPIRE_MINUTES", "soon"),
        ("PASSWORD_RESET_EXPIRE_MINUTES", "0"),
        ("PASSWORD_RESET_EXPIRE_MINUTES", "5000"),
        ("PASSWORD_RESET_COOLDOWN_SECONDS", "-1"),
        ("PASSWORD_RESET_COOLDOWN_SECONDS", "often"),
    ):
        monkeypatch.setenv(name, value)
        with pytest.raises(RuntimeError, match=name):
            security.validate_security_config()
        monkeypatch.delenv(name)
    monkeypatch.setenv("PASSWORD_RESET_EXPIRE_MINUTES", "15")
    security.validate_security_config()
