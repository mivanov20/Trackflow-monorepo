"""The harness and guardrails of the support agent (milestone 8, part 2). See docs/agent/guardrails-design.md."""

from data.pipelines import rag

from . import isolation

# Every retrieved document, ticket and tool result goes through the isolation layer on its way into a prompt (also for the plain RAG endpoint in this process).
rag.set_context_filter(isolation.filter_for_prompt)
