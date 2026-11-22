"""How many times was each guardrail triggered? A summary of the guardrail event log, from the command line.

    python scripts/guardrail_report.py [--since 2026-11-22T10:00:00Z] [--events 10]

Uses DATABASE_URL like the API. The same numbers are served by `GET /agent/guardrails/summary` (admin or manager).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent.guardrails import events  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="only events since this ISO 8601 moment (a test session)")
    parser.add_argument("--events", type=int, default=0, help="also list the latest N events")
    args = parser.parse_args(argv)
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None
    summary = events.summary(since=since)
    print(f"guardrail events: {summary['total']}" + (f" (since {args.since})" if args.since else ""))
    print(f"by type of failure: {summary['by_failure_type']}")
    print(f"by action: {summary['by_action']}")
    for guard, entry in summary["by_guard"].items():
        print(f"  {guard:24s} {entry['failure_type']:10s} {entry['count']:4d}  {entry['actions']}")
    for event in events.recent(limit=args.events) if args.events else []:
        print(f"  {event['created_at'][:19]}  {event['guard']:22s} {event['failure_type']:10s} {event['action']:10s} {event['excerpt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
