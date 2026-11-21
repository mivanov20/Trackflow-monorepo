# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

> _Spanish version: [README.es.md](./README.es.md)._

## `seed_incidents.py`

Loads the analyzer CSV into the incident manager (idempotent, invalid rows reported at the end). Run it from the repository root with `DATABASE_URL` set; see "Incident manager" in `services/README.md` for usage and the CSV-to-model transformations.
