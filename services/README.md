# `services` folder

This folder contains **all the backend services** (APIs and background workers) related to the company for the cross-functional AI Engineering project.

Each subfolder inside `services/` must correspond to **one specific service** (for example: `admin-api`, `data-processor-worker`) and include its own technical and functional documentation.

- **Main purpose**: to centralize all the backend logic, APIs, and queue consumers that support the company's use cases.
- **Recommendation**: document in this file (or in sub-READMEs) the services you add, their objective, the technology used, and how to run them.

> _Spanish version: [README.es.md](./README.es.md)._

## TrackFlow inventory API

The FastAPI service implements TinyDB-backed operative registration/login and the Supabase/PostgreSQL-backed TrackFlow inventory API. Inventory routes are protected with bearer JWTs issued by `/auth/login`; `/docs` exposes the generated OpenAPI reference.

### Local setup

From the repository root, create `services/.env` with your own private values (never commit it):

```dotenv
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/postgres
JWT_SECRET=<random-secret-at-least-32-characters>
AUTH_DB_PATH=./services/data/users.json
CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000,http://127.0.0.1:3000
```

See `services/.env.example` for the full list, including the email settings.

Install and run the API on port 8000, loading the private values into the process:

```bash
python3 -m venv services/.venv
services/.venv/bin/pip install -r services/requirements.txt
set -a && source services/.env && set +a && services/.venv/bin/uvicorn services.main:app --reload --port 8000
```

Register an operative at `/auth/register`, sign in at `/auth/login`, then run `services/.venv/bin/python -m services.seed_data` to add the syllabus development SKUs and movements to the configured database. Seed data is explicit, idempotent, and attributes movements to an already registered TinyDB user. Never use seed credentials or development data as a production access policy.

### Password recovery and email (Resend)

`POST /auth/forgot-password`, `POST /auth/reset-password` and `POST /auth/change-password` cover the two password flows. The reset email is sent with [Resend](https://resend.com); `services/.env.example` lists every variable.

| Variable | Purpose |
| --- | --- |
| `EMAIL_PROVIDER` | `resend` sends real emails, `console` (development only) prints the reset link in the API log, empty or `disabled` sends nothing. |
| `RESEND_API_KEY` | Your Resend API key. Required when `EMAIL_PROVIDER=resend`; the API refuses to start without it. Never commit it. |
| `EMAIL_FROM` | Sender. Defaults to Resend's test sender `TrackFlow <onboarding@resend.dev>`, which only delivers to the email of your own Resend account. |
| `FRONTEND_BASE_URL` | App that serves `/reset-password` (default `http://localhost:3001`, the backoffice; use `http://localhost:3000` for the candidates app). |
| `PASSWORD_RESET_EXPIRE_MINUTES` | How long a reset link lasts (default 30, between 1 and 1440). |
| `PASSWORD_RESET_COOLDOWN_SECONDS` | Minimum time between two emails for the same account (default 60, `0` disables it). |

How it works: the link carries a signed JWT valid for one purpose only. The server also stores a record with a hash of the token id, marks it used when the password changes, and cancels older links, so a link cannot be reused. `forgot-password` always answers `200` with the same message and sends the email in the background, so it never reveals whether an address is registered. Resetting or changing a password signs out sessions issued earlier. To try the flow without sending email, set `EMAIL_PROVIDER=console` and copy the link from the API log.

### Inventory rules

- The API follows `content/contexts/05-backend-development/CONTEXT-trackflow.en.md`: `SKU`, `StockEntry`, and `StockExit`, with `/inventory` routes.
- `current_stock` is calculated per SKU and warehouse from entries minus exits; it is not stored.
- Outbound stock cannot go below zero. API responses use the required HTTP 400 error detail.
- Dispatch exits require a tracking number; loss exits require `tracking_number: null`.
- TinyDB stores operative credentials separately from SQLModel inventory tables. There is no SQL user table.

### Backend checks

```bash
services/.venv/bin/python -m pytest services/tests -q
```

Tests use an in-memory SQLite database and temporary TinyDB file. For live integration, configure a development Supabase `DATABASE_URL` and the backoffice API URL; no real credentials belong in source control.

## Incident manager (`/api/incidents`)

Centralized registry of operational incidents for TrackFlow's two markets. It lives in the same FastAPI service, database and login as the inventory API: `models.py` (`Incident`, `IncidentSeedLedger`), `schemas.py` (`IncidentRead`) and `routers/incidents.py`. Every incident route requires the same bearer JWT as the inventory routes. The backoffice pages are `/backoffice/incidents` (list and summary) and `/backoffice/incidents/new` (report form).

### Data model

| Field | Rules |
| --- | --- |
| `id` | Generated automatically |
| `title`, `description` | Required (title up to 120 characters) |
| `category` | `lost_parcel`, `delivery_failure`, `inventory_discrepancy`, `carrier_issue`, `returns_issue`, `warehouse_incident`, `system_failure`, `client_complaint`, `other` |
| `status` | `open`, `in_progress`, `resolved`, `discarded` (defaults to `open`) |
| `origin` | `customer`, `branch`, `internal` |
| `branch` | Required for every origin: `central`, `la_warehouse`, `la_office`, `zaragoza_warehouse`, `zaragoza_office` |
| `created_at`, `updated_at` | Generated automatically (UTC); `updated_at` changes on every status update |

Allowed values are enforced twice: by the shared validator (clear `400` errors) and by `CHECK` constraints in the database. `category`, `status`, `origin` and `branch` are indexed, so filtering SLA-sensitive categories (`lost_parcel`, `carrier_issue`) is a plain indexed query.

**Lifecycle:** `open` → `in_progress` or `discarded`; `in_progress` → `resolved` or `discarded`; `resolved` and `discarded` are final.

### Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/incidents` | `201` with the incident; `400` naming each invalid field |
| `GET` | `/api/incidents` | Optional filters `status`, `origin`, `branch`, `category` (invalid value → `400`); empty table → `[]` |
| `GET` | `/api/incidents/summary` | `total`, `by_status`, `by_category`, `by_origin`, `by_branch`; every key present, zeros when empty |
| `GET` | `/api/incidents/{id}` | `404` if missing |
| `PATCH` | `/api/incidents/{id}/status` | Body `{"status": "in_progress"}`; invalid value or lifecycle transition → `400` |

### Errors

Incident endpoints always answer in JSON, never with a stack trace:

```json
{"error": "validation_error", "message": "Some fields need your attention.",
 "errors": [{"field": "branch", "message": "Branch is required."}]}
```

`404` uses `"error": "not_found"`. Any unhandled exception returns `500` with `"error": "internal_error"` and a generic message; the real error is only written to the server log. These formats are scoped to the incident router (`IncidentRoute`), so the inventory endpoints keep FastAPI's default errors.

### Shared validation

The enums, lifecycle rules, payload validation and the analyzer-CSV validation and transformation live in `packages/shared/trackflow_shared/incidents.py` (a Python package next to the TypeScript types). The API and `scripts/seed_incidents.py` both import it. `services/requirements.txt` installs it with `-e ./packages/shared`. Its own tests: `cd packages/shared && python -m pytest`.

### Incident file analysis

`POST /api/incidents/analyze` and `GET /api/incidents/results/export` analyse the after-sales incident CSV with the same code as `scripts/analyze.py` (`trackflow_shared/incident_analysis.py`); the backoffice page is `/backoffice/incidents/analyze`. Both routes require the bearer JWT.

| Endpoint | Behaviour |
| --- | --- |
| `POST /api/incidents/analyze` | Body `multipart/form-data` with the CSV in the field `file`. Returns the summary as JSON: totals, the count per invalid rule with the line numbers of invalid records, category / status / country breakdowns (with percentages over valid records) and the satisfaction index (average, distribution, by country and by category). |
| `GET /api/incidents/results/export` | The caller's last analysis as a download `results.csv` (`metric,value`, one row per metric). `404` (`no_analysis`) if there is none yet. |

Errors use the incident format (`{"error", "message"}`): `400` empty file or no `file` field, `413` file over the limit, `415` not `multipart/form-data` or not a `.csv`, `422` content that cannot be analysed (missing columns, wrong separator, not UTF-8, no data rows).

The upload limit is `ANALYSIS_MAX_UPLOAD_MB` (default 250, about a million rows). The file is read as a stream from a temporary file that is deleted afterwards; only the aggregate numbers of each user's last analysis are kept, in memory, so they are lost when the API restarts. The file contains customer emails: no cell value is ever returned, exported or logged.

### Seed the historical incidents

From the repository root, with `DATABASE_URL` set as for the API (for local use, `sqlite:///./services/dev.db` works):

```bash
services/.venv/bin/python scripts/seed_incidents.py path/to/incidents-trackflow.csv
```

Without an argument the script reads `data/raw/incidents-trackflow.csv`, which is git-ignored because the export contains personal data. Every row goes through the shared validation and these transformations before insert: `description` → `title` (first 120 characters, trimmed) and `description` (verbatim); `date` → `created_at` and `updated_at` (midnight UTC); `OPEN`/`CLOSED`/`DISCARDED` → `open`/`resolved`/`discarded`; `LOST_PARCEL` → `lost_parcel`, `DELAYED_DELIVERY`/`DAMAGE` → `carrier_issue`, `WRONG_ADDRESS` → `delivery_failure`, `RETURN_REQUEST` → `returns_issue`; country `US` → `la_office`, `ES` → `zaragoza_office`; `origin` is always `customer`. Other columns (names, emails) are never read.

Invalid rows (missing required field, unknown status, category or country, bad date) are not inserted and are listed with their line number and reason at the end. The CSV `incident_id` is not stored on the incident; it is kept in the `incident_seed_ledger` table, so a second run inserts nothing. Expected summary after seeding the 95 valid rows: status `open` 29 / `resolved` 52 / `discarded` 14, category `lost_parcel` 14 / `carrier_issue` 45 / `delivery_failure` 19 / `returns_issue` 17.

### Known gaps

- The form and messages are English only (no bilingual labels yet).
- Automatic alerts for SLA-sensitive incidents are out of scope; the data model supports filtering for them.
