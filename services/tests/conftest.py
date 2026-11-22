"""Shared fixtures for the incident tests (the inventory tests keep their own fixtures)."""

import os
import sys
from pathlib import Path

# Set before `services` is imported: the database module reads these at import time.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
os.environ.setdefault("BCRYPT_ROUNDS", "4")  # tests: bcrypt rápido
os.environ.setdefault("RFP_CHECKPOINT_DB", ":memory:")  # the approval checkpoints of the tests never touch a file

# Make the repo-level scripts/ folder importable (seed_incidents lives there).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services.database import get_session
from services.main import app


@pytest.fixture
def incident_engine():
    """A fresh in-memory database with every table created."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def incident_client(incident_engine, tmp_path, monkeypatch):
    """A TestClient signed in as a registered operative, using the in-memory database."""
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))

    def get_test_session():
        with Session(incident_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as client:
        credentials = {"email": "operative@example.com", "password": "correct-horse-42"}
        registered = client.post("/auth/register", json={"name": "Test Operative", **credentials})
        assert registered.status_code == 201
        token = client.post("/auth/login", json=credentials).json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client
    app.dependency_overrides.clear()


NEW_INCIDENT = {
    "title": "Parcel 123 lost",
    "description": "Customer never received parcel 123.",
    "category": "lost_parcel",
    "origin": "customer",
    "branch": "central",
}
