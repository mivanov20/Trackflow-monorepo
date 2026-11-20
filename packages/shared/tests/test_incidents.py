from datetime import datetime, timezone

import pytest
from trackflow_shared.incidents import (
    BRANCHES,
    STATUS_TRANSITIONS,
    process_csv_row,
    validate_csv_row,
    validate_filter,
    validate_incident_payload,
    validate_status_value,
    validate_transition,
)

VALID_PAYLOAD = {
    "title": "Parcel 123 lost",
    "description": "Customer never received parcel 123.",
    "category": "lost_parcel",
    "origin": "customer",
    "branch": "central",
}

VALID_ROW = {
    "incident_id": "INC-1",
    "description": "  Package never arrived at the address  ",
    "date": "2025-03-14",
    "status": "CLOSED",
    "category": "DELAYED_DELIVERY",
    "country": "ES",
}


def test_valid_payload_defaults_status_to_open():
    clean, errors = validate_incident_payload(VALID_PAYLOAD)
    assert errors == []
    assert clean["status"] == "open"


@pytest.mark.parametrize("field", ["title", "description", "category", "origin", "branch"])
def test_missing_field_is_reported_by_name(field):
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != field}
    clean, errors = validate_incident_payload(payload)
    assert clean is None
    assert [e.field for e in errors] == [field]
    assert "required" in errors[0].message


@pytest.mark.parametrize(
    "field,value",
    [("category", "nonsense"), ("origin", "alien"), ("branch", "mars_base"), ("status", "done")],
)
def test_out_of_set_values_are_rejected_in_plain_language(field, value):
    _, errors = validate_incident_payload({**VALID_PAYLOAD, field: value})
    assert [e.field for e in errors] == [field]
    assert "must be one of" in errors[0].message


def test_all_problems_are_reported_at_once():
    _, errors = validate_incident_payload({"title": " ", "category": "x"})
    assert {e.field for e in errors} == {"title", "description", "category", "origin", "branch"}


def test_non_object_body_is_rejected():
    assert validate_incident_payload(None)[1][0].field == "body"
    assert validate_incident_payload(["a"])[1][0].field == "body"


def test_lifecycle_transitions():
    assert validate_transition("open", "in_progress") is None
    assert validate_transition("open", "discarded") is None
    assert validate_transition("in_progress", "resolved") is None
    assert validate_transition("in_progress", "discarded") is None
    assert validate_transition("open", "resolved") is not None
    assert validate_transition("in_progress", "open") is not None
    for final in ("resolved", "discarded"):
        assert STATUS_TRANSITIONS[final] == ()
        assert validate_transition(final, "open") is not None


def test_status_value_must_be_in_enum():
    assert validate_status_value("resolved") is None
    assert validate_status_value("closed") is not None
    assert validate_status_value(None) is not None


def test_filters():
    assert validate_filter("status", None) is None
    assert validate_filter("branch", "la_office") is None
    assert validate_filter("category", "bogus") is not None


def test_branch_labels_match_the_briefing():
    assert BRANCHES["central"] == "Central"
    assert BRANCHES["la_warehouse"] == "Los Angeles — Warehouse"
    assert BRANCHES["zaragoza_office"] == "Zaragoza — Office"


def test_csv_row_is_transformed():
    incident, reasons = process_csv_row(VALID_ROW)
    assert reasons == []
    assert incident["title"] == "Package never arrived at the address"
    assert incident["description"] == VALID_ROW["description"]
    assert incident["status"] == "resolved"
    assert incident["category"] == "carrier_issue"
    assert incident["origin"] == "customer"
    assert incident["branch"] == "zaragoza_office"
    expected = datetime(2025, 3, 14, tzinfo=timezone.utc)
    assert incident["created_at"] == incident["updated_at"] == expected


def test_title_is_first_120_characters_trimmed():
    row = {**VALID_ROW, "description": "x" * 100 + " " * 30 + "tail"}
    incident, _ = process_csv_row(row)
    assert incident["title"] == "x" * 100
    assert incident["description"] == row["description"]


@pytest.mark.parametrize(
    "patch,expected",
    [
        ({"description": ""}, "missing description"),
        ({"description": "   "}, "missing description"),
        ({"incident_id": ""}, "missing incident_id"),
        ({"status": "PENDING"}, "unknown status"),
        ({"category": "FIRE"}, "unknown category"),
        ({"country": "FR"}, "unknown country"),
        ({"date": "14/03/2025"}, "invalid date"),
        ({"date": ""}, "missing date"),
    ],
)
def test_invalid_csv_rows_report_a_reason(patch, expected):
    incident, reasons = process_csv_row({**VALID_ROW, **patch})
    assert incident is None
    assert any(expected in reason for reason in reasons)
    assert validate_csv_row({**VALID_ROW, **patch}) == reasons
