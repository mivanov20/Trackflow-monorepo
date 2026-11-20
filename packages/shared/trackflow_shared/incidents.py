"""Incident domain rules, payload validation and CSV validation/transformation.

The API (`services/incident-manager`) and the seed script (`scripts/seed_incidents.py`)
both import this module so the rules exist in exactly one place.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

STATUSES = ("open", "in_progress", "resolved", "discarded")
ORIGINS = ("customer", "branch", "internal")

CATEGORIES = (
    "lost_parcel",
    "delivery_failure",
    "inventory_discrepancy",
    "carrier_issue",
    "returns_issue",
    "warehouse_incident",
    "system_failure",
    "client_complaint",
    "other",
)

BRANCHES = {
    "central": "Central",
    "la_warehouse": "Los Angeles — Warehouse",
    "la_office": "Los Angeles — Office",
    "zaragoza_warehouse": "Zaragoza — Warehouse",
    "zaragoza_office": "Zaragoza — Office",
}

# Lifecycle: resolved and discarded are final states.
STATUS_TRANSITIONS = {
    "open": ("in_progress", "discarded"),
    "in_progress": ("resolved", "discarded"),
    "resolved": (),
    "discarded": (),
}

TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 5000

FIELD_LABELS = {
    "title": "Title",
    "description": "Description",
    "category": "Category",
    "status": "Status",
    "origin": "Origin",
    "branch": "Branch",
}


@dataclass(frozen=True)
class FieldError:
    """A validation problem tied to one field, written for end users."""

    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


def _choices(values) -> str:
    return ", ".join(values)


def check_choice(field: str, value: Any, allowed) -> FieldError | None:
    """Return an error unless `value` is one of `allowed` (exact match)."""
    label = FIELD_LABELS.get(field, field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return FieldError(field, f"{label} is required.")
    if not isinstance(value, str) or value not in allowed:
        return FieldError(field, f"{label} must be one of: {_choices(allowed)}.")
    return None


def validate_incident_payload(data: Any) -> tuple[dict[str, str] | None, list[FieldError]]:
    """Validate the body of POST /api/incidents.

    Returns `(clean_values, [])` when valid, or `(None, errors)` listing every problem.
    `status` is optional and defaults to `open`.
    """
    if not isinstance(data, dict):
        return None, [FieldError("body", "The request body must be a JSON object.")]

    errors: list[FieldError] = []
    clean: dict[str, str] = {}

    for field, max_length in (("title", TITLE_MAX_LENGTH), ("description", DESCRIPTION_MAX_LENGTH)):
        value = data.get(field)
        label = FIELD_LABELS[field]
        if not isinstance(value, str) or not value.strip():
            errors.append(FieldError(field, f"{label} is required."))
        elif len(value.strip()) > max_length:
            errors.append(FieldError(field, f"{label} must be at most {max_length} characters."))
        else:
            clean[field] = value.strip()

    choices = (
        ("category", data.get("category"), CATEGORIES),
        ("origin", data.get("origin"), ORIGINS),
        ("branch", data.get("branch"), tuple(BRANCHES)),
    )
    for field, value, allowed in choices:
        error = check_choice(field, value, allowed)
        if error:
            errors.append(error)
        else:
            clean[field] = value

    status = data.get("status", "open")
    error = check_choice("status", status, STATUSES)
    if error:
        errors.append(error)
    else:
        clean["status"] = status

    return (None, errors) if errors else (clean, [])


def validate_status_value(value: Any) -> FieldError | None:
    """Check that a status value belongs to the lifecycle enum."""
    return check_choice("status", value, STATUSES)


def validate_transition(current: str, new: str) -> FieldError | None:
    """Check that moving from `current` to `new` follows the lifecycle."""
    allowed = STATUS_TRANSITIONS.get(current, ())
    if new in allowed:
        return None
    if not allowed:
        return FieldError("status", f"This incident is {current} and can no longer change status.")
    return FieldError(
        "status",
        f"An incident that is {current} can only move to: {_choices(allowed)}.",
    )


def validate_filter(field: str, value: str | None) -> FieldError | None:
    """Validate an optional list filter (`None` means no filter)."""
    if value is None:
        return None
    allowed = {"status": STATUSES, "origin": ORIGINS, "category": CATEGORIES, "branch": tuple(BRANCHES)}[field]
    if value in allowed:
        return None
    return FieldError(field, f"{FIELD_LABELS[field]} filter must be one of: {_choices(allowed)}.")


# --- Analyzer CSV -> incident model ------------------------------------------------

CSV_REQUIRED_FIELDS = ("incident_id", "description", "date", "status", "category", "country")

CSV_STATUS_MAP = {"OPEN": "open", "CLOSED": "resolved", "DISCARDED": "discarded"}

CSV_CATEGORY_MAP = {
    "LOST_PARCEL": "lost_parcel",
    "DELAYED_DELIVERY": "carrier_issue",
    "WRONG_ADDRESS": "delivery_failure",
    "RETURN_REQUEST": "returns_issue",
    "DAMAGE": "carrier_issue",
}

CSV_COUNTRY_BRANCH_MAP = {"US": "la_office", "ES": "zaragoza_office"}


def validate_csv_row(row: dict[str, Any]) -> list[str]:
    """Return the reasons a CSV row is invalid (empty list means valid).

    A row is invalid when a required field is missing or blank, or when its status,
    category, country or date is outside the allowed set / format.
    """
    reasons: list[str] = []
    values = {field: (row.get(field) or "").strip() for field in CSV_REQUIRED_FIELDS}

    for field in CSV_REQUIRED_FIELDS:
        if not values[field]:
            reasons.append(f"missing {field}")
    if reasons:
        return reasons

    if values["status"] not in CSV_STATUS_MAP:
        reasons.append(f"unknown status '{values['status']}'")
    if values["category"] not in CSV_CATEGORY_MAP:
        reasons.append(f"unknown category '{values['category']}'")
    if values["country"] not in CSV_COUNTRY_BRANCH_MAP:
        reasons.append(f"unknown country '{values['country']}'")
    try:
        datetime.strptime(values["date"], "%Y-%m-%d")
    except ValueError:
        reasons.append(f"invalid date '{values['date']}' (expected YYYY-MM-DD)")
    return reasons


def transform_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a *valid* CSV row to incident model fields.

    `description` -> `title` (first 120 characters, trimmed) and `description` (verbatim),
    `date` -> `created_at` / `updated_at` (midnight UTC), plus the status, category and
    branch maps. `origin` is always `customer` for seed records.
    """
    description = row["description"]
    created_at = datetime.strptime(row["date"].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {
        "title": description[:TITLE_MAX_LENGTH].strip(),
        "description": description,
        "category": CSV_CATEGORY_MAP[row["category"].strip()],
        "status": CSV_STATUS_MAP[row["status"].strip()],
        "origin": "customer",
        "branch": CSV_COUNTRY_BRANCH_MAP[row["country"].strip()],
        "created_at": created_at,
        "updated_at": created_at,
    }


def process_csv_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and transform one CSV row: `(incident_fields, [])` or `(None, reasons)`."""
    reasons = validate_csv_row(row)
    if reasons:
        return None, reasons
    incident = transform_csv_row(row)
    if not incident["title"]:
        return None, ["empty description after trimming"]
    return incident, []
