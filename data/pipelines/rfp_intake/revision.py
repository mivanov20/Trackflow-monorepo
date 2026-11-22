"""A section revised after a decision (Part 3): the owner asked for changes, or the arbiter's rule did.

Two ways in, one result `{content, evaluation_result, engine}`:
  * the owner sends an edited draft: it is evaluated by the same three evaluators as any draft (the owner's own figures are not "invented": `human_authored`), so what comes back to the approval step
    has a structured EvaluationResult;
  * no edit: the generator of the department writes the section again with the comment as feedback (a department-scoped run of the Part 2 loop, bounded by its iteration limit).
Every evaluation and generation is traced as part 3, under its agent.
"""

from __future__ import annotations

from . import drafting_graph, evaluators, trace
from .knowledge import Facts


class TraceReporter(drafting_graph.NullReporter):
    """The Part 2 graph reports to this: nothing is persisted as a section state (Part 3 owns that now), but every node execution is traced, as part 3."""

    def __init__(self, engine, ticket_id: str):
        self.engine, self.ticket_id = engine, ticket_id

    def trace(self, agent, node, input, output, department_id=None):
        trace.record(self.engine, self.ticket_id, 3, agent, node, input, output, department_id)


def evaluate_edit(engine, ticket_id: str, brief: dict, facts: dict, content: str) -> dict:
    facts_obj = Facts(**facts)
    readability = evaluators.evaluate_readability(content)
    relevance = evaluators.evaluate_relevance(content, brief, facts_obj, [], human_authored=True)
    compliance = evaluators.evaluate_compliance(content, brief, facts_obj)
    result = evaluators.consolidate(brief["department_id"], f"{ticket_id}:{brief['department_id']}", 0, readability, relevance, compliance)
    trace.record(
        engine, ticket_id, 3, "evaluators", "evaluate_edit",
        {"characters": len(content), "authored_by": f"owner of {brief['department_id']}"},
        {"overall_pass": result["overall_pass"], "readability": readability["pass"], "relevance": relevance["pass"], "compliance": compliance["rule_ids"]},
        brief["department_id"],
    )
    return result


def revise(engine, ticket_id: str, brief: dict, facts: dict, previous: str, comment: str, fixes: list[dict], edited_content: str | None, max_iterations: int, iteration: int) -> dict:
    if edited_content:
        result = evaluate_edit(engine, ticket_id, brief, facts, edited_content)
        result["iteration"] = iteration
        return {"content": edited_content, "evaluation_result": result, "engine": "human"}
    feedback = {"text": comment, "fixes": fixes or [{"kind": "human", "id": "owner-request", "instruction": comment}]}
    final = drafting_graph.section_graph().invoke(
        {"brief": brief, "facts": facts, "ticket_id": ticket_id, "iteration": 1, "max_iterations": max_iterations, "feedback": feedback, "draft": {"content": previous}, "evals": [], "attempts": []},
        {"configurable": {"reporter": TraceReporter(engine, ticket_id)}},
    )
    last = final["attempts"][-1]
    result = {**last["evaluation_result"], "iteration": iteration}
    return {"content": last["draft"], "evaluation_result": result, "engine": last["engine"]}
