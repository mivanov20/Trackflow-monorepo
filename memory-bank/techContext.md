# Technical context

## Repository shape

This is a company-project monorepo. User interfaces belong in `uis/`, backend APIs/workers in `services/`, data pipelines and source data in `data/`, product agents in `agents/`, reusable agent capabilities in `skills/`, and shared libraries in `packages/` or `shared/`. Read the relevant folder `README.md` before adding a project. `.agents/` is development-agent configuration and is not the same as product code in `agents/` or `skills/`.

## Applications

- Root application: Next.js 15, React 19, and TypeScript, with App Router code in `app/`, reusable UI in `components/`, candidate API helpers in `lib/`, and candidate types in `types/`. The current root application is a candidate-pipeline demo; do not treat it as TrackFlow domain truth.
- `uis/website/`: standalone Next.js 15, React 19, and TypeScript app. App Router pages are `/` (corporate homepage) and `/application` (locally validated simulated onboarding form). Reusable typed site sections are in `app/components.tsx`; its own `package.json` runs the development server on port 3000.
- `uis/backoffice/`: standalone Next.js 15, React 19, and TypeScript app on port 3001. The existing overview still displays illustrative shipment/M2 analytics. Protected inventory routes under `app/backoffice/inventory/` use `lib/inventory.ts` and `lib/api-client.ts` to access the FastAPI service; login is at `/backoffice/login`, with JWT credentials in browser localStorage.
- `services/`: FastAPI service on port 8000. `auth.py` registers/logs in operative accounts using TinyDB, password hashes, and expiring JWTs. `models.py`, `schemas.py`, and `routers/inventory.py` implement `SKU`, `StockEntry`, and `StockExit` against the configured Supabase PostgreSQL URL; current stock is calculated per warehouse. Private local configuration belongs in ignored `services/.env`; tests use in-memory SQLite and temporary TinyDB.
| Run public website | `npm --prefix uis/website run dev` (port 3000) |
| Typecheck public website | `npm --prefix uis/website run typecheck` |
| Build public website | `npm --prefix uis/website run build` |
| Run backoffice | `npm --prefix uis/backoffice run dev` (port 3001) |
| Typecheck backoffice | `npm --prefix uis/backoffice run typecheck` |
| Build backoffice | `npm --prefix uis/backoffice run build` |
| Run FastAPI | `services/.venv/bin/uvicorn services.main:app --reload --port 8000` |
| Test FastAPI | `services/.venv/bin/python -m pytest services/tests -q` |
- `uis/backoffice/`: TrackFlow internal operations UI, implemented as static HTML/CSS/vanilla JavaScript with sample-only metrics. Its entry view is `index.html`; it has no live API connection.
- `uis/talent-pipeline-tracker/`: a separate static candidate-tracker project that talks to the 4Geeks tracker API. It is not a TrackFlow operations application.

## Commands

Run from the repository root unless stated otherwise:

| Purpose | Command |
| --- | --- |
| Install locked dependencies | `npm ci` |
| Run root Next.js app | `npm run dev` |
| Typecheck root app | `npm run typecheck` |
| Build root app | `npm run build` |
| Serve public website | `python3 -m http.server 8080 --directory uis/website` |
| Serve backoffice | `python3 -m http.server 8081 --directory uis/backoffice` |

There is no root test script or app-specific test harness currently. Validate the independent Next.js apps with their typecheck/build scripts and runtime route checks; report checks that do not exist rather than claiming they passed.

## Conventions and constraints

- Follow the owning app's existing stack; keep the public website and internal backoffice independently runnable in their canonical folders.
- Keep public and internal interfaces in their canonical `uis/website` and `uis/backoffice` directories.
- Backend work belongs under `services/`; do not add an API merely to support a static entry view.
- Preserve unrelated milestone work and inspect `git status` before staging or committing.
- Treat root `CONTEXT.md` as the authoritative company briefing. It identifies Thomas Harry as CEO and Daniel Espinoza as the creator of TrackFlow Tech.
- Update this memory when architecture, app entry points, commands, or verified domain decisions change.

## Incident manager (project 13)

- API: `services/routers/incidents.py` (`/api/incidents`, bearer JWT required) with `Incident` and `IncidentSeedLedger` in `services/models.py` and `IncidentRead` in `services/schemas.py`. Structured JSON errors (400/404/generic 500) are scoped to this router through `IncidentRoute`; inventory keeps FastAPI defaults.
- Shared rules and validation: `packages/shared/trackflow_shared/incidents.py` (Python package; installed by `services/requirements.txt` via `-e ./packages/shared`; tests: `cd packages/shared && python -m pytest`). Used by the API and `scripts/seed_incidents.py`.
- UI: `/backoffice/incidents` (list, filters, summary) and `/backoffice/incidents/new` (form) in `uis/backoffice`, using `lib/incidents.ts`.
- Seed: `services/.venv/bin/python scripts/seed_incidents.py <csv>` from the repo root with `DATABASE_URL` set. The real `incidents-trackflow.csv` (personal data) is git-ignored at `data/raw/incidents-trackflow.csv`.
- Windows note: the virtualenv interpreter is `services/.venv/Scripts/python.exe`.

## Error handling (error-handling audit)

- API: `services/main.py` maps `SQLAlchemyError` to a generic 503 and any unhandled exception to a generic 500 (`{"detail": ...}`), via a middleware registered inside CORS so browsers can read it. Commits go through `services.database.commit_session` (rollback, then 409/503). TinyDB access in `services/auth.py` goes through `_users_db()` (I/O or corrupt JSON -> 503). Real errors are only logged server-side.
- UIs: every fetch has loading / error / ready states with a retry action; `uis/backoffice/lib/api-client.ts` and `lib/candidates.ts` turn status codes, non-JSON bodies and validation internals into plain language. Each Next.js app has an `app/error.tsx` (retry + home link).
- Scripts: `scripts/seed_incidents.py` and `services/seed_data.py` print sanitized messages to stderr and exit 1 on critical failures (database error messages are reduced to the exception type).
