"""Optional, non-critical step: a JSON snapshot of the KPI rows a run loaded, for people who want a file (design 4.1)."""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import Engine

from .config import PIPELINE_NAME, SNAPSHOT_DIR_ENV
from .queries import get_weekly_performance

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "process" / "weekly-performance-snapshots"


def write_snapshot(engine: Engine, weeks: list[date], directory: Path | None = None) -> list[str]:
    """Writes one `<pipeline>_<week_start>.json` per week and returns the file paths."""
    target = Path(directory or os.getenv(SNAPSHOT_DIR_ENV) or DEFAULT_DIR)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for week in weeks:
        payload = get_weekly_performance(engine, week) or {"week_start": week.isoformat(), "entries": []}
        payload["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = target / f"{PIPELINE_NAME}_{week.isoformat()}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    return written
