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
        assert e["emit_at"].replace("|", "\\|") in row


def test_transport_and_prerequisites_are_specified():
    for heading in ("## 9. Transport", "### 9.2 Collector endpoint", "### 9.3 Browser producer", "### 9.4 Backend producer", "## 10. Prerequisites"):
        assert heading in PLAN
    for needle in ("POST /telemetry/events", "202", "429", "Retry-After", "X-Request-ID", "min_stock", "stock_is_read_only", "warehouse_mismatch", "TELEMETRY_PEPPER"):
        assert needle in PLAN, needle


# --- Phase 2: the backoffice implements the plan --------------------------------------------

BACKOFFICE = REPO_ROOT / "uis" / "backoffice"
SOURCE_FILES = [p for folder in ("lib", "app") for p in (BACKOFFICE / folder).rglob("*.ts*") if p.name != "telemetry-schema.generated.ts"]
SOURCE_TEXT = {p: p.read_text(encoding="utf-8") for p in SOURCE_FILES}


def track_literals():
    pattern = re.compile(r'track\(\s*"([a-z0-9_]+)"')
    found = {}
    for path, text in SOURCE_TEXT.items():
        for name in pattern.findall(text):
            found.setdefault(name, set()).add(path.relative_to(BACKOFFICE).as_posix())
    return found


def test_every_track_call_uses_an_event_of_the_approved_plan():
    known = {e["type"] for e in ev.EVENTS}
    used = track_literals()
    assert set(used) <= known, f"events not in the plan: {set(used) - known}"


def test_every_mandatory_metric_is_instrumented_in_the_backoffice():
    used = track_literals()
    for name in MANDATORY:
        assert name in used, f"{name} is not tracked anywhere"


def test_implementation_table_matches_the_source_code():
    import build

    used = track_literals()
    for name, where in build.FRONTEND_IMPLEMENTATION.items():
        assert name in used or name in PROVIDER_EVENTS, f"{name} is listed as implemented but never tracked"
        for path in re.findall(r"`((?:lib|app)/[A-Za-z0-9_./-]+)`", where):
            assert (BACKOFFICE / path).exists(), f"{path} does not exist"
    for name in used:
        assert name in build.FRONTEND_IMPLEMENTATION, f"{name} is tracked but missing from the implementation table"


# Tracked through helper functions in telemetry-flows.ts and telemetry-errors.ts, with the same literal call form.
PROVIDER_EVENTS = {"page_viewed", "flow_started", "flow_abandoned", "form_validation_failed", "page_load_recorded", "frontend_error_caught"}


def test_only_the_telemetry_service_and_the_api_client_call_fetch():
    allowed = {"lib/telemetry.ts", "lib/api-client.ts"}
    for path, text in SOURCE_TEXT.items():
        relative = path.relative_to(BACKOFFICE).as_posix()
        if relative in allowed:
            continue
        assert not re.search(r"(?<![A-Za-z.])fetch\(|axios|sendBeacon", text), f"{relative} sends requests on its own"


def test_pages_and_components_do_not_build_envelopes_or_know_the_endpoint():
    for path, text in SOURCE_TEXT.items():
        relative = path.relative_to(BACKOFFICE).as_posix()
        assert "NEXT_PUBLIC_TELEMETRY_ENDPOINT" not in text or relative == "lib/telemetry.ts", relative
        if relative.startswith("app/"):
            assert not re.search(r"eventId|sessionId|schemaVersion", text), f"{relative} sets envelope fields"
            assert "track(" not in text.replace("trackFrontendError(", ""), f"{relative} tracks directly"


def test_endpoint_is_configured_through_environment_variables():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    local_example = (BACKOFFICE / ".env.example").read_text(encoding="utf-8")
    for text in (compose, env_example):
        assert "NEXT_PUBLIC_TELEMETRY_ENDPOINT" in text
        assert "TELEMETRY_ENDPOINT:" in text or "TELEMETRY_ENDPOINT=" in text
    assert "NEXT_PUBLIC_TELEMETRY_ENDPOINT" in local_example


def test_browser_schema_is_generated_from_the_plan():
    text = (BACKOFFICE / "lib" / "telemetry-schema.generated.ts").read_text(encoding="utf-8")
    assert f'TELEMETRY_SCHEMA_VERSION = "{ev.SCHEMA_VERSION}"' in text
    for e in ev.EVENTS:
        assert f'"{e["type"]}": {{' in text


# --- Phase 3: storage mapping ---------------------------------------------------------------


def test_level_and_value_mapping_only_names_real_events_and_numeric_properties():
    types = {e["type"] for e in ev.EVENTS}
    assert set(ev.LEVELS) <= types and set(ev.VALUE_PROPERTY) <= types
    assert set(ev.LEVELS.values()) <= {"warn", "error"}
    for e in ev.EVENTS:
        assert e["level"] in ("info", "warn", "error")
        name = e["value_property"]
        if name:
            prop = next(p for p in e["properties"] if p["name"] == name)
            kinds = prop["schema"]["type"] if isinstance(prop["schema"]["type"], list) else [prop["schema"]["type"]]
            assert set(kinds) - {"null"} <= {"integer", "number"}, f"{e['type']}.{name} is not numeric"


def test_the_plan_documents_the_storage_mapping():
    section = PLAN.split("## 13. Phase 3: storage in Supabase")[1]
    for needle in ("telemetry_events", "gen_random_uuid()", "GIN", "_envelope", '"received": N, "stored": M, "rejected": R', "list[TelemetryEvent]", "append-only"):
        assert needle in section, needle
    for e in ev.EVENTS:
        assert f"| `{e['type']}` | {e['level']} |" in section


def test_the_api_catalogue_is_generated_from_the_plan():
    text = (REPO_ROOT / "services" / "telemetry_catalogue.py").read_text(encoding="utf-8")
    assert "GENERATED by docs/telemetry/build.py" in text
    for e in ev.EVENTS:
        assert f"'{e['type']}':" in text


def test_the_frontend_was_not_touched_by_the_storage_phase():
    """Phase 3 is backend only: the browser's schema file is generated from the same plan and must still match it."""
    text = (BACKOFFICE / "lib" / "telemetry-schema.generated.ts").read_text(encoding="utf-8")
    assert f'TELEMETRY_SCHEMA_VERSION = "{ev.SCHEMA_VERSION}"' in text


# --- Phase 4: the operational report ----------------------------------------------------------


def test_the_plan_documents_every_metric_of_the_report():
    source = (REPO_ROOT / "services" / "telemetry" / "analysis.py").read_text(encoding="utf-8")
    keys = re.findall(r'^    "(\w+)": \w+,$', source.split("METRICS = {")[1], re.M)
    assert len(keys) >= 3
    section = PLAN.split("## 14. Phase 4: the operational report")[1]
    for key in keys:
        assert f"`{key}`" in section, key
    for needle in ("pd.to_datetime(..., utc=True)", "60 seconds", "X-Cache", "`422`", "last 7 days"):
        assert needle in section, needle


def test_the_metrics_read_only_events_of_the_plan():
    import ast

    tree = ast.parse((REPO_ROOT / "services" / "telemetry" / "analysis.py").read_text(encoding="utf-8"))
    types = {e["type"] for e in ev.EVENTS}
    requested = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_load" and len(node.args) == 5:
            requested |= {item.value for item in node.args[4].elts}
    assert {"page_load_recorded", "network_request_failed", "frontend_error_caught", "login_attempt_failed", "login_succeeded"} <= requested
    assert requested <= types, f"events not in the plan: {requested - types}"
