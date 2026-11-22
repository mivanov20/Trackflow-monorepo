"""Indexes docs/company-knowledge-base/ into the Qdrant collection `trackflow_knowledge` (safe to run again).

    python scripts/index_knowledge_base.py

Needs QDRANT_URL (default http://localhost:6333), LLM_API_URL, LLM_API_KEY and EMBEDDING_MODEL (see .env.example).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.process import knowledge_base  # noqa: E402


def main() -> int:
    print(json.dumps(knowledge_base.setup(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
