"""Telemetry Plan: el Markdown, el JSON de esquemas y el catálogo (docs/telemetry/events.py) no se contradicen."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[3]
TELEMETRY_DIR = REPO_ROOT / "docs" / "telemetry"
sys.path.insert(0, str(TELEMETRY_DIR))

import events as ev  # noqa: E402

SCHEMAS = json.loads((TELEMETRY_DIR / "event-schemas.json").read_text(encoding="utf-8"))
PLAN = (TELEMETRY_DIR / "telemetry-plan.md").read_text(encoding="utf-8")

MANDATORY = [
    "inbound_order_created", "outbound_order_created", "stock_threshold_triggered",
    "direct_stock_edit_rejected", "inventory_discrepancy_detected",
]
MIN_INVENTORY_PROPERTIES = ["warehouse", "client_id", "product_id", "product_category", "quantity"]
FORBIDDEN_NAMES = re.compile(r"email(?!_hash|_provider)|name$|phone|address|password|token|tracking|recipient|carrier|description|title|message$|stack$|ip$", re.I)


def envelope(event):
    return {
        "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
        "timestamp": "2026-11-21T14:03:22.418Z",
        "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
        "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
        "event_type": event["type"],
        "schemaVersion": ev.SCHEMA_VERSION,
        "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
        "properties": event["example"],
    }


def validator(event_type):
    root = {"$ref": f"#/events/{event_type}", "definitions": SCHEMAS["definitions"], "events": SCHEMAS["events"]}
    return jsonschema.Draft7Validator(root)


def test_schema_file_is_valid_draft07():
    jsonschema.Draft7Validator.check_schema(SCHEMAS["definitions"]["envelope"])
    for name, schema in SCHEMAS["events"].items():
        jsonschema.Draft7Validator.check_schema(schema)
        assert name == schema["title"]


def test_mandatory_metrics_are_present_exact_and_flagged():
    for name in MANDATORY:
        assert name in SCHEMAS["events"], name
        assert SCHEMAS["events"][name]["x-class"] == "mandatory"
        props = SCHEMAS["events"][name]["allOf"][1]["properties"]["properties"]
        for field in MIN_INVENTORY_PROPERTIES:
            assert field in props["properties"], f"{name} lacks {field}"
            assert field in props["required"], f"{name}: {field} must be required"
    others = [n for n, s in SCHEMAS["events"].items() if s["x-class"] == "mandatory"]
    assert sorted(others) == sorted(MANDATORY)


def test_mandatory_values_match_the_briefing():
    for name in MANDATORY:
        props = SCHEMAS["events"][name]["allOf"][1]["properties"]["properties"]["properties"]
        assert props["warehouse"]["enum"][:2] == ["los_angeles", "zaragoza"]
        assert set(props["product_category"]["enum"]) >= {"fashion", "electronics", "cosmetics"}


def test_coverage_requirements():
    events = ev.EVENTS
    assert len(events) - len(MANDATORY) >= 8
    assert len({e["category"] for e in events if e["klass"] != "mandatory"}) >= 3
    inventory = [e for e in events if e["category"] == "inventory"]
    assert len(inventory) >= 5


def test_every_event_has_hypothesis_decision_and_justified_delivery():
    for e in ev.EVENTS:
        assert e["hypothesis"].strip() and e["decision"].strip(), e["type"]
        assert e["delivery"] in ("stream", "batch")
        assert len(e["delivery_reason"]) > 40, e["type"]
        assert e["pii_note"].strip()
        sentence = f"We capture `{e['type']}` because we need to know `{e['hypothesis']}`, which allows us to make the decision `{e['decision']}`."
        assert sentence in PLAN, e["type"]


def test_event_names_follow_entity_action_taxonomy():
    types = [e["type"] for e in ev.EVENTS]
    assert len(types) == len(set(types))
    for name in types:
        assert re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$", name), name
        assert any(name.endswith("_" + verb) for verb in ev.VERBS), f"{name}: verb not in the controlled list"


@pytest.mark.parametrize("event", ev.EVENTS, ids=lambda e: e["type"])
def test_example_is_valid_for_its_schema(event):
    validator(event["type"]).validate(envelope(event))


@pytest.mark.parametrize("event", ev.EVENTS, ids=lambda e: e["type"])
def test_allowlist_rejects_unknown_properties_and_wrong_event_type(event):
    v = validator(event["type"])
    extra = envelope(event)
    extra["properties"] = {**event["example"], "customer_email": "someone@example.com"}
    assert not v.is_valid(extra)
    wrong = envelope(event)
    wrong["event_type"] = next(e["type"] for e in ev.EVENTS if e["type"] != event["type"])
    assert not v.is_valid(wrong)
    missing = envelope(event)
    del missing["requestId"]
    assert not v.is_valid(missing)


@pytest.mark.parametrize("event", ev.EVENTS, ids=lambda e: e["type"])
def test_no_property_name_suggests_personal_data(event):
    for p in event["properties"]:
        name = p["name"]
        # Nombres que contienen una palabra sospechosa pero guardan un indicador, un contador o un nombre de lista cerrada.
        safe = {
            "email_hash", "email_provider", "error_name", "route_template", "table", "service", "message_type",
            "form_name", "flow_name", "has_name", "has_phone", "has_address", "token_age_seconds", "target_user_id",
        }
        if name in safe:
            continue
        assert not FORBIDDEN_NAMES.search(name), f"{event['type']}.{name} looks like personal data"


def test_free_text_is_not_allowed_anywhere():
    for e in ev.EVENTS:
        for p in e["properties"]:
            s = p["schema"]
            if s.get("type") == "string" or s.get("type") == ["string", "null"]:
                assert "enum" in s or "pattern" in s, f"{e['type']}.{p['name']} is an unconstrained string"


def test_pseudonymised_properties_document_how():
    for e in ev.EVENTS:
        for p in e["properties"]:
            if p["pii"] == "pseudonymized" and p["name"] in ("email_hash", "source_network_hash"):
                assert "HMAC" in p["how"] and "PEPPER" in p["how"]


def test_every_property_is_described_in_the_markdown():
    for e in ev.EVENTS:
        for p in e["properties"]:
            assert f"`{p['name']}`" in PLAN


def test_committed_files_are_up_to_date():
    result = subprocess.run([sys.executable, str(TELEMETRY_DIR / "build.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_quick_reference_has_a_row_per_event_with_readiness():
    section = PLAN.split("### 3.6 Instrumentation quick reference")[1].split("## 4.")[0]
    for e in ev.EVENTS:
        row = next((line for line in section.splitlines() if line.startswith(f"| `{e['type']}` |")), None)
        assert row, e["type"]
        assert e["emit_at"].replace("|", "\|") in row\|") in row


def test_transport_and_prerequisites_are_specified():
    for heading in ("## 9. Transport", "### 9.2 Collector endpoint", "### 9.3 Browser producer", "### 9.4 Backend producer", "## 10. Prerequisites"):
        assert heading in PLAN
    for needle in ("POST /telemetry/events", "202", "429", "Retry-After", "X-Request-ID", "min_stock", "stock_is_read_only", "warehouse_mismatch", "TELEMETRY_PEPPER"):
        assert needle in PLAN, needle
