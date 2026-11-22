# `data/raw` folder

This folder is intended for **raw data** related to the company: dumps, exports, sample files, event samples, or untransformed datasets.

- **Main purpose**: serve as a landing zone or reference for original data before pipelines process it.
- **Recommendation**: document each dataset’s origin, format, expected size, privacy/PII considerations, and how it is versioned (ideally avoiding sensitive data in the repository).

> _Spanish version: [README.es.md](./README.es.md)._

## `data/raw/rfp/` (runtime, not versioned)

PDFs uploaded through the backoffice's **RFP intake** are stored here as `<ticket_id>.pdf` by `POST /rfp/tickets` (Milestone 9). They are client documents, so the folder is
in `.gitignore`; the seed PDFs of the course live only in `tests/pipelines/fixtures/rfp-requests/` and are uploaded through the UI like any other RFP. See [docs/rfp/design.md](../../docs/rfp/design.md).
