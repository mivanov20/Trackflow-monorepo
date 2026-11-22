"""Standalone helpers for the RFP intake (Milestone 9, part 1). They call the same pipeline the API schedules; there is no second HTTP service.

    python scripts/rfp_intake.py smoke path/to/rfp.pdf       convert + classify + analyse a PDF in memory and print the result (touches no database)
    python scripts/rfp_intake.py reprocess <ticket_id>        run the pipeline again for a ticket (for example one marked `failed` after a restart)
    python scripts/rfp_intake.py reprocess --all-failed       the same for every ticket in `failed`
    python scripts/rfp_intake.py list                          the tickets and their status

`reprocess` and `list` use DATABASE_URL like the API. With LLM_API_URL / LLM_API_KEY / LLM_MODEL set the agents use the model, otherwise their rules.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.process import rag_config  # noqa: E402,F401  (loads the repo-root .env, like the API and the other scripts)


def smoke(path: Path) -> int:
    from data.pipelines.rfp_intake import classifier, convert, metadata, orchestrator, reading_cost, synthesizer, workers

    markdown = convert.pdf_to_markdown(path)
    verdict = classifier.classify(markdown)
    print(json.dumps({"classification": verdict}, indent=2, ensure_ascii=False))
    if not verdict["is_rfp"]:
        print("-> discarded: the flow stops here.")
        return 0
    meta = metadata.extract(markdown)
    cost = reading_cost.analyze(markdown)
    sections = [workers.run_worker(task) for task in orchestrator.plan(markdown, meta)]
    result = synthesizer.synthesize(meta, sections, cost)
    print(json.dumps({"metadata": meta, "readability": cost}, indent=2, ensure_ascii=False))
    print("\n" + result["summary"])
    return 0


def _engine():
    from sqlalchemy import create_engine

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL must point to the TrackFlow database.")
    return create_engine(url, pool_pre_ping=True)


def reprocess(ticket_ids: list[str], all_failed: bool) -> int:
    from sqlmodel import Session, select

    from data.pipelines.rfp_intake import run_intake, store
    from services.models import RfpTicket

    engine = _engine()
    with Session(engine) as session:
        if all_failed:
            ticket_ids = [ticket.ticket_id for ticket in session.exec(select(RfpTicket).where(RfpTicket.status == "failed"))]
        for ticket_id in ticket_ids:
            if store.get_ticket(session, ticket_id) is None:
                print(f"{ticket_id}: no such ticket")
                return 1
            store.reset_for_reprocess(session, ticket_id)
    for ticket_id in ticket_ids:
        print(f"{ticket_id}: {run_intake(ticket_id, engine)}")
    if not ticket_ids:
        print("nothing to reprocess")
    return 0


def list_tickets() -> int:
    from sqlmodel import Session

    from data.pipelines.rfp_intake import store

    with Session(_engine()) as session:
        for ticket in store.list_tickets(session, 200):
            print(f"{ticket.rfp_id}  {ticket.status:16s} {ticket.ticket_id}  {ticket.client_name or '-':24s} {','.join(ticket.departments_needed) or '-'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    one = sub.add_parser("smoke")
    one.add_argument("pdf", type=Path)
    again = sub.add_parser("reprocess")
    again.add_argument("ticket_ids", nargs="*")
    again.add_argument("--all-failed", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "smoke":
        return smoke(args.pdf)
    if args.command == "reprocess":
        if not args.ticket_ids and not args.all_failed:
            parser.error("give a ticket id or --all-failed")
        return reprocess(args.ticket_ids, args.all_failed)
    return list_tickets()


if __name__ == "__main__":
    raise SystemExit(main())
