"""Builds a SYNTHETIC analyzer-style CSV for tests.

The real `incidents-trackflow.csv` is not in the repository (it holds personal data).
This stand-in has 95 valid rows distributed exactly like the briefing's expected
post-transformation totals, plus 5 invalid rows, so the seed pipeline can be checked.
"""

import csv
from pathlib import Path

# Raw CSV values chosen so the transformed totals match the briefing:
# status open 29 / resolved 52 / discarded 14, category lost_parcel 14 /
# carrier_issue 45 / delivery_failure 19 / returns_issue 17.
RAW_STATUSES = ["OPEN"] * 29 + ["CLOSED"] * 52 + ["DISCARDED"] * 14
RAW_CATEGORIES = (
    ["LOST_PARCEL"] * 14
    + ["DELAYED_DELIVERY"] * 25
    + ["DAMAGE"] * 20
    + ["WRONG_ADDRESS"] * 19
    + ["RETURN_REQUEST"] * 17
)

EXPECTED_STATUS = {"open": 29, "resolved": 52, "discarded": 14}
EXPECTED_CATEGORY = {"lost_parcel": 14, "carrier_issue": 45, "delivery_failure": 19, "returns_issue": 17}

FIELDNAMES = ["incident_id", "customer_email", "description", "date", "status", "category", "country"]


def valid_rows() -> list[dict[str, str]]:
    rows = []
    for index, (status, category) in enumerate(zip(RAW_STATUSES, RAW_CATEGORIES), start=1):
        rows.append(
            {
                "incident_id": f"INC-{index:04d}",
                "customer_email": f"person{index}@example.com",
                "description": f"Synthetic incident {index} reported by a customer.",
                "date": f"2025-0{(index % 9) + 1}-{(index % 27) + 1:02d}",
                "status": status,
                "category": category,
                "country": "US" if index % 2 else "ES",
            }
        )
    return rows


def invalid_rows() -> list[dict[str, str]]:
    base = valid_rows()[0]
    return [
        {**base, "incident_id": "BAD-1", "description": ""},
        {**base, "incident_id": "BAD-2", "status": "PENDING"},
        {**base, "incident_id": "BAD-3", "category": "FIRE"},
        {**base, "incident_id": "BAD-4", "country": "FR"},
        {**base, "incident_id": "BAD-5", "date": "not-a-date"},
    ]


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path
