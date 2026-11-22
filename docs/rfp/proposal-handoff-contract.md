# Handoff contract: drafting (part 2) → approval (part 3)

Part 3 starts from the database. **Where:** `rfp_tickets.part3_handoff` (JSON), flagged by `rfp_tickets.proposal_state = 'ready_for_part_3'`. The same data is in the section rows
(`rfp_department_sections.draft_content`, `evaluation_results`, `draft_status`, `iterations`), written in the same transaction family.

```sql
SELECT ticket_id, part3_handoff FROM rfp_tickets WHERE proposal_state = 'ready_for_part_3' AND status IN ('under_evaluation', 'needs_human_review');
```

Every active department is in it, **including the sections that exhausted their attempts**. Nothing is dropped.

```json
{
  "schema_version": 1,
  "ticket_id": "9f0c…", "rfp_id": "TF-RFP-2026-0001",
  "status": "needs_human_review",
  "needs_human_review": ["reverse"],
  "client": {"name": "ModaViva, S.L.", "country": "ES", "currency": "EUR", "contact": {}},
  "arbiter": "Miguel Torres",
  "guidelines": ["…the five rules of CONTEXT section 5…"],
  "max_iterations": 3,
  "sections": [
    {
      "section_id": "9f0c…:warehouse", "department_id": "warehouse", "department": "Warehouse Operations", "owner": "Ana Whitfield",
      "content": "## Warehousing proposal: ModaViva, S.L. …",
      "section_status": "passed",
      "iterations": 1,
      "evaluation_result": {"readability": {…}, "relevance": {…}, "compliance": {…}, "overall_pass": true, "feedback_for_generator": "The section passed all checks."},
      "approval_status": "pending"
    }
  ]
}
```

## Rules of the contract

- `sections` has one entry per department in `departments_needed` of the Part 1 handoff, in the order `warehouse`, `lastmile`, `reverse`. Part 3 asks **that department's owner** to approve it.
- `content` is the **last** draft; `evaluation_result` is the evaluation **of that same draft** (so for `needs_human_review` it is a failing result and its `feedback_for_generator` says what is still wrong).
- `section_status` is `passed` or `needs_human_review`. A ticket with any `needs_human_review` section has `status = needs_human_review`; when all sections pass, `status` is `under_evaluation`
  with `proposal_state = ready_for_part_3` (Part 3 statuses are Part 3's to set).
- `approval_status` is `pending` for every section; approvers and timestamps are Part 3's (`approver`, `approved_at` columns).
- `evaluation_result.compliance.rule_ids` (GL-1…GL-5) and `client.currency` give Part 3's arbitration the structured state it needs for `currency-mismatch` and `returns-sla-breach`.
- Adding fields does not change `schema_version`.
