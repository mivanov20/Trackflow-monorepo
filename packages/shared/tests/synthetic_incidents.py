"""Builds the SYNTHETIC incident file used for tests and demos.

The real `incidents-trackflow.csv` holds customer emails and must not be shared with AI tools or
committed. This stand-in has the same shape and the exact distribution the briefing describes:
100 rows, 95 valid and 5 invalid (one per rule listed in the expected output). Every email is a
fake `@example.com` address. The output is deterministic, and `data/eval/incidents-synthetic.csv`
is a committed copy (a test keeps both in sync).

Regenerate the committed copy from the repository root with:

    python packages/shared/tests/synthetic_incidents.py
"""

import csv
import random
import sys
from pathlib import Path

COLUMNS = [
    "incident_id", "date", "country", "customer_type", "tracking_number", "carrier",
    "category", "description", "status", "customer_email", "satisfaction_score",
]

CARRIERS = {"US": ["UPS", "FEDEX", "DHL_US"], "ES": ["MRW", "SEUR", "DHL_ES", "LOCAL_ES"]}

# What the briefing says the analysis must produce for the 100-row test file.
EXPECTED = {
    "total": 100,
    "valid": 95,
    "invalid": 5,
    "invalid_rules": {
        "invalid_tracking_number": 1,
        "invalid_carrier": 1,
        "invalid_category": 1,
        "invalid_email": 1,
        "closed_without_score": 1,
    },
    "category": {"LOST_PARCEL": 14, "DELAYED_DELIVERY": 38, "WRONG_ADDRESS": 19, "RETURN_REQUEST": 17, "DAMAGE": 7},
    "category_percent": {"LOST_PARCEL": 14.7, "DELAYED_DELIVERY": 40.0, "WRONG_ADDRESS": 20.0, "RETURN_REQUEST": 17.9, "DAMAGE": 7.4},
    "status": {"OPEN": 29, "CLOSED": 52, "DISCARDED": 14},
    "status_percent": {"OPEN": 30.5, "CLOSED": 54.7, "DISCARDED": 14.7},
    "country": {"US": 50, "ES": 45},
    "country_percent": {"US": 52.6, "ES": 47.4},
    "scored": 52,
    "scores": {1: 6, 2: 11, 3: 15, 4: 14, 5: 6},
    "average": 3.06,
}

# Record numbers (1-based, header excluded) of the five invalid rows and the rule each one breaks.
INVALID_RECORDS = {7: "tracking", 23: "carrier", 41: "category", 68: "email", 90: "closed_no_score"}


def _spread(counts: dict, rng: random.Random) -> list:
    items = [value for value, count in counts.items() for _ in range(count)]
    rng.shuffle(items)
    return items


def build_rows() -> list[dict[str, str]]:
    """The 100 rows, in file order."""
    rng = random.Random(2025)
    categories = _spread(EXPECTED["category"], rng)
    statuses = _spread(EXPECTED["status"], rng)
    countries = _spread(EXPECTED["country"], rng)
    scores = _spread(EXPECTED["scores"], rng)

    rows: list[dict[str, str]] = []
    valid_index = 0
    for number in range(1, EXPECTED["total"] + 1):
        if number in INVALID_RECORDS:
            country, category, status, score = ("US" if number % 2 else "ES"), "LOST_PARCEL", "OPEN", ""
        else:
            country, category, status = countries[valid_index], categories[valid_index], statuses[valid_index]
            score = str(scores.pop()) if status == "CLOSED" else ""
            valid_index += 1
        row = {
            "incident_id": f"TRF-{number:06d}",
            "date": f"2025-03-{(number % 28) + 1:02d}",
            "country": country,
            "customer_type": "B2B" if number % 3 == 0 else "B2C",
            "tracking_number": f"TRK{number:09d}",
            "carrier": CARRIERS[country][number % len(CARRIERS[country])],
            "category": category,
            "description": f"Customer reported an issue with order {number}.",
            "status": status,
            "customer_email": f"customer{number:03d}@example.com",
            "satisfaction_score": score,
        }
        problem = INVALID_RECORDS.get(number)
        if problem == "tracking":
            row["tracking_number"] = "SHORT1"
        elif problem == "carrier":
            row["country"], row["carrier"] = "US", "MRW"  # MRW only operates in Spain
        elif problem == "category":
            row["category"] = "UNKNOWN_CATEGORY"
        elif problem == "email":
            row["customer_email"] = "customer068.example.com"
        elif problem == "closed_no_score":
            row["status"], row["satisfaction_score"] = "CLOSED", ""
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, str]] | None = None) -> Path:
    """Write the rows (the standard 100 by default) as a UTF-8, comma-separated file with a header."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(build_rows() if rows is None else rows)
    return path


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[3] / "data" / "eval" / "incidents-synthetic.csv"
    write_csv(target)
    print(f"Wrote {target}", file=sys.stderr)
