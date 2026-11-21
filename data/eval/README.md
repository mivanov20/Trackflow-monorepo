# `data/eval` folder

This folder is for **evaluation and validation**: evaluation datasets, golden sets, experiment results, metrics, and artifacts used to measure quality for models, RAG, agents, or pipelines.

- **Main purpose**: centralize evaluation inputs and outputs so improvements stay measurable across project milestones.
- **Recommendation**: document each evaluation set (what it measures, how it was built, success criteria) and avoid sensitive data; use synthetic or anonymized data when needed.

## `incidents-synthetic.csv`

Synthetic version of the after-sales incident file (100 rows: 95 valid, 5 invalid, 52 closed with an average score of 3.06) with fake `@example.com` emails. It reproduces the distribution the briefing describes for the real test file, so `scripts/analyze.py`, `POST /api/incidents/analyze` and the tests can be checked without the real data, which contains customer emails. Regenerate it with `python packages/shared/tests/synthetic_incidents.py`.

> _Spanish version: [README.es.md](./README.es.md)._
