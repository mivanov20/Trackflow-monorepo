"""POST /api/incidents/analyze y GET /api/incidents/results/export."""

import csv
import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from trackflow_shared.incident_analysis import analyze_path, results_csv_text

from services.main import app
from services.routers.incident_analysis import clear_results

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = REPO_ROOT / "data" / "eval" / "incidents-synthetic.csv"
HEADER = "incident_id,date,country,customer_type,tracking_number,carrier,category,description,status,customer_email,satisfaction_score\n"
GOOD_ROW = "TRF-000001,2025-03-01,US,B2C,TRK123456789,UPS,LOST_PARCEL,Parcel never arrived.,OPEN,someone@example.com,\n"


@pytest.fixture(autouse=True)
def fresh_results():
    clear_results()
    yield
    clear_results()


def upload(client, content, name="incidents.csv", content_type="text/csv", **kwargs):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return client.post("/api/incidents/analyze", files={"file": (name, data, content_type)}, **kwargs)


def sample_bytes():
    return SAMPLE.read_bytes()


# --- análisis ----------------------------------------------------------------------------


def test_the_analysis_returns_the_summary_of_the_briefing(incident_client):
    response = upload(incident_client, sample_bytes(), name="incidents-trackflow.csv")
    assert response.status_code == 200
    body = response.json()
    assert (body["total_records"], body["valid_records"], body["invalid_records"]) == (100, 95, 5)
    assert body["source_file"] == "incidents-trackflow.csv"
    assert {item["rule"]: item["count"] for item in body["invalid_breakdown"] if item["count"]} == {
        "invalid_tracking_number": 1, "invalid_carrier": 1, "invalid_category": 1, "invalid_email": 1, "closed_without_score": 1,
    }
    assert {item["value"]: item["count"] for item in body["by_category"]} == {
        "LOST_PARCEL": 14, "DELAYED_DELIVERY": 38, "WRONG_ADDRESS": 19, "RETURN_REQUEST": 17, "DAMAGE": 7,
    }
    assert {item["value"]: item["count"] for item in body["by_status"]} == {"OPEN": 29, "CLOSED": 52, "DISCARDED": 14}
    assert {item["value"]: item["count"] for item in body["by_country"]} == {"US": 50, "ES": 45}
    assert body["satisfaction"]["scored"] == 52 and body["satisfaction"]["average"] == 3.06
    assert [item["count"] for item in body["satisfaction"]["distribution"]] == [6, 11, 15, 14, 6]
    assert [item["line"] for item in body["invalid_examples"]] == [8, 24, 42, 69, 91]


def test_the_api_and_the_script_share_the_same_logic(incident_client):
    from_api = upload(incident_client, sample_bytes()).json()
    from_script = analyze_path(str(SAMPLE), "incidents.csv").to_dict()
    for body in (from_api, from_script):
        body.pop("analyzed_at")
    assert from_api == from_script


def test_a_file_with_a_byte_order_mark_is_accepted(incident_client):
    assert upload(incident_client, b"\xef\xbb\xbf" + (HEADER + GOOD_ROW).encode("utf-8")).json()["valid_records"] == 1


def test_a_large_file_is_analysed(incident_client):
    rows = GOOD_ROW * 60_000
    body = upload(incident_client, HEADER + rows).json()
    assert body["total_records"] == 60_000 and body["valid_records"] == 60_000


def test_an_invalid_only_file_still_returns_a_summary(incident_client):
    response = upload(incident_client, HEADER + GOOD_ROW.replace("UPS", "MRW") * 3)
    body = response.json()
    assert response.status_code == 200 and body["valid_records"] == 0 and body["invalid_records"] == 3
    assert body["satisfaction"]["average"] is None and body["by_category"][0]["percent"] == 0.0


def test_a_missing_score_column_is_reported_as_a_warning(incident_client):
    no_score = ",".join(HEADER.strip().split(",")[:-1]) + "\n" + GOOD_ROW.rsplit(",", 1)[0] + "\n"
    body = upload(incident_client, no_score).json()
    assert any("satisfaction_score" in warning for warning in body["warnings"])


# --- errores -------------------------------------------------------------------------------


def test_analysis_requires_a_session():
    with TestClient(app) as anonymous:
        assert upload(anonymous, sample_bytes()).status_code == 401
        assert anonymous.get("/api/incidents/results/export").status_code == 401


def test_an_empty_file_returns_400_with_a_message(incident_client):
    response = upload(incident_client, b"")
    assert response.status_code == 400
    assert response.json() == {"error": "empty_file", "message": "The file is empty."}


def test_a_request_without_the_file_field_returns_400(incident_client):
    response = incident_client.post("/api/incidents/analyze", files={"other": ("incidents.csv", b"x", "text/csv")})
    assert response.status_code == 400 and response.json()["error"] == "file_missing"
    assert "file" in response.json()["message"]


def test_a_non_multipart_request_returns_415(incident_client):
    response = incident_client.post("/api/incidents/analyze", json={"file": "x"})
    assert response.status_code == 415 and response.json()["error"] == "unsupported_media_type"


@pytest.mark.parametrize("name", ["incidents.txt", "incidents.xlsx", "incidents", "incidents.csv.exe"])
def test_only_csv_files_are_accepted(incident_client, name):
    response = upload(incident_client, sample_bytes(), name=name)
    assert response.status_code == 415 and response.json()["error"] == "unsupported_file_type"


def test_a_file_name_with_a_folder_path_is_shortened(incident_client):
    body = upload(incident_client, sample_bytes(), name="C:\\exports\\secret folder\\incidents.csv").json()
    assert body["source_file"] == "incidents.csv"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (HEADER, "no data rows"),
        ("incident_id,date\nTRF-000001,2025-03-01\n", "missing required column"),
        (HEADER.replace(",", ";") + GOOD_ROW.replace(",", ";"), "separator is a comma"),
        (b"\x00\x01\x02\xff\xfe" * 50, "UTF-8"),
        ("just some text\nwithout structure\n", "missing required column"),
    ],
)
def test_content_that_cannot_be_analysed_returns_422(incident_client, content, message):
    response = upload(incident_client, content)
    assert response.status_code == 422 and response.json()["error"] == "invalid_csv"
    assert message.lower() in response.json()["message"].lower()


def test_a_file_that_is_not_utf8_returns_422_without_echoing_it(incident_client):
    latin1 = (HEADER + GOOD_ROW.replace("never arrived", "Caf\xe9 secret@customer.com")).encode("latin-1")
    response = upload(incident_client, latin1)
    assert response.status_code == 422 and "UTF-8" in response.json()["message"]
    assert "secret@customer.com" not in response.text


def test_a_file_over_the_limit_returns_413(incident_client, monkeypatch):
    monkeypatch.setenv("ANALYSIS_MAX_UPLOAD_MB", "1")
    rows = (1024 * 1024) // len(GOOD_ROW) + 500  # un poco más de 1 MB
    response = upload(incident_client, HEADER + GOOD_ROW * rows)
    assert response.status_code == 413 and response.json()["error"] == "file_too_large"
    assert "1 MB" in response.json()["message"]


def test_a_request_declaring_a_huge_body_is_refused_before_reading_it(incident_client, monkeypatch):
    monkeypatch.setenv("ANALYSIS_MAX_UPLOAD_MB", "1")
    rows = (2 * 1024 * 1024) // len(GOOD_ROW) + 2000  # más que el límite (1 MB) más el margen multipart (1 MB)
    response = upload(incident_client, HEADER + GOOD_ROW * rows)
    assert response.status_code == 413


def test_the_upload_limit_setting_is_validated(monkeypatch):
    from services.routers.incident_analysis import max_upload_bytes

    assert max_upload_bytes() == 250 * 1024 * 1024
    for bad in ("big", "0", "-5"):
        monkeypatch.setenv("ANALYSIS_MAX_UPLOAD_MB", bad)
        with pytest.raises(RuntimeError, match="ANALYSIS_MAX_UPLOAD_MB"):
            max_upload_bytes()


# --- exportación -----------------------------------------------------------------------------


def test_export_without_an_analysis_returns_404_not_an_incident_lookup(incident_client):
    response = incident_client.get("/api/incidents/results/export")
    assert response.status_code == 404 and response.json()["error"] == "no_analysis"


def test_export_returns_the_last_analysis_as_a_downloadable_csv(incident_client):
    upload(incident_client, sample_bytes())
    response = incident_client.get("/api/incidents/results/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="results.csv"'
    assert response.headers["cache-control"] == "no-store"

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["metric", "value"] and all(len(row) == 2 for row in rows)
    values = {metric: value for metric, value in rows[1:]}
    assert len(values) == len(rows) - 1
    assert values["total_records"] == "100" and values["valid_records"] == "95" and values["invalid_records"] == "5"
    assert values["category.DELAYED_DELIVERY"] == "38" and values["satisfaction.average"] == "3.06"
    assert values["invalid.invalid_carrier"] == "1" and values["country.US"] == "50"
    assert response.text == results_csv_text(analyze_path(str(SAMPLE)))


def test_a_new_analysis_replaces_the_previous_one(incident_client):
    upload(incident_client, sample_bytes())
    upload(incident_client, HEADER + GOOD_ROW * 2)
    values = dict(list(csv.reader(io.StringIO(incident_client.get("/api/incidents/results/export").text)))[1:])
    assert values["total_records"] == "2"


def test_a_failed_upload_keeps_the_previous_analysis(incident_client):
    upload(incident_client, sample_bytes())
    assert upload(incident_client, b"").status_code == 400
    values = dict(list(csv.reader(io.StringIO(incident_client.get("/api/incidents/results/export").text)))[1:])
    assert values["total_records"] == "100"


def test_each_user_exports_only_their_own_analysis(incident_client):
    upload(incident_client, sample_bytes())
    incident_client.post("/users", json={"email": "second@example.com", "password": "correct-horse-42"})
    token = incident_client.post("/auth/login", json={"email": "second@example.com", "password": "correct-horse-42"}).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert incident_client.get("/api/incidents/results/export", headers=other).status_code == 404
    upload(incident_client, HEADER + GOOD_ROW, headers=other)
    mine = dict(list(csv.reader(io.StringIO(incident_client.get("/api/incidents/results/export").text)))[1:])
    theirs = dict(list(csv.reader(io.StringIO(incident_client.get("/api/incidents/results/export", headers=other).text)))[1:])
    assert mine["total_records"] == "100" and theirs["total_records"] == "1"


# --- privacidad y convivencia con el resto de rutas -----------------------------------------------


def test_no_customer_email_reaches_a_response_an_export_or_the_logs(incident_client, caplog):
    with caplog.at_level(logging.DEBUG):
        analysis = upload(incident_client, sample_bytes())
        export = incident_client.get("/api/incidents/results/export")
        failure = upload(incident_client, (HEADER + GOOD_ROW).replace("someone@example.com", "x") + "bad;row;here\n")
    everything = analysis.text + export.text + failure.text + caplog.text
    for fragment in ("@example.com", "customer0", "customer1", "someone@"):
        assert fragment not in everything


def test_the_incident_manager_routes_still_work(incident_client):
    assert incident_client.get("/api/incidents").status_code == 200
    assert incident_client.get("/api/incidents/summary").status_code == 200
    missing = incident_client.get("/api/incidents/999")
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"


def test_the_browser_may_call_the_endpoints(incident_client):
    for method, path in (("POST", "/api/incidents/analyze"), ("GET", "/api/incidents/results/export")):
        preflight = incident_client.options(
            path, headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "authorization"}
        )
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == "http://localhost:3001"


def test_the_openapi_schema_documents_the_file_field(incident_client):
    spec = incident_client.get("/openapi.json").json()
    body = spec["paths"]["/api/incidents/analyze"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["required"] == ["file"] and body["properties"]["file"]["format"] == "binary"
    assert "/api/incidents/results/export" in spec["paths"]
