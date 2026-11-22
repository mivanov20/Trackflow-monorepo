"""What TrackFlow's CONTEXT-company.md (Milestone 9) fixes for the RFP intake: departments, owners, currencies, statuses.

Nothing here is a secret. The department identifiers are exactly the ones of the CONTEXT: `warehouse`, `lastmile`, `reverse`.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# --- ticket lifecycle (CONTEXT 2.3). Part 1 only uses the first three; `failed` is ours: the pipeline broke, so the ticket must not stay `analyzing` forever.
ANALYZING = "analyzing"
DISCARDED = "discarded"
INTAKE_COMPLETE = "intake_complete"
FAILED = "failed"
PART1_STATUSES = (ANALYZING, DISCARDED, INTAKE_COMPLETE, FAILED)

HANDOFF_READY = "ready_for_part_2"
HANDOFF_NOT_READY = "not_ready"

# --- departments (CONTEXT 2.1), in the order they are listed everywhere
DEPARTMENTS = {
    "warehouse": {
        "name": "Warehouse Operations",
        "owner": "Ana Whitfield",
        "contributes": "Storage capacity, cost per pallet/SKU, onboarding time",
        "service": "warehousing",
    },
    "lastmile": {
        "name": "Last Mile and Carrier Management",
        "owner": "Carlos Vega",
        "contributes": "Cost per shipment, available carriers by destination, delivery SLA",
        "service": "last_mile",
    },
    "reverse": {
        "name": "Reverse Logistics",
        "owner": "Sofía Ramos",
        "contributes": "Returns processing cost and turnaround time",
        "service": "reverse_logistics",
    },
}
DEPARTMENT_IDS = tuple(DEPARTMENTS)
SERVICES = tuple(spec["service"] for spec in DEPARTMENTS.values())
SERVICE_TO_DEPARTMENT = {spec["service"]: department_id for department_id, spec in DEPARTMENTS.items()}
COMMERCIAL_DIRECTOR = "Miguel Torres"  # receives the RFPs; the arbiter of Part 3

# --- currency is decided by the client's country (CONTEXT section 5)
COUNTRY_CURRENCY = {"US": "USD", "ES": "EUR"}

# Business guidelines Part 2 will check; Part 1 only passes them along in the handoff so no one has to re-read the CONTEXT.
GUIDELINES = (
    "Prices are quoted in USD for US operations and EUR for Spain operations (client_country decides).",
    "Every proposal must state the on-time delivery SLA (%) TrackFlow commits to.",
    "No proposal may promise returns processing in under 48 hours.",
    "Every proposal must include a volume-based discount tier table.",
    "No proposal may disclose negotiated rates with specific carriers, only the final cost offered to the client.",
)

# --- limits
MAX_UPLOAD_BYTES = int(os.getenv("RFP_MAX_UPLOAD_MB", "10")) * 1024 * 1024
MODEL_INPUT_CHARS = 24_000  # what one agent call may read of the document; an RFP is a few pages, a huge file is cut here
HANDOFF_SCHEMA_VERSION = 1


def raw_dir() -> Path:
    """Where uploaded PDFs are kept: `data/raw/rfp/` (a runtime artifact of the intake, never committed). `RFP_RAW_DIR` overrides it for tests."""
    override = os.getenv("RFP_RAW_DIR")
    return Path(override) if override else ROOT / "data" / "raw" / "rfp"


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()
