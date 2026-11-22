# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

> _Spanish version: [README.es.md](./README.es.md)._

## `seed_incidents.py`

Loads the analyzer CSV into the incident manager (idempotent, invalid rows reported at the end). Run it from the repository root with `DATABASE_URL` set; see "Incident manager" in `services/README.md` for usage and the CSV-to-model transformations.

## `analyze.py`

Analyses the after-sales incident file (`incidents.csv`) and prints a summary for the CX team. It needs only Python 3.11+ (standard library), no installation, and works from any folder:

```bash
python scripts/analyze.py incidents-trackflow.csv
```

On Windows, if `python` is not on your PATH, use the project interpreter: `services\.venv\Scripts\python.exe scripts\analyze.py incidents-trackflow.csv`.

It reads the file row by row (a million rows do not need to fit in memory), validates every record and prints:

- total records, valid and invalid, and how many records fall into each invalid rule (tracking number, carrier/country mismatch, category, email, closed without score, plus country, description, score range, status and missing required fields), with the line numbers to review;
- breakdowns of the valid records by category, status and country;
- the satisfaction index of closed incidents: scored incidents, average, distribution, and the average by country and by category (to see whether the problem is worse in the US or Spain, or in specific categories).

At the end it asks `Export results to CSV? [y / n]`; `y` writes `results.csv` (columns `metric,value`, one row per metric). `--export` writes it without asking. An unusable file (not found, empty, wrong separator or missing columns, not UTF-8, no rows) prints a clear message and exits with code 1.

**The file contains customer emails.** The script never prints, logs or exports an email or any other cell value: invalid records are reported by line number and rule, and unexpected errors are reported without details. Keep the real file out of git and out of AI tools; `data/raw/incidents-trackflow.csv` is git-ignored.

The validation and the metrics are in `packages/shared/trackflow_shared/incident_analysis.py`, shared with the API endpoint `POST /api/incidents/analyze` (see `services/README.md`). `data/eval/incidents-synthetic.csv` is a synthetic file with the same distribution as the briefing's test file (100 rows, 95 valid, average 3.06) and fake emails, so you can try the script, the API and the page without the real data. To check the real file, run `python scripts/analyze.py data/raw/incidents-trackflow.csv` and compare the numbers with the briefing.
