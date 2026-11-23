"""Audit probe: calls EVERY HTTP route of the API with NO credentials and prints what answers.

    python scripts/security/route_auth_probe.py            # table + exit code 1 if an unexpected route is public

A route is PUBLIC when it answers anything other than 401/403 (or 404/405 for a probe with no valid path). The expected public routes are listed in
`EXPECTED_PUBLIC` with the reason; anything else public is a finding. This is the reproducible evidence for OWASP A01 (broken access control) on the backend.
It uses a throwaway in-memory database and a throwaway JWT secret: nothing real is touched.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET", "route-probe-only-secret-with-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("AUTH_DB_PATH", str(ROOT / "services" / "data" / "route-probe-users.json"))

EXPECTED_PUBLIC = {
    ("POST", "/auth/login"): "sign in",
    ("POST", "/auth/token"): "Swagger form login",
    ("POST", "/auth/register"): "self-registration (role user)",
    ("POST", "/auth/forgot-password"): "password recovery request",
    ("POST", "/auth/reset-password"): "password recovery with an emailed token",
    ("POST", "/users"): "self-registration (role user)",
    ("POST", "/telemetry/events"): "client telemetry, sent before sign-in too (login failures)",
    ("GET", "/health"): "liveness probe, returns no data",
}
SKIP_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def main() -> int:
    from fastapi.routing import APIRoute
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel
    from fastapi.testclient import TestClient

    from services import database
    from services.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    database.engine = engine
    from sqlmodel import Session

    def session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[database.get_session] = session
    rows, findings = [], []
    client = TestClient(app, raise_server_exceptions=False)
    # The router tree comes from the OpenAPI document (it follows nested routers); routes hidden from the schema are added by hand.
    operations = {(method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}}
    operations |= {("POST", "/auth/token"), ("GET", "/health")}
    for method, route_path in sorted(operations, key=lambda o: (o[1], o[0])):
        if route_path.startswith(SKIP_PREFIXES):
            continue
        path = re.sub(r"\{[^}]+\}", "1", route_path)
        response = client.request(method, path, json={})
        public = response.status_code not in (401, 403)
        expected = EXPECTED_PUBLIC.get((method, route_path))
        rows.append((method, route_path, response.status_code, "PUBLIC (expected: " + expected + ")" if public and expected else "PUBLIC !!" if public else "protected"))
        if public and not expected:
            findings.append((method, route_path, response.status_code))
    width = max(len(r[1]) for r in rows)
    for method, path, code, verdict in rows:
        print(f"{method:6} {path:<{width}}  {code}  {verdict}")
    print(f"\n{len(rows)} routes probed, {len(findings)} unexpected public route(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
