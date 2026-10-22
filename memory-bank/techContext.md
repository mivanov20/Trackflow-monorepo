# Technical context

## Repository shape

This is a company-project monorepo. User interfaces belong in `uis/`, backend APIs/workers in `services/`, data pipelines and source data in `data/`, product agents in `agents/`, reusable agent capabilities in `skills/`, and shared libraries in `packages/` or `shared/`. Read the relevant folder `README.md` before adding a project. `.agents/` is development-agent configuration and is not the same as product code in `agents/` or `skills/`.

## Applications

- Root application: Next.js 15, React 19, and TypeScript, with App Router code in `app/`, reusable UI in `components/`, candidate API helpers in `lib/`, and candidate types in `types/`. The current root application is a candidate-pipeline demo; do not treat it as TrackFlow domain truth.
- `uis/website/`: the existing TrackFlow public site, implemented as static HTML and vanilla JavaScript with Tailwind loaded from its CDN. Its entry points are `index.html` and `application.html`; `components.js` renders shared data-driven homepage cards, while `index.js`, `language.js`, and `validation.js` handle page interactions, localization, and form validation.
- `uis/backoffice/`: TrackFlow internal operations UI, implemented as static HTML/CSS/vanilla JavaScript. Its entry view `index.html` uses sample-only metrics with no API connection; `suppliers.html` is the supplier directory and talks to `services/supplier-directory`.
- `services/supplier-directory/`: FastAPI + TinyDB + Pydantic supplier API (Python 3.12, managed with uv). Run from that folder: `uv run seed`, `uv run serve` (port 8000), `uv run pytest`. Supplier fields (`rate_per_shipment`, `currency` tied to country `USA`/`Spain`), the eight snake_case categories, statuses `active`/`suspended` and the 15 seed suppliers come from the Supplier Directory section of `CONTEXT.md` and are documented in its README.
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

There is no root test script currently. Use the narrowest relevant runtime smoke check for static apps, and report checks that do not exist rather than claiming they passed.

## Conventions and constraints

- Follow the owning app's existing stack; do not add a framework or dependency for a static page.
- Keep public and internal interfaces in their canonical `uis/website` and `uis/backoffice` directories.
- Backend work belongs under `services/`; do not add an API merely to support a static entry view.
- Preserve unrelated milestone work and inspect `git status` before staging or committing.
- Treat root `CONTEXT.md` as the authoritative company briefing. It identifies Thomas Harry as CEO and Daniel Espinoza as the creator of TrackFlow Tech.
- Update this memory when architecture, app entry points, commands, or verified domain decisions change.