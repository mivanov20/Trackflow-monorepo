"""The final document: the approved sections consolidated (CONTEXT 2.3 `FinalDocument`: ticket_id, sections, currency, generated_at, plus the Markdown the client-facing document is made of).

Generated only when EVERY active department has approved (the coordinator checks it and the arbitration found no open conflict). It contains what the owners approved, in the order
warehouse, last mile, returns, with who approved each section and when, and the decisions of the arbiter that shaped it (a capped volume, for example). Items still "to be confirmed" in an approved section
are carried as they are: the owners approve the text, and an owner who wants a price in the document edits the section before approving.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import DEPARTMENT_IDS, DEPARTMENTS


def build(ticket_id: str, rfp_id: str, handoff: dict, sections: list[dict], arbitration_log: list[dict]) -> dict:
    """`{rfp_id, currency, sections, content_markdown}` from the approved sections (dicts of `approval_store.sections_snapshot`)."""
    ordered = sorted(sections, key=lambda section: DEPARTMENT_IDS.index(section["department_id"]))
    client = handoff.get("client", {})
    generated_at = datetime.now(timezone.utc)
    summary = (handoff.get("summary") or "").split("\n\n")[0]
    lines = [
        f"# Logistics proposal for {client.get('name') or 'the client'}",
        "",
        f"Reference {rfp_id} · Currency {client.get('currency')} · Generated {generated_at.strftime('%Y-%m-%d')}",
        "",
        "## Summary",
        summary,
        "",
    ]
    for section in ordered:
        lines += [section["content"].strip(), ""]
    lines += ["## Approvals", "| Department | Approved by | Date |", "|---|---|---|"]
    for section in ordered:
        lines.append(f"| {DEPARTMENTS[section['department_id']]['name']} | {section['approver']} | {(section['approved_at'] or '')[:10]} |")
    if arbitration_log:
        lines += ["", "## Decisions of the arbiter"]
        lines += [f"- {entry['trigger_id']} ({entry['arbiter']}): {entry['evidence']}" for entry in arbitration_log]
    return {
        "rfp_id": rfp_id,
        "currency": client.get("currency"),
        "sections": [
            {"department_id": s["department_id"], "department": DEPARTMENTS[s["department_id"]]["name"], "owner": DEPARTMENTS[s["department_id"]]["owner"], "content": s["content"], "approver": s["approver"], "approved_at": s["approved_at"]}
            for s in ordered
        ],
        "content_markdown": "\n".join(lines).strip() + "\n",
    }
