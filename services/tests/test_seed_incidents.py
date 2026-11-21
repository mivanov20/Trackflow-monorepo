from sqlmodel import Session, select

from services.models import Incident
from seed_incidents import main, seed_incidents
from synthetic_csv import EXPECTED_CATEGORY, EXPECTED_STATUS, invalid_rows, valid_rows, write_csv


def test_seed_loads_95_valid_rows_with_expected_totals(tmp_path, incident_client, incident_engine):
    csv_path = write_csv(tmp_path / "in.csv", valid_rows() + invalid_rows())
    report = seed_incidents(csv_path, incident_engine)

    assert report.inserted == 95
    assert len(report.invalid) == 5

    summary = incident_client.get("/api/incidents/summary").json()
    assert summary["total"] == 95
    assert {k: v for k, v in summary["by_status"].items() if v} == EXPECTED_STATUS
    assert {k: v for k, v in summary["by_category"].items() if v} == EXPECTED_CATEGORY
    assert summary["by_origin"] == {"customer": 95, "branch": 0, "internal": 0}
    assert summary["by_branch"]["la_office"] + summary["by_branch"]["zaragoza_office"] == 95


def test_seed_applies_the_transformations(tmp_path, incident_client, incident_engine):
    row = {**valid_rows()[0], "description": "A" * 150, "date": "2025-02-03", "status": "CLOSED",
           "category": "DAMAGE", "country": "ES"}
    seed_incidents(write_csv(tmp_path / "in.csv", [row]), incident_engine)
    incident = incident_client.get("/api/incidents").json()[0]
    assert incident["title"] == "A" * 120
    assert incident["description"] == "A" * 150
    assert incident["status"] == "resolved"
    assert incident["category"] == "carrier_issue"
    assert incident["origin"] == "customer"
    assert incident["branch"] == "zaragoza_office"
    assert incident["created_at"] == incident["updated_at"] == "2025-02-03T00:00:00Z"


def test_seed_is_idempotent(tmp_path, incident_client, incident_engine):
    csv_path = write_csv(tmp_path / "in.csv", valid_rows() + invalid_rows())
    first = seed_incidents(csv_path, incident_engine)
    second = seed_incidents(csv_path, incident_engine)
    assert first.inserted == 95
    assert second.inserted == 0 and second.already_loaded == 95
    assert incident_client.get("/api/incidents/summary").json()["total"] == 95


def test_duplicate_ids_inside_one_file_are_loaded_once(tmp_path, incident_engine):
    rows = valid_rows()[:3]
    report = seed_incidents(write_csv(tmp_path / "in.csv", rows + rows), incident_engine)
    assert report.inserted == 3 and report.already_loaded == 3


def test_personal_data_columns_are_never_stored(tmp_path, incident_engine):
    seed_incidents(write_csv(tmp_path / "in.csv", valid_rows()[:3]), incident_engine)
    with Session(incident_engine) as session:
        for incident in session.exec(select(Incident)).all():
            assert "example.com" not in incident.title + incident.description


def test_command_reports_invalid_records_and_exit_code(tmp_path, monkeypatch, capsys, incident_engine):
    # Point the platform engine at the test database so main() writes there.
    monkeypatch.setattr("services.database.engine", incident_engine)
    monkeypatch.setattr("services.database.create_db_and_tables", lambda: None)
    csv_path = write_csv(tmp_path / "in.csv", valid_rows()[:5] + invalid_rows())
    assert main(["seed_incidents.py", str(csv_path)]) == 0
    output = capsys.readouterr().out
    assert "Inserted 5 incident(s)" in output
    assert "5 invalid record(s) were NOT inserted" in output
    assert "missing description" in output and "unknown status 'PENDING'" in output
    assert main(["seed_incidents.py", str(csv_path)]) == 0
    assert "Inserted 0 incident(s); 5 already loaded" in capsys.readouterr().out


def test_missing_file_and_missing_database_url_exit_with_error(tmp_path, monkeypatch, capsys):
    assert main(["seed_incidents.py", str(tmp_path / "nope.csv")]) == 1
    assert "not found" in capsys.readouterr().err
    csv_path = write_csv(tmp_path / "in.csv", valid_rows()[:1])
    monkeypatch.delenv("DATABASE_URL")
    assert main(["seed_incidents.py", str(csv_path)]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err


# --- critical input / database failures -----------------------------------------


def _point_main_at(monkeypatch, engine):
    monkeypatch.setattr("services.database.engine", engine)
    monkeypatch.setattr("services.database.create_db_and_tables", lambda: None)


def test_missing_required_column_exits_with_error(tmp_path, monkeypatch, capsys, incident_engine):
    _point_main_at(monkeypatch, incident_engine)
    csv_path = tmp_path / "in.csv"
    csv_path.write_text("incident_id,description\n1,Lost parcel\n", encoding="utf-8")
    assert main(["seed_incidents.py", str(csv_path)]) == 1
    err = capsys.readouterr().err
    assert "missing required column(s): date, status, category, country" in err


def test_non_utf8_and_empty_files_exit_with_error(tmp_path, monkeypatch, capsys, incident_engine):
    _point_main_at(monkeypatch, incident_engine)
    latin1 = tmp_path / "latin1.csv"
    latin1.write_bytes("incident_id,description\n1,Caf\xe9\n".encode("latin-1"))
    assert main(["seed_incidents.py", str(latin1)]) == 1
    assert "not valid UTF-8" in capsys.readouterr().err

    header_only = write_csv(tmp_path / "empty.csv", [])
    assert main(["seed_incidents.py", str(header_only)]) == 1
    assert "no data rows" in capsys.readouterr().err


def test_database_failure_exits_with_sanitized_error(tmp_path, monkeypatch, capsys, incident_engine):
    from sqlalchemy.exc import OperationalError

    def refuse(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("password authentication failed for user secret_user"))

    _point_main_at(monkeypatch, incident_engine)
    monkeypatch.setattr(Session, "commit", refuse)
    csv_path = write_csv(tmp_path / "in.csv", valid_rows()[:2])
    assert main(["seed_incidents.py", str(csv_path)]) == 1
    err = capsys.readouterr().err
    assert "Seed failed" in err and "OperationalError" in err
    assert "secret_user" not in err


def test_unreachable_database_exits_with_error(tmp_path, monkeypatch, capsys):
    from sqlalchemy.exc import OperationalError

    def unreachable():
        raise OperationalError("CONNECT", {}, Exception("could not connect to db.internal"))

    monkeypatch.setattr("services.database.create_db_and_tables", unreachable)
    csv_path = write_csv(tmp_path / "in.csv", valid_rows()[:1])
    assert main(["seed_incidents.py", str(csv_path)]) == 1
    err = capsys.readouterr().err
    assert "Could not prepare the database" in err and "db.internal" not in err
