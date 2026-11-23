# Incident response runbook (AI systems and customer data)

For the Compliance Lead, the CTO and whoever is on call. It exists because TrackFlow's two regulators run different clocks: **GDPR, 72 hours to the Agencia Española de Protección de Datos (AEPD)**, and **CCPA/CPRA, notification to California residents in the most expedient time possible and without unreasonable delay**. Contact details of the people filling the roles below belong in the company's internal directory, not in this repository.

## Roles

| Role | Who | Does |
|---|---|---|
| Incident lead | Andrés Kim (CTO) | Decides severity, owns containment and the technical timeline |
| Compliance lead | Compliance Lead | Decides whether it is a notifiable breach, files the AEPD notification, drafts the communication to affected people |
| Customer-facing lead | Valentina Cruz (Customer Experience) | Handles customer and brand contact; gives the agents one approved script |
| Business owner of the affected component | See the inventory in `nist-report.md` §3 | Confirms what the component should have done, approves restoring it |
| Executive sponsor | Thomas Harry (CEO) | Informed at severity 1 and 2; approves public statements |

## What counts as an incident

| Severity | Examples | Start |
|---|---|---|
| **1** | A customer saw another customer's name, contact details or address; a brand's contract or volume data reached another brand; a credential or key was exposed; an AI action with money impact was taken without the required human confirmation | Immediately; the 72-hour clock may already be running |
| **2** | Successful prompt injection that changed the agent's behaviour without exposing data; a `security_alert` from one account; an unexplained spike of `rate_limit` events | Within 4 hours |
| **3** | A blocked attack, a dependency advisory, a failed sign-in burst | Next business day |

## The first hour

1. **Open the record**: who noticed, when, what. Write the time of awareness; **this is the start of the 72-hour clock** (the time TrackFlow has a reasonable degree of certainty that personal data was exposed).
2. **Contain**, in this order and without waiting for the full picture:
   - an exposed key: revoke and re-issue it at the provider (LLM, e-mail, carrier, database), then replace it in `/etc/trackflow/trackflow.env` and redeploy; JWT secret change signs everyone out;
   - an abusing account: disable it (`PUT /users/{id}` with `is_active: false`); a password change already invalidates its tokens;
   - the agent itself: set `RATE_LIMIT_PER_MINUTE=1`, or stop the `api` service chat endpoints at the proxy, or remove the agent's scopes at the identity provider (`AGENT_MCP_SCOPES` empty); the agent has no write scope, so containment never needs a data change;
   - a bad document in the knowledge base: remove it from the source and re-run `python scripts/index_knowledge_base.py`.
3. **Preserve evidence** before cleaning: export `guardrail_events` for the actor and time window, the agent trace of every affected `run_id` (`GET /agent/runs/{id}/trace`), the MCP audit lines, API logs (`trackflow.security`, `trackflow.agent.guardrails`), and the chat messages of the affected sessions.
4. **Scope**: from the trace and the logs, list which orders, customers and brands were exposed and to whom.

## The legal clock

| Hour | Action | Who |
|---|---|---|
| 0 | Time of awareness recorded; severity set | Incident lead |
| ≤ 24 | Scope known well enough to decide risk to individuals; compliance lead decides *notifiable or not* and records the reasoning even if the answer is no | Compliance lead |
| ≤ 72 | If personal data of people in Spain/EU is affected and there is a risk to their rights: **notify the AEPD** (what happened, categories and approximate number of people and records, likely consequences, measures taken and proposed). Missing facts may be sent in phases, but the first notification must not wait for them | Compliance lead |
| Without undue delay | If the risk is **high**: tell the affected people in plain language (what happened, what it means for them, what to do, who to contact) | Compliance lead, customer-facing lead |
| Most expedient time possible | California residents whose personal information was exposed: notify them (CCPA/CPRA; Cal. Civ. Code §1798.82); the Los Angeles operation prepares the California letter | Compliance lead |
| Contract terms | A brand's data is often confidential by contract: tell the brand's account manager and follow its contract's notification term, which can be shorter than 72 hours | Customer-facing lead, Miguel Torres |

## Scenario: a customer obtained another customer's address through the chat

1. Severity 1. Record the time of awareness.
2. Contain: set `RATE_LIMIT_PER_MINUTE=1`; if the cause is not understood, take the chat offline at the proxy.
3. Evidence: the `run_id` of the exchange; its trace shows which nodes ran, which sources were read and whether a guard fired; `guardrail_events` for the actor; the shipments registry rows the account owns (the access check compares the order to the signed-in account).
4. Cause: was the order checked against the account (`session_authorization`)? Did the agent read a ticket or stock tool for a customer account (`tools_allowed`)? Did the answer pass the output guard? Reproduce it in a test **before** fixing it, add it to `services/tests/test_nist_controls.py`, fix, deploy.
5. Notify as in the table above.
6. Close with a post-incident review within 5 business days: what failed, which control would have caught it, the new test, the roadmap item.

## Recover

See `nist-report.md` §7 for each store. After any incident: rotate the secrets that were or may have been exposed (provider keys first, then `JWT_SECRET`, then the MCP service account), check `verify-server.sh` still passes, re-run `python scripts/security/secret_scan.py --all` and the test suite, and record the restoration time against the proposed RTO.

## Rehearsal

Run the scenario above as a tabletop exercise every quarter with the incident lead, the compliance lead and the customer-facing lead; time how long it takes to reach each row of the legal clock. The first rehearsal is roadmap item G-06 and has not been done.
