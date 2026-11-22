"""data/pipelines/PIPELINE_DESIGN.md: the design has every required part and agrees with the telemetry plan."""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN = (REPO_ROOT / "data" / "pipelines" / "PIPELINE_DESIGN.md").read_text(encoding="utf-8")
SCHEMAS = json.loads((REPO_ROOT / "docs" / "telemetry" / "event-schemas.json").read_text(encoding="utf-8"))["events"]

KPI_EVENTS = ["inbound_order_created", "outbound_order_created", "stock_threshold_triggered", "inventory_discrepancy_detected"]
KPIS = ["Inbound Volume", "Outbound Throughput", "Stockout Frequency", "Discrepancy Rate"]
DESTINATION_COLUMNS = [
    "id uuid primary key default gen_random_uuid()",
    "warehouse text not null",
    "client_id text not null",
    "week_start date not null",
    "inbound_units_count integer not null default 0",
    "outbound_orders_count integer not null default 0",
    "stockout_events_count integer not null default 0",
    "discrepancy_events_count integer not null default 0",
    "discrepancy_rate numeric not null default 0",
    "computed_at timestamptz not null default now()",
    "unique (warehouse, client_id, week_start)",
]


def section(title: str) -> str:
    """Text of the `## ` or `### ` section that starts with `title`, up to the next heading of the same or higher level."""
    match = re.search(rf"^(#{{2,3}}) {re.escape(title)}.*?$", DESIGN, re.M)
    assert match, f"missing section: {title}"
    level = len(match.group(1))
    rest = DESIGN[match.end():]
    end = re.search(rf"^#{{2,{level}}} ", rest, re.M)
    return rest[: end.start()] if end else rest


def table_rows(text: str) -> list[list[str]]:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in text.splitlines() if line.startswith("|")]
    return [row for row in rows if not set("".join(row)) <= set("-: ")][1:]  # without the header and its separator


def test_the_design_exists_and_the_entry_point_it_documents_exists():
    assert (REPO_ROOT / "data" / "pipelines" / "PIPELINE_DESIGN.md").is_file()
    assert (REPO_ROOT / "data" / "pipelines" / "pipeline.py").is_file()
    assert "python data/pipelines/pipeline.py" in section("9. Implementation") and "0 4 * * 1" in section("9. Implementation")


def test_current_state_and_the_business_gap():
    current = section("1. Current state")
    for needle in ("telemetry_events", "track(", "services/telemetry/analysis.py", "GET /telemetry/report"):
        assert needle in current, needle
    gap = section("1.4 The gap")
    assert "does not" in gap.lower() or "not enough" in gap.lower()
    assert "weekly" in gap.lower() or "week" in gap.lower()


def test_purpose_is_one_sentence_naming_the_deliverable_the_kpis_and_the_metrics():
    purpose = section("2.1 Purpose").strip().lstrip("> ").strip()
    assert purpose.count("\n") == 0, "the purpose is a single sentence"
    assert purpose.count(". ") == 0
    assert "Weekly Warehouse & Client Performance Report" in purpose
    assert "Thomas" in purpose and "Ana" in purpose
    for name in KPIS + KPI_EVENTS + ["reporting.weekly_warehouse_client_performance"]:
        assert name in purpose, name


def test_extraction_format_source_payload_and_cadence():
    extraction = section("2.3 Extraction format")
    assert "FROM telemetry_events" in extraction and "event_type IN" in extraction
    assert "timestamp >= :window_start" in extraction and "timestamp <  :window_end" in extraction
    for column in ("warehouse", "client_id", "quantity", "exit_type", "event_id"):
        assert f"`{column}`" in extraction
    assert "weekly" in extraction.lower() and "Monday 04:00 UTC" in extraction


def test_data_flow_diagram_has_three_stages_with_real_names():
    diagram = re.search(r"```mermaid\n(.*?)```", section("2.4 Data flow"), re.S)
    assert diagram, "no mermaid diagram"
    text = diagram.group(1)
    for needle in ("EXTRACT", "TRANSFORM", "LOAD", "telemetry_events", "reporting.weekly_warehouse_client_performance", "reporting.pipeline_runs"):
        assert needle in text, needle


def test_update_strategy_is_a_concrete_mechanism():
    updates = section("2.6 Handling a source that updates existing records")
    for needle in ("ON CONFLICT (warehouse, client_id, week_start) DO UPDATE", "IS DISTINCT FROM", "two complete weeks", "eventId", "append-only"):
        assert needle in updates, needle


def test_destination_matches_the_company_context_and_stays_out_of_telemetry_events():
    destination = section("2.7 Destination")
    normalised = re.sub(r"\s+", " ", destination.lower())
    for column in DESTINATION_COLUMNS:
        assert re.sub(r"\s+", " ", column) in normalised, column
    assert "create schema if not exists reporting" in destination.lower()
    for statement in re.findall(r"(?is)\b(insert into|update|delete from|create table)\s+([\w.]+)", "\n".join(re.findall(r"```sql\n(.*?)```", DESIGN, re.S))):
        assert statement[1].startswith("reporting"), f"a statement writes to {statement[1]}"


def test_idempotency_describes_the_second_run_after_a_load_failure():
    idempotency = section("3.1 Idempotency strategy")
    for needle in ("Second run", "rolls back", "unique key", "ON CONFLICT", "no duplicate", "empty_extract_guard"):
        assert needle.lower() in idempotency.lower(), needle


def test_execution_log_has_at_least_five_fields_with_type_and_justification():
    rows = table_rows(section("3.3 Execution log").split("Example of")[0])
    assert len(rows) >= 5
    for field, data_type, why in rows:
        assert field.startswith("`") and data_type and len(why) > 15, field
    names = {row[0].strip("`") for row in rows}
    assert {"started_at", "finished_at", "status", "error_message"} <= names
    assert names & {"records_extracted", "rows_inserted"}, "records processed"


def test_prefect_mapping_has_a_flow_and_at_least_three_tasks_and_states():
    prefect = section("4. Mapping to Prefect")
    assert "weekly_warehouse_client_performance_flow" in prefect
    tasks = re.findall(r"\| (?:Optional )?[Tt]ask \| `(\w+)`", prefect)
    assert {"extract_telemetry_events", "transform_weekly_rollup", "load_weekly_rollup", "write_report_snapshot"} <= set(tasks)
    assert "return_state=True" in prefect and "cache_key_fn" in prefect
    for state in ("Running", "Completed", "Failed"):
        assert f"**{state}**" in prefect
    blocks = section("4.4 Blocks")
    assert "Secret" in blocks and "supabase" in blocks


def test_three_endpoints_each_naming_the_pipeline_function_it_calls():
    integration = section("5. Application integration")
    rows = table_rows(integration.split("Access:")[0].split("Planned layout")[1].split("| Endpoint")[1].join(["| Endpoint", ""]))
    paths = {
        "GET /reporting/weekly-warehouse-client-performance": "get_weekly_performance",
        "GET /reporting/pipeline-runs/latest": "get_latest_run",
        "POST /reporting/pipeline-runs": "trigger_weekly_performance_run",
    }
    for path, function in paths.items():
        row = next((r for r in rows if r[0].startswith(f"`{path}")), None)
        assert row, path
        assert function in row[1] and "data/pipelines" in integration
    assert "services/reporting/" in integration and "no etl logic" in integration.lower()


def test_the_design_uses_only_telemetry_that_already_exists_and_adds_no_payload_field():
    properties = ("warehouse", "client_id", "quantity")
    for event in KPI_EVENTS:
        schema = SCHEMAS[event]
        assert schema["x-class"] == "mandatory", event
        allowed = schema["allOf"][1]["properties"]["properties"]["properties"]
        for name in properties if event != "stock_threshold_triggered" else ("warehouse", "client_id", "severity"):
            assert name in allowed, f"{event} has no `{name}`"
    outbound = SCHEMAS["outbound_order_created"]["allOf"][1]["properties"]["properties"]["properties"]
    assert outbound["exit_type"]["enum"] == ["dispatch", "loss"]
    assert "no new payload field" in DESIGN.lower()


def test_the_technical_report_path_is_declared_untouched():
    assert "Left untouched" in DESIGN and "services/telemetry/analysis.py" in DESIGN and "GET /telemetry/report" in DESIGN
    assert "read-only" in DESIGN.lower()
