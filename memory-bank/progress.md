# Current project state

## Milestone

Milestone 4: establish AI-agent context and delivery conventions, keep the public website under `uis/website`, and provide a visible internal entry view under `uis/backoffice`.

## Known applications

- The root Next.js application currently implements a candidate pipeline and candidate detail flow. It is retained as existing work and is not the TrackFlow operations backoffice.
- `uis/website/` contains the TrackFlow public site and application form. The assigned briefing is now available in `CONTEXT.md`; reconcile neutral copy with confirmed company facts when relevant.
- `uis/talent-pipeline-tracker/` is a separate static candidate-tracking UI and remains in place.
- `uis/backoffice/` contains an independently runnable operations overview with clearly labeled illustrative sample data and no backend connection.

## Open facts and constraints

- Root `CONTEXT.md` contains the assigned briefing and resolves the CEO attribution in favor of Thomas Harry based on repeated overview and executive-leadership statements.
- The root Next.js app and the static UI projects have separate run and verification workflows.
- No TrackFlow backend service has been created by this milestone; service work belongs under `services/` when requirements and API contracts exist.

## Next steps

- Reconcile public website copy with the authoritative company briefing as it evolves.
- Preserve the backoffice demo-data disclaimer until real integrations exist.
- Add backend services only after requirements and API contracts are defined.
## Project 7: Supplier Directory (branch `Supplier-Directory`)

- `services/supplier-directory/` implements the supplier API with seeder and tests; `uis/backoffice/suppliers.html` is the supplier page, linked from the backoffice menu.
- The supplier model and the 15 seed suppliers (in `supplier_directory/seed.py`) follow the project brief supplied by the course; they are not in the general `CONTEXT.md`. `updated_at` tracks only the last rate update; currency must match country.
