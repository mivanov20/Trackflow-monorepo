# Technical context

## Repository shape

This is a company-project monorepo. User interfaces belong in `uis/`, backend APIs/workers in `services/`, data pipelines and source data in `data/`, product agents in `agents/`, reusable agent capabilities in `skills/`, and shared libraries in `packages/` or `shared/`. Read the relevant folder `README.md` before adding a project. `.agents/` is development-agent configuration and is not the same as product code in `agents/` or `skills/`.

## Applications

- Root application: Next.js 15, React 19, and TypeScript, with App Router code in `app/`, reusable UI in `components/`, candidate API helpers in `lib/`, and candidate types in `types/`. The current root application is a candidate-pipeline demo; do not treat it as TrackFlow domain truth.
- `uis/website/`: the existing TrackFlow public site, implemented as static HTML and vanilla JavaScript with Tailwind loaded from its CDN. Its entry points are `index.html` and `application.html`; `components.js` renders shared data-driven homepage cards, while `index.js`, `language.js`, and `validation.js` handle page interactions, localization, and form validation.
- `uis/backoffice/`: TrackFlow internal operations UI, implemented as static HTML/CSS/vanilla JavaScript. Its entry view `index.html` uses sample-only metrics with no API connection; `suppliers.html` is the supplier directory and talks to `services/supplier-directory`.
- `services/supplier-directory/`: FastAPI + TinyDB + Pydantic supplier API (Python 3.12, managed with uv). Run from that folder: `uv run seed`, `uv run serve` (port 8000), `uv run pytest`. Supplier fields (`rate_per_shipment`, `currency` tied to country `USA`/`Spain`), the eight snake_case categories, statuses `active`/`suspended` and the 15 seed suppliers come from the Supplier Directory section of `CONTEXT.md` and are documented in its README.

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
- Inventory columns were renamed `user_uuid` -> `user_uid`. Existing databases need `scripts/migrate_user_uid.py` once (the API refuses to start otherwise). Run it from the repo root; it reads `DATABASE_URL` from the environment or `services/.env`, and `--dry-run` shows what it would change without touching anything. The first admin is created with `python scripts/promote_user.py <email> admin`.

## Error handling (error-handling audit)

- API: `services/main.py` maps `SQLAlchemyError` to a generic 503 and any unhandled exception to a generic 500 (`{"detail": ...}`), via a middleware registered inside CORS so browsers can read it. Commits go through `services.database.commit_session` (rollback, then 409/503). TinyDB access in `services/auth.py` goes through `_users_db()` (I/O or corrupt JSON -> 503). Real errors are only logged server-side.
- UIs: every fetch has loading / error / ready states with a retry action; `uis/backoffice/lib/api-client.ts` and `lib/candidates.ts` turn status codes, non-JSON bodies and validation internals into plain language. Each Next.js app has an `app/error.tsx` (retry + home link).
- Scripts: `scripts/seed_incidents.py` and `services/seed_data.py` print sanitized messages to stderr and exit 1 on critical failures (database error messages are reduced to the exception type).

## Frontend authentication (AUTH-02)

- Both authenticated Next.js apps use the same client-side flow; `uis/website` (public site) has no auth code and is untouched. The token lives in `localStorage` under `trackflow-access-token` (user summary under `trackflow-auth-user`). It is sent as `Authorization: Bearer` only to the TrackFlow API (`NEXT_PUBLIC_INVENTORY_API_URL`, default `http://localhost:8000`), never to the external 4Geeks candidate API. No Next.js middleware is used.
- Pages in each app: `/login`, `/register` (POST /users, then POST /auth/login, then redirect), `/account/profile` (GET /auth/me, PUT /profiles/me). Logout clears the token and goes to `/login`.
- Guard: `AuthGuard` (backoffice `app/components/AuthGuard.tsx`, root `components/AuthGuard.tsx`) redirects to `/login?next=...` when there is no token, validates it with `GET /auth/me` once on entry, and shows a retry screen (without logging out) if the API is unreachable. A 401 from any protected call clears the session and redirects to `/login?reason=expired`. `safeNextPath` only accepts same-site paths after login.
- Backoffice protected views: `/`, `/backoffice/inventory/*`, `/backoffice/incidents/*`, `/account/profile`. Root app protected views (route group `app/(app)`): `/`, `/candidates/[id]`, `/ai-driven-engineering`, `/account/profile`. Public: `/login`, `/register` in both apps and the whole website. The old `/backoffice/login` redirects to `/login`.
- API additions: `GET /auth/me`; default CORS origins include `http://localhost:3000` (root app) and `http://localhost:3001` (backoffice).
- The two apps keep separate copies of `lib/session.ts` and the auth client (an app cannot share React code across folders without duplicating React); localStorage is per origin, so each app has its own session.

## Password reset and change (AUTH-03)

- API (`services/auth.py`): `POST /auth/forgot-password` (always 200 and the same message; email sent in a background task; per-account cooldown), `POST /auth/reset-password` (400 for invalid, expired or used links), `POST /auth/change-password` (authenticated; 400 for a wrong current password; returns a fresh token). Reset token = JWT with `purpose=password_reset` and a `jti`; the server keeps a record per link (`password_resets` table in the TinyDB file) with only `sha256(jti)`, `expires_at` and `used_at`. `complete_password_reset` (in `services/accounts.py`) spends the link and changes the password atomically, cancels other pending links and sets `password_changed_at`; `get_current_user` rejects session tokens whose `iat` is older than that. A token with a `purpose` claim is never accepted as a session.
- Email: `services/mailer.py` calls the Resend HTTP API with `httpx` (no extra dependency). Settings only through env vars (`EMAIL_PROVIDER`, `RESEND_API_KEY`, `EMAIL_FROM`, `FRONTEND_BASE_URL`, `PASSWORD_RESET_EXPIRE_MINUTES`, `PASSWORD_RESET_COOLDOWN_SECONDS`); documented in `services/.env.example` and `services/README.md`. `console` mode logs the link for local development. Delivery failures are logged (never the key, token or address) and never change the API response.
- Frontend (both authenticated apps): public `/forgot-password` and `/reset-password` (reads `?token=`, on success redirects to `/login?reset=success`, on a bad link shows an error with a link to `/forgot-password`), protected `/account/change-password` (checks that the confirmation matches; the API's new token replaces the stored one). The login pages link to `/forgot-password`; the profile pages link to change password. `/forgot-password` and `/reset-password` are listed as auth pages in `lib/session.ts`, so a 401 there never redirects.

## Incident file analyzer

- Logic: `packages/shared/trackflow_shared/incident_analysis.py` (standard library only, streams the CSV, ten invalid-record rules, metrics, `to_dict()` for the API and `to_csv_rows()` for the export) and `incident_report.py` (console layout). One implementation, used by `scripts/analyze.py` and by `services/routers/incident_analysis.py`. Field names, allowed values (categories, statuses, countries, carriers per country) and rules come from the TrackFlow briefing; the category/status/country codes are the same ones `incidents.py` uses for seeding.
- Script: `python scripts/analyze.py incidents-trackflow.csv` (prints the report, asks `Export results to CSV? [y / n]`, writes `results.csv`, `--export` skips the question). API: `POST /api/incidents/analyze` (multipart field `file`), `GET /api/incidents/results/export` (last analysis of the caller, kept in memory, aggregates only). `ANALYSIS_MAX_UPLOAD_MB` (default 250). Uses `python-multipart`.
- UI: `/backoffice/incidents/analyze` (drag and drop, summary, invalid records explained, download).
- Privacy: the real file has customer emails and is never read by tools or committed. Nothing but counts and line numbers leaves the analysis (tests assert this for results, CSV, errors and logs). `data/eval/incidents-synthetic.csv` is the synthetic stand-in with the briefing's distribution.
