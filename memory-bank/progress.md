# Current project state

## Milestone

Milestone 5: migrate the public site and backoffice, then implement operative authentication, the FastAPI inventory backend, and protected live inventory views.

## Known applications

- The root Next.js application currently implements a candidate pipeline and candidate detail flow. It is retained as existing work and is not the TrackFlow operations backoffice.
- `uis/website/` is a standalone Next.js 15, React 19, and TypeScript app on port 3000. `/` contains the migrated corporate homepage sections; `/application` retains the locally validated simulated onboarding form and bilingual toggle.
- `uis/talent-pipeline-tracker/` is a separate static candidate-tracking UI and remains in place.
- `uis/backoffice/` is a separate Next.js 15, React 19, and TypeScript app on port 3001. Its operations overview retains illustrative M2 analytics; its inventory section uses authenticated API data from `services/`.
- `services/` now contains a FastAPI auth and inventory API on port 8000. TinyDB stores operative credentials; Supabase/PostgreSQL stores SKUs and stock movements. Inventory tests use in-memory SQLite.
- The backoffice now includes `/backoffice/login` plus protected `/backoffice/inventory/products`, `/backoffice/inventory/orders/inbound`, `/backoffice/inventory/orders/outbound`, and `/backoffice/inventory/orders` routes. Inventory data is fetched through the centralized API module and requires the configured API URL and a valid bearer session.

## Open facts and constraints

- Root `CONTEXT.md` contains the assigned briefing and resolves the CEO attribution in favor of Thomas Harry based on repeated overview and executive-leadership statements.
- The root Next.js candidate demo, public website, and operations backoffice have separate run and verification workflows.
- Live Supabase integration still requires a private `DATABASE_URL` in `services/.env`; the backoffice requires `NEXT_PUBLIC_INVENTORY_API_URL` in `uis/backoffice/.env.local`. These values are intentionally not committed.

## Next steps

- Preserve the backoffice demo-data disclaimer until real integrations exist.
- Add backend services only after requirements and API contracts are defined.
## Project 7: Supplier Directory (branch `Supplier-Directory`)

- `services/supplier-directory/` implements the supplier API with seeder and tests; `uis/backoffice/suppliers.html` is the supplier page, linked from the backoffice menu.
- The supplier model and the 15 seed suppliers (in `supplier_directory/seed.py`) follow the Supplier Directory section of `CONTEXT.md`. `updated_at` tracks only the last rate update; currency must match country.

- Attach website/backoffice screenshots and link `AGENTS.md` in the PR description when preparing the change for review.
- Start the service with private Supabase configuration, register an operative, seed the development database, and verify all four protected views against the live API.
- Configure a private development Supabase `DATABASE_URL`, start the API, and repeat inventory checks against Supabase; the implemented API was verified with temporary SQLite.
- Commit and push the verified Milestone 5 changes to the fork after live Supabase validation.

## Project 13: Centralized Incident Manager (branch `Centralized-Incident-Manager`, built on Milestone 5)

- Integrated into the Milestone 5 platform: same FastAPI service, SQLModel database, login and Next.js backoffice. API, seed script, shared validation, tests (`services/tests`, `packages/shared/tests`) and UI are in place.
- The real `incidents-trackflow.csv` and the analyzer project's code were not available. CSV columns (`incident_id`, `description`, `date`, `status`, `category`, `country`) come from the briefing's mappings, and tests use a synthetic 95-valid-row CSV. Seeding totals against the real file are unverified.
- Verified locally against SQLite; not run against a live Supabase `DATABASE_URL`.
- Screenshots for the PR are in `docs/incident-manager/`.

## Incident File Analyzer (branch `Incident-File-Analyzer`)

- Script, shared logic, API endpoints and the backoffice page are in place. Verified against a synthetic file that reproduces the briefing's numbers exactly (100 records, 95 valid, 5 invalid with one per rule, categories 14/38/19/17/7, statuses 29/52/14, countries 50/45, 52 scored, average 3.06).
- NOT verified: the real `incidents-trackflow.csv`. It contains customer emails and was deliberately not read. Run `python scripts/analyze.py data/raw/incidents-trackflow.csv` and compare with the briefing.
- The briefing mentions a 1,000-row test file in one place and 100 rows in the data section; the expected values are for 100 rows.
