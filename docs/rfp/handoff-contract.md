# Handoff contract: intake (part 1) → drafting (part 2)

Part 2 starts from the database, not from the PDF and not from an HTTP call between services.

**Where.** Table `rfp_tickets`, column `handoff` (JSON), flagged by `handoff_state`.

**When it is ready.** `handoff_state = 'ready_for_part_2'` together with `status = 'intake_complete'`. Both are written in the same transaction as the
`rfp_department_sections` rows, so a ticket flagged ready always has its sections. A ticket that is `discarded` or `failed` has `handoff_state = 'not_ready'` and an empty `handoff`.

**How Part 2 picks work up.**

```sql
SELECT ticket_id, handoff FROM rfp_tickets WHERE handoff_state = 'ready_for_part_2' AND status = 'intake_complete';
```

Part 2 then moves the ticket to `drafting` (its own status) and fills `draft_content` / `evaluation_results` of the section rows it already has
(`rfp_department_sections`: `department_id`, `key_aspects`, `draft_content`, `evaluation_results`, `approval_status`, `approver`, `approved_at`).
The converted document stays in `rfp_tickets.markdown` if a department needs a slice the intake did not extract.

## The payload (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "ticket_id": "9f0c…",
  "rfp_id": "TF-RFP-2026-0001",
  "status_when_handed_off": "intake_complete",
  "next_stage": "drafting",
  "created_at": "2026-11-22T10:15:00+00:00",
  "client": { "name": "ModaViva, S.L.", "country": "ES", "currency": "EUR", "contact": { "name": "Elena Bosch", "title": "Directora de Operaciones", "email": "logistica@modaviva.es" } },
  "services_requested": ["warehousing", "reverse_logistics"],
  "departments_needed": ["warehouse", "reverse"],
  "monthly_volume": null,
  "deadline": "2026-08-28",
  "budget_range": null,
  "cost_band": "low",
  "arbiter": "Miguel Torres",
  "guidelines": ["Prices are quoted in USD for US operations and EUR for Spain operations …", "Every proposal must state the on-time delivery SLA (%) …", "…"],
  "summary": "ModaViva, S.L. (Spain, quoted in EUR) asks for warehousing and returns …",
  "open_questions": ["The monthly order volume is not stated. …"],
  "workstreams": [
    {
      "department_id": "warehouse",
      "owner": "Ana Whitfield",
      "key_aspects": {
        "summary": "…", "requirements": ["…"], "stated_figures": { "skus": 15000, "preferred_location": "Zaragoza" },
        "asks_of_owner": ["…"], "open_questions": ["…"], "constraints": ["Prices in EUR."], "contributes": "Storage capacity, cost per pallet/SKU, onboarding time"
      }
    }
  ]
}
```

## Rules of the contract

- `departments_needed` and `workstreams` have the same departments, in the order `warehouse`, `lastmile`, `reverse`. Part 2 generates a section **only** for those.
- `monthly_volume`, `deadline` and `budget_range` are `null` when the RFP does not state them. Part 2 must not fill them in; the matching `open_questions` say what to ask.
- `client.currency` is derived from `client.country` (US → USD, ES → EUR) and is the currency of every proposal section.
- `guidelines` are the business constraints of the CONTEXT (section 5) the evaluators of Part 2 check; `arbiter` is the fixed arbiter of Part 3.
- A change of `schema_version` means a field changed meaning or was removed; adding fields does not change it.
