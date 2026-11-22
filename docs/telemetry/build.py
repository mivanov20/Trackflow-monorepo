"""Generate `telemetry-plan.md` and `event-schemas.json` from `events.py` and `plan.template.md`.

Usage (from the repository root):
    python docs/telemetry/build.py            # rewrite both files
    python docs/telemetry/build.py --check    # exit 1 if the committed files are out of date

Both outputs are written with LF line endings so the check is stable on Windows and Linux.\n"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import events as ev  # noqa: E402

PLAN_PATH = HERE / "telemetry-plan.md"
SCHEMA_PATH = HERE / "event-schemas.json"
TS_SCHEMA_PATH = HERE.parents[1] / "uis" / "backoffice" / "lib" / "telemetry-schema.generated.ts"
PY_CATALOGUE_PATH = HERE.parents[1] / "services" / "telemetry_catalogue.py"
TEMPLATE_PATH = HERE / "plan.template.md"

MANDATORY_ORDER = [
    "inbound_order_created", "outbound_order_created", "stock_threshold_triggered",
    "direct_stock_edit_rejected", "inventory_discrepancy_detected",
]

ENVELOPE_FIELDS = [
    ("eventId", "string (UUID v4)", "yes", "Unique id of this event. Lets the pipeline drop duplicates when a batch is retried."),
    ("timestamp", "string (ISO 8601, UTC)", "yes", "When the thing happened, taken on the producer, for example `2026-11-21T14:03:22.418Z`. Always UTC with a `Z`."),
    ("sessionId", "string (UUID) or null", "yes", "One browser session (created at sign-in, regenerated at sign-out). `null` only for events with no user session (service start, scheduled scans)."),
    ("userId", "string (UUID) or null", "yes", "Internal account id (`User.user_id`). It is pseudonymous: no name, no email. `null` for anonymous callers (failed logins) and system events."),
    ("event_type", "string", "yes", "Event name in `entity_action` format (lowercase snake case, entity first, then the verb from the controlled list)."),
    ("schemaVersion", "string (semver)", "yes", "Version of the schema this event follows, for example `1.0.0`."),
    ("requestId", "string (UUID)", "yes", "Correlation id. The frontend creates one per user action and sends it as `X-Request-ID`; the API reuses it, or creates one when absent, and writes it on every event of that request. For events with no request, a fresh UUID."),
    ("properties", "object", "yes", "Event-specific payload. Only the keys allowlisted for that `event_type` are accepted."),
]

UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
EVENT_TYPE_RE = "^[a-z][a-z0-9]*(_[a-z0-9]+)+$"
SEMVER_RE = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
TIMESTAMP_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"


def envelope_schema():
    return {
        "type": "object",
        "required": ["eventId", "timestamp", "sessionId", "userId", "event_type", "schemaVersion", "requestId", "properties"],
        "additionalProperties": False,
        "properties": {
            "eventId": {"type": "string", "pattern": UUID_RE, "description": ENVELOPE_FIELDS[0][3]},
            "timestamp": {"type": "string", "format": "date-time", "pattern": TIMESTAMP_RE, "description": ENVELOPE_FIELDS[1][3]},
            "sessionId": {"type": ["string", "null"], "pattern": UUID_RE, "description": ENVELOPE_FIELDS[2][3]},
            "userId": {"type": ["string", "null"], "pattern": UUID_RE, "description": ENVELOPE_FIELDS[3][3]},
            "event_type": {"type": "string", "pattern": EVENT_TYPE_RE, "enum": [e["type"] for e in ev.EVENTS], "description": ENVELOPE_FIELDS[4][3]},
            "schemaVersion": {"type": "string", "pattern": SEMVER_RE, "description": ENVELOPE_FIELDS[5][3]},
            "requestId": {"type": "string", "pattern": UUID_RE, "description": ENVELOPE_FIELDS[6][3]},
            "properties": {"type": "object", "description": ENVELOPE_FIELDS[7][3]},
        },
    }


def event_schema(e):
    props = {p["name"]: {**p["schema"], "description": p["description"], "x-pii": p["pii"]} for p in e["properties"]}
    for p in e["properties"]:
        if p["how"] and p["pii"] != "none":
            props[p["name"]]["x-sanitization"] = p["how"]
    required = [p["name"] for p in e["properties"] if p["required"]]
    sentence = golden_sentence(e, plain=True)
    return {
        "title": e["type"],
        "description": e["description"],
        "x-class": "mandatory" if e["klass"] == "mandatory" else "identified_opportunity",
        "x-category": e["category"],
        "x-source": e["source"],
        "x-delivery": e["delivery"],
        "x-delivery-window": e["window"],
        "x-hypothesis": e["hypothesis"],
        "x-decision": e["decision"],
        "x-statement": sentence,
        "x-pii": e["pii"],
        "allOf": [
            {"$ref": "#/definitions/envelope"},
            {
                "properties": {
                    "event_type": {"const": e["type"]},
                    "properties": {
                        "type": "object",
                        "required": required,
                        "additionalProperties": False,
                        "properties": props,
                    },
                }
            },
        ],
    }


def build_schema():
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://trackflow.example/telemetry/event-schemas.json",
        "title": "TrackFlow telemetry event schemas",
        "description": (
            "Validate an event in two steps: it must match #/definitions/envelope, then the schema "
            "at #/events/<event_type>, which re-applies the envelope, fixes the event_type and allowlists "
            "the properties. Unknown properties are rejected (additionalProperties: false). "
            "Custom x-* keywords are documentation for humans and for the pipeline; validators ignore them."
        ),
        "x-schemaVersion": ev.SCHEMA_VERSION,
        "x-taxonomy": {"verbs": ev.VERBS, "categories": ev.CATEGORIES},
        "definitions": {"envelope": envelope_schema()},
        "events": {e["type"]: event_schema(e) for e in ev.EVENTS},
    }


# Phase 2: where the backoffice produces each event (file and function). Events not listed here wait for the
# backend producer of Phase 3. `test_telemetry_docs.py` checks this table against the source code.
FRONTEND_IMPLEMENTATION = {
    "inbound_order_created": "`lib/inventory.ts` -> `inventoryApi.createInboundOrder`",
    "outbound_order_created": "`lib/inventory.ts` -> `inventoryApi.createOutboundOrder`",
    "stock_threshold_triggered": "`lib/inventory.ts` -> `inventoryApi.createOutboundOrder` (after the exit)",
    "direct_stock_edit_rejected": "`lib/inventory.ts` -> `inventoryApi.createProduct` (stock-field guard)",
    "inventory_discrepancy_detected": "`lib/inventory.ts` -> `inventoryApi.recordStockCount` (new stock-count page)",
    "inbound_order_rejected": "`lib/inventory.ts` -> `inventoryApi.createInboundOrder` (failure branch)",
    "outbound_order_rejected": "`lib/inventory.ts` -> `inventoryApi.createOutboundOrder` (failure branch)",
    "product_created": "`lib/inventory.ts` -> `inventoryApi.createProduct`",
    "stock_integrity_violation_detected": "`lib/inventory.ts` -> `checkOrderIntegrity` (response check)",
    "session_expired": "`lib/api-client.ts` -> `apiRequest` (401 branch)",
    "login_attempt_failed": "`lib/auth.ts` -> `signIn` (failure branch)",
    "user_registered": "`lib/auth.ts` -> `registerUser`",
    "password_changed": "`lib/auth.ts` -> `changePassword`",
    "incident_created": "`lib/incidents.ts` -> `incidentsApi.create`",
    "incident_status_changed": "`lib/incidents.ts` -> `incidentsApi.updateStatus`",
    "incident_file_analyzed": "`lib/incident-analysis.ts` -> `analysisApi.analyze`",
    "incident_file_rejected": "`lib/incident-analysis.ts` -> `analysisApi.analyze` (failure branch)",
    "filter_applied": "`lib/incidents.ts` -> `incidentsApi.list` (debounced)",
    "page_viewed": "`app/components/TelemetryProvider.tsx` (route changes)",
    "flow_started": "`app/components/TelemetryProvider.tsx` (first interaction with a form)",
    "flow_abandoned": "`app/components/TelemetryProvider.tsx` (route change or page hide before completion)",
    "form_validation_failed": "`app/components/TelemetryProvider.tsx` (submit followed by invalid fields)",
    "page_load_recorded": "`app/components/TelemetryProvider.tsx` (Web Vitals)",
    "frontend_error_caught": "`app/components/TelemetryProvider.tsx` and `app/error.tsx`",
    "network_request_failed": "`lib/api-client.ts` -> `apiRequest` (network failure branch)",
}


# --- markdown rendering --------------------------------------------------------------------


def golden_sentence(e, plain=False):
    q = "" if plain else "`"
    return f"We capture {q}{e['type']}{q} because we need to know {q}{e['hypothesis']}{q}, which allows us to make the decision {q}{e['decision']}{q}."


def esc(text):
    return str(text).replace("|", "\\|")


def pii_label(value):
    return {"none": "none", "pseudonymized": "pseudonymised", "sensitive": "sensitive (restricted)"}[value]


def class_label(e):
    return "Mandatory" if e["klass"] == "mandatory" else "Identified opportunity"


def delivery_label(e):
    return f"{e['delivery']} ({e['window']})"


def render_catalogue():
    lines = ["| # | Event | Class | Category | Delivery | Persona or owner |", "|---|---|---|---|---|---|"]
    for i, e in enumerate(sorted(ev.EVENTS, key=order_key), 1):
        lines.append(f"| {i} | `{e['type']}` | {class_label(e)} | {ev.CATEGORIES[e['category']]} | {delivery_label(e)} | {esc(e['consumers'].split(';')[0])} |")
    return "\n".join(lines)


def order_key(e):
    cat_order = list(ev.CATEGORIES)
    klass = 0 if e["klass"] == "mandatory" else 1
    mand = MANDATORY_ORDER.index(e["type"]) if e["type"] in MANDATORY_ORDER else 0
    return (cat_order.index(e["category"]), klass, mand)


def render_statements():
    out = []
    for key, title in ev.CATEGORIES.items():
        group = sorted([e for e in ev.EVENTS if e["category"] == key], key=order_key)
        out.append(f"#### {title}\n")
        for e in group:
            tag = "**Mandatory.**" if e["klass"] == "mandatory" else "Opportunity."
            out.append(f"- {tag} {golden_sentence(e)}")
        out.append("")
    return "\n".join(out).rstrip()


def render_inventory_points():
    rows = ["| # | Instrumentation point | Event | Why there |", "|---|---|---|---|"]
    points = [
        ("`create_stock_entry()` after commit", "inbound_order_created", "Count a receipt only once it is stored; a rolled-back receipt must not inflate volume."),
        ("`create_stock_exit()` after commit", "outbound_order_created", "Same rule for dispatches and losses; also the moment the new stock level is known."),
        ("`create_stock_exit()` after the movement, comparing stock before and after", "stock_threshold_triggered", "The only place where a crossing of the minimum is observable; edge-triggered."),
        ("Handler of the 405 on `PUT`, `PATCH`, `DELETE /inventory/products/{sku_id}`", "direct_stock_edit_rejected", "Direct edit attempt on the product routes: today refused but invisible."),
        ("Payload guard in `create_product()` (stock fields in the body)", "direct_stock_edit_rejected", "Today the stock value is silently dropped (201). Rejecting it with 400 makes the attempt visible."),
        ("`_sku_or_404()` and the 422 handler of the inbound route", "inbound_order_rejected", "Failed validations on receipts, by SKU and warehouse."),
        ("The 400, 404 and 422 paths of `create_stock_exit()`", "outbound_order_rejected", "Failed validations on dispatches, including insufficient stock."),
        ("Warehouse-versus-SKU check and negative-stock check in the movement routes, plus a periodic scan", "stock_integrity_violation_detected", "Protect the invariants the stock computation relies on."),
        ("New stock-count flow (to be built)", "inventory_discrepancy_detected", "Compare physical count with system stock; reconcile through an adjustment order."),
        ("`create_product()` after commit", "product_created", "Catalogue growth per client, warehouse and category."),
    ]
    for i, (where, event, why) in enumerate(points, 1):
        rows.append(f"| {i} | {where} | `{event}` | {why} |")
    return "\n".join(rows)


def render_property_table(e):
    lines = ["| Property | Type | Req. | PII | Description and how it is obtained |", "|---|---|---|---|---|"]
    for p in e["properties"]:
        how = f" **Source:** {p['how']}" if p["how"] else ""
        lines.append(f"| `{p['name']}` | {esc(describe_type(p))} | {'yes' if p['required'] else 'no'} | {pii_label(p['pii'])} | {esc(p['description'])}{esc(how)} |")
    return "\n".join(lines)


def describe_type(p):
    s = p["schema"]
    base = p["label"]
    extras = []
    if "enum" in s and s["type"] != "array":
        values = [v for v in s["enum"] if v is not None]
        extras.append("one of " + ", ".join(f"`{v}`" for v in values))
    if s.get("type") == "array":
        extras.append("items: " + ", ".join(f"`{v}`" for v in s["items"]["enum"]))
    if "pattern" in s:
        extras.append(f"pattern `{s['pattern']}`")
    for key, symbol in (("minimum", ">="), ("maximum", "<="), ("exclusiveMinimum", ">"), ("maxLength", "max length")):
        if key in s:
            extras.append(f"{symbol} {s[key]}")
    if s.get("type") == "object":
        extras.append("keys: " + ", ".join(f"`{k}`" for k in s["properties"]))
    return base + ("; " + "; ".join(extras) if extras else "")


def render_event_detail():
    out = []
    for key, title in ev.CATEGORIES.items():
        out.append(f"### {title}\n")
        for e in sorted([x for x in ev.EVENTS if x["category"] == key], key=order_key):
            out.append(f"#### `{e['type']}`\n")
            out.append(f"{e['description']}\n")
            out.append(f"> {golden_sentence(e)}\n")
            out.append(f"- **Class:** {class_label(e)}")
            out.append(f"- **Produced by:** {', '.join(e['source'])}")
            out.append(f"- **Fires when:** {e['trigger']}")
            out.append(f"- **Instrument at:** {e['emit_at']}")
            out.append(f"- **Used by:** {e['consumers']}")
            out.append(f"- **Delivery:** {delivery_label(e)}. {e['delivery_reason']}")
            if e.get("throttle"):
                out.append(f"- **Volume control:** {e['throttle']}")
            out.append(f"- **Personal data:** {pii_label(e['pii'])}. {e['pii_note']}")
            if e.get("notes"):
                out.append(f"- **Notes:** {e['notes']}")
            out.append("")
            out.append(render_property_table(e))
            out.append("")
            envelope_example = {
                "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
                "timestamp": "2026-11-21T14:03:22.418Z",
                "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
                "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
                "event_type": e["type"],
                "schemaVersion": ev.SCHEMA_VERSION,
                "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
                "properties": e["example"],
            }
            out.append("Example:\n")
            out.append("```json")
            out.append(json.dumps(envelope_example, indent=2, ensure_ascii=False))
            out.append("```\n")
    return "\n".join(out).rstrip()


def render_envelope_table():
    lines = ["| Field | Type | Req. | Meaning |", "|---|---|---|---|"]
    for name, typ, req, meaning in ENVELOPE_FIELDS:
        lines.append(f"| `{name}` | {typ} | {req} | {esc(meaning)} |")
    return "\n".join(lines)


def render_verbs():
    lines = ["| Verb | Use it when |", "|---|---|"]
    for verb, meaning in ev.VERBS.items():
        lines.append(f"| `{verb}` | {meaning} |")
    return "\n".join(lines)


def render_delivery_table():
    lines = ["| Event | Mode | Window | Why this mode (decision urgency) |", "|---|---|---|---|"]
    for e in sorted(ev.EVENTS, key=lambda x: (x["delivery"] != "stream", order_key(x))):
        lines.append(f"| `{e['type']}` | {e['delivery']} | {e['window']} | {esc(e['delivery_reason'])} |")
    return "\n".join(lines)


def render_volume_table():
    lines = ["| Event | Rule |", "|---|---|"]
    for e in ev.EVENTS:
        if e.get("throttle"):
            lines.append(f"| `{e['type']}` | {esc(e['throttle'])} |")
    return "\n".join(lines)


def render_pii_table():
    lines = ["| Event | Property | Treatment | How it is sanitised |", "|---|---|---|---|"]
    for e in ev.EVENTS:
        for p in e["properties"]:
            if p["pii"] != "none":
                lines.append(f"| `{e['type']}` | `{p['name']}` | {pii_label(p['pii'])} | {esc(p['how'] or p['description'])} |")
    return "\n".join(lines)


def render_allowlist_summary():
    lines = ["| Event | Allowed properties (`*` = optional) |", "|---|---|"]
    for e in sorted(ev.EVENTS, key=order_key):
        names = ", ".join(f"`{p['name']}{'' if p['required'] else '*'}`" for p in e["properties"])
        lines.append(f"| `{e['type']}` | {names} |")
    return "\n".join(lines)


def render_coverage():
    lines = ["| Category | Events | Mandatory | Opportunities | Stream | Batch |", "|---|---|---|---|---|---|"]
    for key, title in ev.CATEGORIES.items():
        g = [e for e in ev.EVENTS if e["category"] == key]
        lines.append(
            f"| {title} | {len(g)} | {sum(e['klass'] == 'mandatory' for e in g)} | {sum(e['klass'] != 'mandatory' for e in g)} | "
            f"{sum(e['delivery'] == 'stream' for e in g)} | {sum(e['delivery'] == 'batch' for e in g)} |"
        )
    total = ev.EVENTS
    lines.append(
        f"| **Total** | **{len(total)}** | **{sum(e['klass'] == 'mandatory' for e in total)}** | **{sum(e['klass'] != 'mandatory' for e in total)}** | "
        f"**{sum(e['delivery'] == 'stream' for e in total)}** | **{sum(e['delivery'] == 'batch' for e in total)}** |"
    )
    return "\n".join(lines)


READINESS = {
    "stock_threshold_triggered": "After prerequisite 1",
    "inventory_discrepancy_detected": "After prerequisite 2",
    "direct_stock_edit_rejected": "Partly: the `405` channel now, the payload channel after prerequisite 3",
    "stock_integrity_violation_detected": "After prerequisite 4",
}


def render_quickref():
    lines = ["| Event | Produced by | Instrument at | Fires when | Mode | Ready |", "|---|---|---|---|---|---|"]
    for e in sorted(ev.EVENTS, key=order_key):
        ready = READINESS.get(e["type"], "Yes")
        lines.append(
            f"| `{e['type']}` | {', '.join(e['source'])} | {esc(e['emit_at'])} | {esc(e['trigger'])} | {e['delivery']} | {esc(ready)} |"
        )
    return "\n".join(lines)


def render_frontend_status():
    lines = ["| Event | Class | Phase 2 | Where |", "|---|---|---|---|"]
    for e in sorted(ev.EVENTS, key=order_key):
        where = FRONTEND_IMPLEMENTATION.get(e["type"])
        status = "Backoffice (`uis/backoffice`)" if where else "Backend, Phase 3"
        lines.append(f"| `{e['type']}` | {class_label(e)} | {status} | {where or 'Needs a server-side producer.'} |")
    return "\n".join(lines)


def render_ts_schema():
    def clean(schema):
        drop = {"description"}
        return {k: (clean(v) if isinstance(v, dict) else v) for k, v in schema.items() if k not in drop and not k.startswith("x-")}

    entries = {}
    for e in ev.EVENTS:
        entries[e["type"]] = {
            "delivery": e["delivery"],
            "required": [p["name"] for p in e["properties"] if p["required"]],
            "properties": {p["name"]: clean(p["schema"]) for p in e["properties"]},
        }
    header = (
        "// GENERATED by docs/telemetry/build.py from docs/telemetry/events.py. Do not edit by hand.\n"
        "// Run `python docs/telemetry/build.py` after changing an event.\n\n"
        f'export const TELEMETRY_SCHEMA_VERSION = "{ev.SCHEMA_VERSION}";\n\n'
        "export interface PropertySchema {\n"
        "  type: string | string[];\n"
        "  enum?: (string | null)[];\n"
        "  pattern?: string;\n"
        "  minimum?: number;\n"
        "  maximum?: number;\n"
        "  exclusiveMinimum?: number;\n"
        "  maxLength?: number;\n"
        "  minItems?: number;\n"
        "  uniqueItems?: boolean;\n"
        "  items?: PropertySchema;\n"
        "  properties?: Record<string, PropertySchema>;\n"
        "  additionalProperties?: boolean;\n"
        "}\n\n"
        "export interface EventSchema {\n"
        '  delivery: "stream" | "batch";\n'
        "  required: string[];\n"
        "  properties: Record<string, PropertySchema>;\n"
        "}\n\n"
    )
    body = "export const EVENT_SCHEMAS: Record<string, EventSchema> = " + json.dumps(entries, indent=2, ensure_ascii=False) + ";\n"
    return header + body


def _clean_schema(schema):
    """A property schema without documentation keywords: only what a validator needs."""
    return {k: (_clean_schema(v) if isinstance(v, dict) else v) for k, v in schema.items() if k != "description" and not k.startswith("x-")}


def render_py_catalogue():
    """services/telemetry_catalogue.py: what the collector needs to validate and store each event (the API image has no docs/ folder)."""
    import pprint

    major = int(ev.SCHEMA_VERSION.split(".")[0])
    entries = {
        e["type"]: {
            "level": e["level"],
            "value": e["value_property"],
            "message": e["description"],
            "required": [p["name"] for p in e["properties"] if p["required"]],
            "properties": {p["name"]: _clean_schema(p["schema"]) for p in e["properties"]},
        }
        for e in ev.EVENTS
    }
    header = (
        '"""GENERATED by docs/telemetry/build.py from docs/telemetry/events.py. Do not edit by hand.\n\n'
        "Run `python docs/telemetry/build.py` after changing an event. Used by `telemetry_store.py` to validate\n"
        'each event against the approved plan and to map it to a `telemetry_events` row."""\n\n'
        f"SCHEMA_MAJOR = {major}\n\n"
        "EVENT_CATALOGUE = "
    )
    return header + pprint.pformat(entries, width=130, sort_dicts=False) + "\n"


def render_storage_mapping():
    lines = ["| Event | `level` | `value` comes from | `message` |", "|---|---|---|---|"]
    for e in sorted(ev.EVENTS, key=order_key):
        value = f"`{e['value_property']}`" if e["value_property"] else "null"
        lines.append(f"| `{e['type']}` | {e['level']} | {value} | {esc(e['description'])} |")
    return "\n".join(lines)


def render_vocab():
    return (
        f"- Warehouses: {', '.join(f'`{w}`' for w in ev.WAREHOUSES)} (country `US` and `ES`).\n"
        f"- Product categories: {', '.join(f'`{c}`' for c in ev.PRODUCT_CATEGORIES)}.\n"
        f"- Roles: {', '.join(f'`{r}`' for r in ev.USER_ROLES)}."
    )


PLACEHOLDERS = {
    "ENVELOPE_TABLE": render_envelope_table,
    "VERB_TABLE": render_verbs,
    "COVERAGE_TABLE": render_coverage,
    "CATALOGUE_TABLE": render_catalogue,
    "STATEMENTS": render_statements,
    "INVENTORY_POINTS": render_inventory_points,
    "EVENT_DETAIL": render_event_detail,
    "ALLOWLIST_SUMMARY": render_allowlist_summary,
    "PII_TABLE": render_pii_table,
    "DELIVERY_TABLE": render_delivery_table,
    "VOLUME_TABLE": render_volume_table,
    "VOCAB": render_vocab,
    "STORAGE_MAPPING": render_storage_mapping,
    "FRONTEND_STATUS": render_frontend_status,
    "QUICKREF": render_quickref,
    "SCHEMA_VERSION": lambda: ev.SCHEMA_VERSION,
    "EVENT_COUNT": lambda: str(len(ev.EVENTS)),
}


def build_plan():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, fn in PLACEHOLDERS.items():
        text = text.replace("{{" + key + "}}", fn())
    if "{{" in text:
        raise SystemExit("Unreplaced placeholder in template: " + text[text.index("{{"):][:40])
    return text.replace("\r\n", "\n")


def write_lf(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def main():
    plan = build_plan()
    schema = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
    ts_schema = render_ts_schema()
    py_catalogue = render_py_catalogue()
    if "--check" in sys.argv:
        stale = [
            p.name for p, c in ((PLAN_PATH, plan), (SCHEMA_PATH, schema), (TS_SCHEMA_PATH, ts_schema), (PY_CATALOGUE_PATH, py_catalogue))
            if not p.exists() or p.read_bytes().replace(b"\r\n", b"\n").decode("utf-8") != c
        ]
        if stale:
            print("Out of date, run `python docs/telemetry/build.py`:", ", ".join(stale))
            return 1
        print("telemetry docs are up to date")
        return 0
    write_lf(PLAN_PATH, plan)
    write_lf(SCHEMA_PATH, schema)
    write_lf(TS_SCHEMA_PATH, ts_schema)
    write_lf(PY_CATALOGUE_PATH, py_catalogue)
    print(f"wrote {PLAN_PATH.name} ({len(ev.EVENTS)} events), {SCHEMA_PATH.name} and {TS_SCHEMA_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
