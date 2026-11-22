"""Weekly Warehouse & Client Performance pipeline: the Prefect flows, their tasks and the command line entry point.

It reads the four mandatory metrics from `telemetry_events` (read-only) and writes the KPIs of the company CONTEXT to
`reporting.weekly_warehouse_client_performance`, one row per warehouse, client and ISO week. Design and decisions:
data/pipelines/PIPELINE_DESIGN.md.

Structure: the main flow `weekly_warehouse_client_performance_flow` only coordinates. Each phase is a subflow with
explicit inputs and outputs, which can also be run on its own:

    extract_weekly_performance_events_flow          telemetry_events -> raw events
    transform_weekly_warehouse_client_kpis_flow     raw events -> Inbound Volume, Outbound Throughput,
                                                    Stockout Frequency and Discrepancy Rate per warehouse and client
    load_weekly_warehouse_client_performance_flow   rollups -> reporting.weekly_warehouse_client_performance
    check_telemetry_silence_flow                    optional: warns when the source sent nothing
    publish_weekly_performance_snapshot_flow        optional: a JSON file of the loaded KPIs

Run it (from the repository root, with DATABASE_URL pointing to the Supabase PostgreSQL):

    python data/pipelines/pipeline.py                       # the last 2 complete weeks (what the schedule runs)
    python data/pipelines/pipeline.py --week-start 2026-11-09   # recompute one week
    python data/pipelines/pipeline.py --weeks-back 8 --trigger backfill
    python data/pipelines/pipeline.py --serve               # keep running and fire every Monday 04:00 UTC

The reporting cycle is weekly: Monday 04:00 UTC (cron `0 4 * * 1`), when the previous UTC week is complete and before
Monday morning in Los Angeles and Zaragoza.
"""

import argparse
import hashlib
import json
import logging
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):  # run as a script: the repository root must be importable (`data.pipelines...`)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run
from sqlalchemy.exc import InterfaceError, OperationalError

from data.pipelines.weekly_performance import config, extract, load, runs, snapshot, transform, window

logger = logging.getLogger("data.pipelines.weekly_performance")

# Errors that can heal by themselves (a dropped connection, a timeout). Everything else (a failed data-quality rule,
# the empty-extract guard, a second run already in progress) would fail again, so retrying it only wastes minutes.
TRANSIENT_ERRORS = (OperationalError, InterfaceError, OSError)


def _transient_only(task_obj, task_run, state) -> bool:
    try:
        error = state.result(raise_on_failure=False)
    except Exception:  # noqa: BLE001 - if the cause cannot be read, retrying is the safe choice
        return True
    return isinstance(error, TRANSIENT_ERRORS)


class PipelineStageFailed(RuntimeError):
    """A critical stage failed after its retries; the run log already holds the details."""


class PartialLoadFailure(RuntimeError):
    """Some weeks failed to load while the others were committed. Rerunning repairs them (the load is idempotent)."""


class TelemetrySilence(RuntimeError):
    """The source returned no event at all for the weeks of the run: the producers may have stopped, not the pipeline."""


# --- tasks: run log ---------------------------------------------------------------------------------------------


# Touches the database. 2 retries, 5 s then 30 s: the usual cause is a connection that has just dropped and comes
# back within seconds; more waiting would only delay the failure being recorded. Not retried when a run is in progress.
@task(name="open_run", retries=2, retry_delay_seconds=[5, 30], retry_condition_fn=_transient_only)
def open_run(trigger: str, triggered_by: str | None, weeks: list[date], run_id: str | None = None) -> str:
    """Closes abandoned runs and records this one as `running` (or adopts the run a manual trigger already opened)."""
    opened = runs.open_run(
        config.reporting_engine(),
        trigger=trigger,
        triggered_by=triggered_by,
        window_start=weeks[0],
        window_end=weeks[-1],
        run_id=uuid.UUID(run_id) if run_id else None,
        prefect_flow_run_id=flow_run.id,
    )
    return str(opened)


# The log must be written even when the data stages struggle: 2 retries on a database blip.
@task(name="close_run", retries=2, retry_delay_seconds=[5, 30], retry_condition_fn=_transient_only)
def close_run(
    run_id: str,
    status: str,
    counters: dict[str, int | None],
    newest: datetime | None,
    optional_steps_failed: int = 0,
    error_stage: str | None = None,
    error: BaseException | None = None,
) -> None:
    """Writes the final state of the run to `reporting.pipeline_runs`."""
    runs.close_run(
        config.reporting_engine(),
        uuid.UUID(run_id),
        status=status,
        counters=counters,
        source_max_timestamp=newest,
        optional_steps_failed=optional_steps_failed,
        error_stage=error_stage,
        error=error,
    )


# --- extraction -------------------------------------------------------------------------------------------------


# Reads the source database: 3 retries waiting 30 s, 2 min and 10 min. Extracting twice is harmless (read-only), the job
# runs weekly with hours of slack before anyone reads the report, and a database restart can take several minutes.
@task(name="extract_telemetry_events", retries=3, retry_delay_seconds=[30, 120, 600], retry_condition_fn=_transient_only)
def extract_telemetry_events(window_start: datetime, window_end: datetime) -> dict:
    """EXTRACT: the four mandatory event types of the window, with the newest timestamp read."""
    raw = extract.extract_events(config.telemetry_engine(), window_start, window_end)
    return {"raw": raw, "newest": extract.newest_timestamp(raw)}


@flow(name="extract_weekly_performance_events_flow")
def extract_weekly_performance_events_flow(window_start: datetime, window_end: datetime) -> dict:
    """Subflow: `inbound_order_created`, `outbound_order_created`, `stock_threshold_triggered` and
    `inventory_discrepancy_detected` of `[window_start, window_end)`. Returns `{"raw": DataFrame, "newest": datetime | None}`."""
    return extract_telemetry_events(window_start, window_end)


# --- transformation: one task per KPI of the company CONTEXT ----------------------------------------------------


def transform_cache_key(context, parameters) -> str:
    """Cache key of the prepare task: a fingerprint of WHAT it would compute.

    It is the hash of (the version of the transformation rules, the weeks and every extracted event: timestamp, type,
    value, tags). The same events and rules give the same key, so a rerun within the hour reuses the previous result;
    one new event, a different week set or a changed rule gives a new key and recomputes.
    """
    raw: pd.DataFrame = parameters["raw"]
    digest = hashlib.sha256()
    digest.update(transform.TRANSFORM_VERSION.encode())
    digest.update(",".join(week.isoformat() for week in parameters["weeks"]).encode())
    hashable = raw.assign(tags=raw["tags"].map(lambda tags: json.dumps(tags, sort_keys=True, default=str)))
    digest.update(pd.util.hash_pandas_object(hashable, index=False).values.tobytes())
    return digest.hexdigest()


# The expensive, pure step (refine, de-duplicate, type, check). Cached for 1 hour (cache_expiration): if the same events
# were prepared successfully in the last hour, the result is reused and the Pandas work is skipped. No retries: it is
# deterministic, so a retry fails alike.
@task(
    name="prepare_telemetry_events",
    cache_key_fn=transform_cache_key,
    cache_expiration=timedelta(hours=1),
    persist_result=True,
    retries=0,
)
def prepare_telemetry_events(raw: pd.DataFrame, weeks: list[date]) -> dict:
    """Usable events with their week, and the counters of what was dropped. Raises DataQualityError on untrustworthy data."""
    return transform.prepare_events(raw, weeks)


@task(name="compute_inbound_volume")
def compute_inbound_volume(events: pd.DataFrame) -> pd.DataFrame:
    """Inbound Volume: units received per warehouse, client and week."""
    return transform.inbound_volume(events)


@task(name="compute_outbound_throughput")
def compute_outbound_throughput(events: pd.DataFrame, count_loss_exits: bool = config.COUNT_LOSS_EXITS) -> pd.DataFrame:
    """Outbound Throughput: orders picked and dispatched per warehouse, client and week."""
    return transform.outbound_throughput(events, count_loss_exits)


@task(name="compute_stockout_frequency")
def compute_stockout_frequency(events: pd.DataFrame) -> pd.DataFrame:
    """Stockout Frequency: times a SKU fell below its minimum per warehouse, client and week."""
    return transform.stockout_frequency(events)


@task(name="count_discrepancy_events")
def count_discrepancy_events(events: pd.DataFrame) -> pd.DataFrame:
    """Detected inventory discrepancies per warehouse, client and week (the numerator of the Discrepancy Rate)."""
    return transform.discrepancy_events(events)


@task(name="compute_discrepancy_rate")
def compute_discrepancy_rate(outbound: pd.DataFrame, discrepancies: pd.DataFrame) -> pd.DataFrame:
    """Discrepancy Rate: discrepancy events over outbound orders, 0 when a week has no outbound orders."""
    return transform.discrepancy_rate(outbound, discrepancies)


@task(name="assemble_weekly_warehouse_client_rollup")
def assemble_weekly_warehouse_client_rollup(
    inbound: pd.DataFrame, outbound: pd.DataFrame, stockouts: pd.DataFrame, discrepancy: pd.DataFrame, weeks: list[date]
) -> dict:
    """The rows of the destination table, one frame per week."""
    return transform.assemble_weekly_rollup(inbound, outbound, stockouts, discrepancy, weeks)


@flow(name="transform_weekly_warehouse_client_kpis_flow")
def transform_weekly_warehouse_client_kpis_flow(raw: pd.DataFrame, weeks: list[date], count_loss_exits: bool = config.COUNT_LOSS_EXITS) -> dict:
    """Subflow: raw events to the four KPIs of the company CONTEXT.

    Returns `{"rollups": {week_start: DataFrame}, "counters": {...}}`. It touches no database.
    """
    prepared = prepare_telemetry_events(raw, weeks)
    events = prepared["events"]
    inbound = compute_inbound_volume(events)
    outbound = compute_outbound_throughput(events, count_loss_exits)
    stockouts = compute_stockout_frequency(events)
    discrepancy = compute_discrepancy_rate(outbound, count_discrepancy_events(events))
    return {"rollups": assemble_weekly_warehouse_client_rollup(inbound, outbound, stockouts, discrepancy, weeks), "counters": prepared["counters"]}


# --- load -------------------------------------------------------------------------------------------------------


# Writes the database. 2 retries, 30 s then 2 min: safe because the load is idempotent (upsert on the unique key, one
# transaction per week), so a retry after a half-finished attempt cannot duplicate or corrupt anything.
@task(name="load_weekly_rollup", retries=2, retry_delay_seconds=[30, 120], retry_condition_fn=_transient_only)
def load_weekly_rollup(week: date, rollup: pd.DataFrame) -> dict:
    """LOAD: replaces one week of the destination table inside one transaction."""
    return load.load_week(config.reporting_engine(), week, rollup)


@flow(name="load_weekly_warehouse_client_performance_flow")
def load_weekly_warehouse_client_performance_flow(rollups: dict, weeks: list[date]) -> dict:
    """Subflow: stores each week in `reporting.weekly_warehouse_client_performance`.

    A week that fails does not stop the others: it rolled back, and the rerun repairs it. Returns
    `{"totals": {rows_inserted, rows_updated, rows_unchanged, rows_deleted}, "failed_weeks": [{week, error_type, error}]}`.
    """
    log = get_run_logger()
    totals = {"rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}
    failed_weeks: list[dict] = []
    for week in weeks:
        state = load_weekly_rollup(week, rollups[week], return_state=True)
        if state.is_failed():
            error = state.result(raise_on_failure=False)
            type_name, message = runs.sanitize_error(error if isinstance(error, BaseException) else RuntimeError(str(error)))
            failed_weeks.append({"week": week.isoformat(), "error_type": type_name, "error": message})
            log.error("Week %s failed to load and was rolled back: %s: %s", week.isoformat(), type_name, message)
            continue
        for name, amount in state.result().items():
            totals[name] += amount
    return {"totals": totals, "failed_weeks": failed_weeks}


# --- optional, non-critical steps --------------------------------------------------------------------------------


# Optional: a failure here is logged and counted, and the run still completes. Not worth retrying.
@task(name="check_telemetry_silence", retries=0)
def check_telemetry_silence(records_extracted: int) -> None:
    """Raises TelemetrySilence when the source sent no event at all for the weeks of the run (design 3.4)."""
    if records_extracted == 0:
        raise TelemetrySilence("No telemetry event arrived for these weeks: the producers may have stopped, not the pipeline.")


@flow(name="check_telemetry_silence_flow")
def check_telemetry_silence_flow(records_extracted: int) -> None:
    """Optional subflow: lets a silent source show up in the run log instead of as a quiet empty report."""
    check_telemetry_silence(records_extracted)


@task(name="write_report_snapshot", retries=0)
def write_report_snapshot(weeks: list[date]) -> list[str]:
    """A JSON file per loaded week, for people who want a file next to the table."""
    return snapshot.write_snapshot(config.reporting_engine(), weeks)


@flow(name="publish_weekly_performance_snapshot_flow")
def publish_weekly_performance_snapshot_flow(weeks: list[date]) -> list[str]:
    """Optional subflow: the JSON snapshot of the KPIs just loaded. Returns the file paths."""
    return write_report_snapshot(weeks)


# --- the main flow ----------------------------------------------------------------------------------------------


def _outcome(state, stage: str, run_id: str, progress: dict):
    """The result of a critical subflow, or the failure recorded in the run log before it is raised.

    The main flow asks for the subflow's state (`return_state=True`) instead of letting the failure propagate, so it can
    write `failed` with the stage and the cause to the run log first.
    """
    if state.is_failed():
        result = state.result(raise_on_failure=False)
        cause = result if isinstance(result, BaseException) else RuntimeError(str(result))
        close_run(run_id, "failed", progress["counters"], progress["newest"], error_stage=stage, error=cause)
        raise PipelineStageFailed(f"The {stage} stage failed: {cause!r}") from cause
    return state.result()


@flow(name="weekly_warehouse_client_performance_flow")
def weekly_warehouse_client_performance_flow(
    week_start: date | None = None,
    weeks_back: int = config.DEFAULT_WEEKS_BACK,
    trigger: str = "schedule",
    triggered_by: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Coordinates extract -> transform -> load for the requested weeks, with the run recorded from start to end."""
    log = get_run_logger()
    weeks = window.weeks_for_run(week_start, weeks_back)
    window_start, window_end = window.window_bounds(weeks)

    # Critical: with no run row there is no audit trail, so a failure here stops the flow (after its retries).
    run = open_run(trigger, triggered_by, weeks, run_id)
    progress: dict = {"counters": {}, "newest": None}
    stage = "extract"
    try:
        extracted = _outcome(extract_weekly_performance_events_flow(window_start, window_end, return_state=True), "extract", run, progress)
        progress["newest"] = extracted["newest"]
        log.info("Extracted %d events for the weeks %s", len(extracted["raw"]), [w.isoformat() for w in weeks])

        stage = "transform"
        transformed = _outcome(
            transform_weekly_warehouse_client_kpis_flow(extracted["raw"], weeks, config.COUNT_LOSS_EXITS, return_state=True), "transform", run, progress
        )
        progress["counters"] = {k: v for k, v in transformed["counters"].items() if k != "value_mismatches"}
        log.info("Transformed: %s", transformed["counters"])

        stage = "load"
        loaded = _outcome(load_weekly_warehouse_client_performance_flow(transformed["rollups"], weeks, return_state=True), "load", run, progress)
        progress["counters"] = {**progress["counters"], **loaded["totals"]}
        failed_weeks = loaded["failed_weeks"]
        if failed_weeks:
            summary = PartialLoadFailure(
                f"{len(failed_weeks)} of {len(weeks)} weeks failed to load ({', '.join(f['week'] for f in failed_weeks)}); first error {failed_weeks[0]['error_type']}."
            )
            close_run(run, "failed", progress["counters"], progress["newest"], error_stage="load", error=summary)
            raise summary

        # Optional subflows: asked for their state so that a failure is logged and counted instead of interrupting the run.
        silence_state = check_telemetry_silence_flow(progress["counters"]["records_extracted"], return_state=True)
        snapshot_state = publish_weekly_performance_snapshot_flow(weeks, return_state=True)
        optional_failed = 0
        for name, state in (("telemetry silence check", silence_state), ("snapshot", snapshot_state)):
            if state.is_failed():
                optional_failed += 1
                log.warning("The optional %s reported a problem and the run continued: %r", name, state.result(raise_on_failure=False))

        close_run(run, "completed", progress["counters"], progress["newest"], optional_steps_failed=optional_failed)
    except (PipelineStageFailed, PartialLoadFailure):
        raise
    except Exception as error:  # an unexpected failure: still leave the run closed and explained
        close_run(run, "failed", progress["counters"], progress["newest"], error_stage=stage, error=error)
        raise
    return {"run_id": run, "status": "completed", "weeks": [w.isoformat() for w in weeks], "optional_steps_failed": optional_failed, **progress["counters"]}


# --- starting a run from outside (the API) and from the command line ---------------------------------------------


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="weekly-performance-run", daemon=True).start()


def open_manual_run(engine, triggered_by: str | None, week_start: date | None = None, weeks_back: int = config.DEFAULT_WEEKS_BACK) -> str:
    """Validates the weeks and records a manual run as `running`. Returns the run id.

    Raises RunAlreadyRunning if a run is in progress and ValueError for an invalid week. The caller then hands the run to
    something that executes it: a Celery worker (`services/reporting/`) or a thread (`trigger_weekly_performance_run`).
    """
    weeks = window.weeks_for_run(week_start, weeks_back)
    return str(runs.open_run(engine, trigger="manual", triggered_by=triggered_by, window_start=weeks[0], window_end=weeks[-1]))


def run_manual_flow(run_id: str, week_start: date | None = None, weeks_back: int = config.DEFAULT_WEEKS_BACK, triggered_by: str | None = None) -> dict:
    """Runs the flow for a run that `open_manual_run` already recorded. This is what the Celery task calls.

    Whatever happens, the run does not stay `running`: the flow closes it itself, and if it failed before it could (a bad week,
    an exception outside its stages) it is closed here before the error is raised again.
    """
    try:
        return weekly_warehouse_client_performance_flow(week_start=week_start, weeks_back=weeks_back, trigger="manual", triggered_by=triggered_by, run_id=run_id)
    except BaseException as error:
        runs.fail_run_if_running(config.reporting_engine(), uuid.UUID(run_id), error)
        raise


def trigger_weekly_performance_run(
    engine,
    triggered_by: str | None,
    week_start: date | None = None,
    weeks_back: int = config.DEFAULT_WEEKS_BACK,
    launcher: Callable[[Callable[[], None]], None] | None = None,
) -> str:
    """Opens a manual run and starts the flow in a background thread without waiting for it. Returns the run id.

    This is the no-broker way (scripts, tests). The API does not use it: it enqueues a Celery task instead, so the work runs
    in a worker process and not in the API.
    """
    run_id = open_manual_run(engine, triggered_by, week_start, weeks_back)

    def execute() -> None:
        try:
            with config.use_engine(engine):
                run_manual_flow(run_id, week_start, weeks_back, triggered_by)
        except Exception:  # noqa: BLE001 - the failure is already in the run log; a background thread has nowhere to raise
            logger.exception("The manual run %s failed", run_id)

    (launcher or _in_background)(execute)
    return run_id


def main(argv: list[str] | None = None) -> int:
    """Command line entry point. Prints a JSON summary and returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(description="Weekly Warehouse & Client Performance pipeline")
    parser.add_argument("--week-start", type=date.fromisoformat, help="recompute one week (a Monday, YYYY-MM-DD)")
    parser.add_argument("--weeks-back", type=int, default=config.DEFAULT_WEEKS_BACK, help="complete weeks to recompute (default 2)")
    parser.add_argument("--trigger", choices=["schedule", "manual", "backfill"], default="manual")
    parser.add_argument("--serve", action="store_true", help="stay running and fire the flow every Monday at 04:00 UTC")
    args = parser.parse_args(argv)

    if args.serve:
        weekly_warehouse_client_performance_flow.serve(name="weekly-warehouse-client-performance", cron="0 4 * * 1", timezone="UTC", parameters={"trigger": "schedule"})
        return 0
    try:
        summary = weekly_warehouse_client_performance_flow(week_start=args.week_start, weeks_back=args.weeks_back, trigger=args.trigger)
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "error": runs.sanitize_error(error)[1]}, indent=2))
        return 1
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
