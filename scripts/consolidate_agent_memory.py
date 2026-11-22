"""Runs the cleanup of the agent's memory: expire, deduplicate, merge by carrier + country, cap, purge. Safe to run any number of times.

    python scripts/consolidate_agent_memory.py

Run it on a schedule (for example nightly, next to scripts/nightly_export.py). The policy and its reasons are in docs/agent/memory-design.md, section 6.
Uses DATABASE_URL like the API. The audit log of proposals is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent.memory.store import MemoryStore  # noqa: E402


def main() -> int:
    counts = MemoryStore().consolidate()
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
