"""Runs an RFP through the whole workflow (intake, generation and evaluation, human approval, final document) with SIMULATED approvals. Reproducible: no UI, no model, no external service.

    python scripts/rfp_e2e.py                                   the formal sample RFP (ModaViva), every owner approves
    python scripts/rfp_e2e.py path/to/rfp.pdf                    another RFP
    python scripts/rfp_e2e.py --scenario request_changes         an owner asks for a change first
    python scripts/rfp_e2e.py tests/pipelines/fixtures/rfp-requests/trackflow-request-2.pdf --scenario volume_conflict
    python scripts/rfp_e2e.py --trace                            also print which agent did what, in order
    python scripts/rfp_e2e.py --document                         also print the final document

It uses an in-memory database and in-memory checkpoints, so it touches nothing. The approvals go through the same resume entry point the API uses (`Workflow.submit`).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("RFP_CHECKPOINT_DB", ":memory:")
os.environ.setdefault("JWT_SECRET", "e2e-only-secret-with-at-least-32-characters")

from data.process import rag_config  # noqa: E402,F401  (loads .env like the other scripts; the run itself uses the rules unless a model is configured)

DEFAULT_PDF = ROOT / "tests" / "pipelines" / "fixtures" / "rfp-requests" / "trackflow-request-1.pdf"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", nargs="?", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--scenario", choices=("approve", "request_changes", "volume_conflict"), default="approve")
    parser.add_argument("--trace", action="store_true", help="print the trace: which agent did what, in order")
    parser.add_argument("--document", action="store_true", help="print the final document")
    args = parser.parse_args(argv)

    from data.pipelines.rfp_intake import e2e

    result = e2e.run(args.pdf, args.scenario)
    print(f"RFP: {args.pdf.name}  scenario: {args.scenario}")
    print("status timeline: " + " -> ".join(result["timeline"]))
    print(f"final status: {result['status']} (approval: {result['approval_state']})  departments: {', '.join(result['departments']) or '-'}")
    by_part: dict[int, int] = {}
    for event in result["events"]:
        by_part[event["part"]] = by_part.get(event["part"], 0) + 1
    print("trace events by part: " + ", ".join(f"part {part}: {count}" for part, count in sorted(by_part.items())))
    if args.trace:
        for event in result["events"]:
            print(f"  {event['seq']:3d} P{event['part']} {event['at'][11:23]} {event['agent']:28s} {event['node']:18s} {event['department_id'] or '-':10s} -> {str(event['output'])[:90]}")
    if args.document and result["document"]:
        print("\n" + result["document"])
    return 0 if result["status"] in ("done", "discarded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
