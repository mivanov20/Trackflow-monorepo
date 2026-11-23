"""Human confirmation for irreversible actions (CONTEXT section 5).

The four actions TrackFlow says a person must confirm, where each is decided in this codebase, and what the code does today:

| Action (CONTEXT section 5) | Where it is decided | What enforces the human |
|---|---|---|
| Approve a return above the team's monetary value | the returns approval engine (NOT BUILT in this fork) | `decide_return`: above `RETURN_APPROVAL_LIMIT_EUR`, or when any rule check fails or is unknown, the answer is `needs_human`; never `approve` |
| Confirm dispatch of a high-value shipment | `POST /inventory/orders/outbound` (a real endpoint, staff only) | `require_dispatch_confirmation`: from `DISPATCH_CONFIRMATION_UNITS` units, the request needs `human_confirmation: true` or answers `409 human_confirmation_required` |
| Change the carrier of a shipment already in transit | the carrier selection engine (NOT BUILT in this fork) | `carrier_change_decision`: any shipment past `created`/`ready` is `needs_human`; the agent has no tool for it |
| Share one client brand's volume or incident data with another | the agent's tools and the output guard | the live tools are refused to customer accounts (`tools_allowed`, audit finding C-01); no tool takes a "client" argument; a person shares data, not the agent |

Two rules apply to every gate: it FAILS CLOSED (a value that is missing, malformed or unknown means "ask a person", never "go ahead"), and the confirmation is a field of the request
made by the signed-in staff member (it is logged with their account), not something the AI can supply: the agent has no write scope and no route to these endpoints.
Thresholds are TrackFlow's to set (environment), the defaults are deliberately low.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, status

logger = logging.getLogger("trackflow.security")
HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default  # a malformed setting keeps the safe default


def return_limit_eur() -> float:
    return _number("RETURN_APPROVAL_LIMIT_EUR", 150.0)


def dispatch_confirmation_units() -> int:
    return int(_number("DISPATCH_CONFIRMATION_UNITS", 250))


def confirmation_required(action: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": HUMAN_CONFIRMATION_REQUIRED, "action": action, "message": f"A person must confirm this action: {reason}. Send it again with human_confirmation set to true."})


def require_dispatch_confirmation(*, exit_type: str, quantity: int, confirmed: bool, actor: str) -> None:
    """High-value dispatch: units stand in for value (the inventory has no prices). Raises 409 unless a signed-in person confirmed; the confirmation is logged with who gave it."""
    if exit_type != "dispatch" or quantity < dispatch_confirmation_units():
        return
    if not confirmed:
        logger.warning("dispatch of %d units held: human confirmation required (requested by %s)", quantity, actor)
        raise confirmation_required("high_value_dispatch", f"a dispatch of {quantity} units is at or above the {dispatch_confirmation_units()}-unit limit")
    logger.warning("high-value dispatch of %d units confirmed by %s", quantity, actor)


def decide_return(*, amount_eur: float | None, rules_passed: bool | None) -> str:
    """The decision of a returns engine: `approve` only when the amount is known, is within the limit and every rule check passed; `needs_human` otherwise. Never rejects by itself."""
    if rules_passed is not True or amount_eur is None or amount_eur != amount_eur or amount_eur < 0:
        return "needs_human"
    return "approve" if amount_eur <= return_limit_eur() else "needs_human"


def carrier_change_decision(shipment_status: str | None) -> str:
    """Changing the carrier is automatic only before the parcel moves; once it is in transit (or the state is unknown) a person decides."""
    return "allowed" if shipment_status in ("created", "ready") else "needs_human"
