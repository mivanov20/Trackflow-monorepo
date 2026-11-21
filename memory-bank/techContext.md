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

## Authentication (AUTH-01)

- Stateless JWT only (no sessions or cookies). `services/security.py`: bcrypt hashing (`libpass`, imported as `passlib.hash.bcrypt`; max 72 bytes) and `python-jose` signing. Config from env: `JWT_SECRET` (>= 32 chars, required), `JWT_ALGORITHM` (HS256/384/512, default HS256), `ACCESS_TOKEN_EXPIRE_MINUTES` (default 480), `BCRYPT_ROUNDS` (default 12; tests use 4).
- `User` (id, email, hashed_password, is_active, role admin|manager|user, created_at) and `Profile` (id, user_id, name, phone, address) live only in TinyDB (`AUTH_DB_PATH`, tables `users` and `profiles`) through the service layer in `services/accounts.py`. `User.id` is a uuid string; it is the JWT `sub` and what inventory stores as `user_uid`. Legacy accounts (old default-table format with `user_uuid`) are migrated on first read and re-hashed to bcrypt on first login.
- Routes: `/auth` (`POST /login` JSON, `POST /token` form for Swagger, deprecated `POST /register` alias), `/users` (`POST` public; list admin-only; get/put/delete self-or-admin, 403 otherwise; only admins change `role`/`is_active`; the last active admin cannot be removed), `/profiles/me` (GET/PUT). `get_current_user` in `services/auth.py` protects `/users`, `/profiles`, `/inventory/*` and `/api/incidents/*`; only `/health`, `/auth/*` and `POST /users` are public.
- Inventory columns were renamed `user_uuid` -> `user_uid`. Existing databases need `python scripts/migrate_user_uid.py` once (the API refuses to start otherwise). The first admin is created with `python scripts/promote_user.py <email> admin`.
