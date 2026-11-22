"""The orchestrator agent: splits one RFP into one subtask per department the requested scope really needs.

It decides WHICH departments from the services the RFP asks for and does not ask for (`metadata.detect_services`): ModaViva wants warehousing and returns and keeps
its own carrier, so `warehouse` and `reverse` are triggered and `lastmile` is not. Each subtask carries only what its worker may see:

  * the shared metadata (client, country, currency, volume, deadline): the same for everybody;
  * the EXTRACTS of the document that mention that department's subject (a worker never receives the slices of another department);
  * a one-line focus (what that department contributes to the proposal, CONTEXT 2.1; the model may reword it when it is configured).

The orchestrator never reads figures into the subtasks: if the RFP states no volume, the shared metadata says `None` and the worker records an open question.
"""

from __future__ import annotations

import re

from . import llm, metadata
from .config import DEPARTMENTS

# What makes a block of the RFP relevant for a department, besides the service words themselves (metadata.SERVICE_TERMS).
EXTRA_TERMS = {
    "warehouse": r"\bskus?\b|\bpallets?\b|capacidad|capacity|\berp\b|\bapi\b|integraci|onboarding|inventor|\bstock\b|ubicaci|location|\bsla\b",
    "lastmile": r"destinos?|destinations?|\bzones?\b|coast|rural|nationwide|across the us|deliver\w+|entreg\w+|\bsla\b|\bparcels?\b|packages?|paquetes?|ship",
    "reverse": r"inspecci\w+|inspection|refurb|reacondicion|\brestock|\brma\b|exchange|cambios?|refund|reembolso",
}
SHARED_FIELDS = ("client_name", "client_country", "currency", "monthly_volume", "deadline", "budget_range", "services_requested")


def shared_metadata(meta: dict) -> dict:
    return {key: meta.get(key) for key in SHARED_FIELDS}


def extracts_for(department_id: str, markdown: str) -> list[str]:
    """The blocks of the document that talk about this department's subject, in the order they appear, without the ones that only say it is NOT wanted."""
    service = DEPARTMENTS[department_id]["service"]
    pattern = re.compile(f"(?:{metadata.SERVICE_TERMS[service]})|(?:{EXTRA_TERMS[department_id]})")
    picked: list[str] = []
    for block in metadata.blocks(markdown):
        folded = metadata.fold(block)
        if not pattern.search(folded):
            continue
        cue = metadata.NEGATION_CUES.search(folded)
        direct = re.search(metadata.SERVICE_TERMS[service], folded)
        if cue and direct and direct.start() >= cue.start():
            continue  # "No se requiere ... ultima milla": a statement about what is excluded, not something to quote to that department
        if block not in picked:
            picked.append(block)
    return picked


def plan(markdown: str, meta: dict) -> list[dict]:
    """One subtask for each department in `meta["departments_needed"]` (CONTEXT 2.1 order), nothing for the others."""
    shared = shared_metadata(meta)
    subtasks = []
    for department_id in meta["departments_needed"]:
        spec = DEPARTMENTS[department_id]
        subtasks.append(
            {
                "department_id": department_id,
                "department": spec["name"],
                "owner": spec["owner"],
                "contributes": spec["contributes"],
                "focus": f"What {spec['name']} must provide for this proposal: {spec['contributes'].lower()}.",
                "shared": shared,
                "extracts": extracts_for(department_id, markdown),
            }
        )
    return reword_focus(subtasks, shared) if subtasks else subtasks


def reword_focus(subtasks: list[dict], shared: dict) -> list[dict]:
    """With a model configured, the focus of each subtask is written for this client. Only the sentence changes: departments and extracts do not."""
    answer = llm.ask_json(
        "You are the orchestrator of TrackFlow's RFP intake. For each department write ONE sentence saying what that department must provide for this client's proposal. "
        'Return JSON: {"focus": {"<department_id>": "<sentence>"}}.',
        "Departments: " + "; ".join(f"{task['department_id']} ({task['contributes']})" for task in subtasks) + f"\nClient data: {shared}",
        required=("focus",),
        max_tokens=400,
    )
    focus = answer.get("focus") if answer else None
    if isinstance(focus, dict):
        for task in subtasks:
            sentence = focus.get(task["department_id"])
            if isinstance(sentence, str) and 10 <= len(sentence.strip()) <= 300 and not re.search(r"\d", sentence):
                task["focus"], task["focus_engine"] = sentence.strip(), "llm"
    return subtasks
