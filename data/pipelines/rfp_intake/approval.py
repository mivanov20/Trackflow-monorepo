"""The human approval of Part 3: who may approve, what a decision looks like, and the limits of the loops.

CONTEXT-company.md 6: each active department's NAMED OWNER (section 2.1) approves its own section, independently of the others. There is no approval ladder: TrackFlow has peer department owners
only, so the only extra person in the flow is the fixed arbiter of section 7, Miguel Torres, who acts through the arbitration node and never as a second approver of every section.

A decision is validated BEFORE it is allowed back into the graph (`validate_decision`): the API calls it before resuming a branch, and the branch calls it again on resume, so a malformed value can
never move a section forward whichever way it arrives.

Limits (verifiable in code, configurable by environment):
    max_rounds()              how many decisions one section may go through (request changes / reject send it back for a new draft); the last round that still asks for changes ends the
                              section as `rejected` and the ticket goes to `needs_human_review`
    max_arbitration_rounds()  how many times the arbitration may send sections back for conflicts (currency-mismatch is "rejected if unresolved after the iteration limit")
"""

from __future__ import annotations

import os
import unicodedata

from .config import DEPARTMENTS

DECISIONS = ("approve", "request_changes", "reject")
DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_ARBITRATION_ROUNDS = 2
MIN_COMMENT, MAX_COMMENT = 5, 2000
MIN_EDIT, MAX_EDIT = 60, 20000
# structured commitments an owner may state with a decision; the arbitration reads them (volume-vs-capacity), never free text
COMMITMENT_KEYS = {"warehouse": "capacity_orders_per_month", "lastmile": "assumed_monthly_volume"}


class DecisionError(ValueError):
    """The decision is not acceptable. The message is written for the person who sent it."""


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def max_rounds() -> int:
    return _int_env("RFP_MAX_APPROVAL_ROUNDS", DEFAULT_MAX_ROUNDS)


def max_arbitration_rounds() -> int:
    return _int_env("RFP_MAX_ARBITRATION_ROUNDS", DEFAULT_MAX_ARBITRATION_ROUNDS)


def _same_person(left: str, right: str) -> bool:
    def fold(text: str) -> str:
        return " ".join("".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).casefold().split())

    return fold(left) == fold(right)


def validate_decision(raw, department_id: str) -> dict:
    """The cleaned decision `{decision, comment, approver, edited_content, commitments}`, or a DecisionError saying what is wrong."""
    if department_id not in DEPARTMENTS:
        raise DecisionError("Unknown department.")
    owner = DEPARTMENTS[department_id]["owner"]
    if not isinstance(raw, dict):
        raise DecisionError("The decision must be an object with a `decision`.")
    decision = raw.get("decision")
    if decision not in DECISIONS:
        raise DecisionError(f"`decision` must be one of: {', '.join(DECISIONS)}.")
    approver = str(raw.get("approver") or "").strip()
    if not _same_person(approver, owner):
        raise DecisionError(f"Only {owner}, the owner of {DEPARTMENTS[department_id]['name']}, can sign off this section.")
    comment = str(raw.get("comment") or "").strip()
    if len(comment) > MAX_COMMENT:
        raise DecisionError(f"The comment is too long (the limit is {MAX_COMMENT} characters).")
    if decision != "approve" and len(comment) < MIN_COMMENT:
        raise DecisionError("Say what has to change: a comment is required to reject or to request changes.")
    edited = raw.get("edited_content")
    if edited is not None:
        if decision != "request_changes":
            raise DecisionError("An edited draft can only be sent with `request_changes`: it is checked again and then you approve it.")
        if not isinstance(edited, str) or not (MIN_EDIT <= len(edited.strip()) <= MAX_EDIT):
            raise DecisionError(f"The edited draft must be text of {MIN_EDIT} to {MAX_EDIT} characters.")
        edited = edited.strip()
    commitments = raw.get("commitments") or {}
    if not isinstance(commitments, dict):
        raise DecisionError("`commitments` must be an object.")
    allowed = COMMITMENT_KEYS.get(department_id)
    cleaned: dict = {}
    for key, value in commitments.items():
        if key != allowed:
            raise DecisionError(f"{DEPARTMENTS[department_id]['name']} cannot state `{key}`." if allowed is None else f"The only commitment for this department is `{allowed}`.")
        if isinstance(value, bool) or not isinstance(value, int) or not (0 < value <= 10_000_000):
            raise DecisionError(f"`{key}` must be a whole number of orders per month, above zero.")
        cleaned[key] = value
    return {"decision": decision, "comment": comment, "approver": owner, "edited_content": edited, "commitments": cleaned}
