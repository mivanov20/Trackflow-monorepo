# TrackFlow public website

The public-facing TrackFlow website is a standalone Next.js 15, React 19, and TypeScript app. The App Router entry points are `app/page.tsx` for the corporate homepage and `app/application/page.tsx` for the simulated client onboarding form. Reusable typed sections and cards live in `app/components.tsx`; the homepage retains the original feature, process, reasons, company snapshot, and footer sections, plus its English/Spanish toggle.

## Run locally

From this directory:

```bash
npm install
npm run dev
```

The app starts on `http://localhost:3000`. From the monorepo root, use `npm --prefix uis/website run dev`. The onboarding form is available at `/application`.

## Verification

```bash
npm run typecheck
npm run build
```

The application form validates locally and simulates submission. It is not connected to a production API.
# TrackFlow public website

Public-facing TrackFlow site and application form, placed at the monorepo's canonical `uis/website/` path. It uses static HTML and vanilla JavaScript; Tailwind CSS, fonts, and editorial photography are loaded from external CDNs.

Repeated homepage cards are defined as data and rendered by the shared builders in `components.js`. The builders create the feature, process, company-reason, and snapshot cards; `language.js` translates their `data-i18n` content after they render.

## Run locally

From the repository root:

```bash
python3 -m http.server 8080 --directory uis/website
```

Open `http://localhost:8080/index.html` for the site or `http://localhost:8080/application.html` for the application form. The existing `WEB_RUN.md` also documents `npx serve .` when run from this directory.

This is a static milestone UI. Do not assume its application form is connected to a production API. Root `../../CONTEXT.md` contains the assigned company briefing and is authoritative when this earlier `TRACKFLOW_README.md` differs.