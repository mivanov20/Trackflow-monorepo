"""RFP intake (Milestone 9, part 1): a PDF arrives, becomes Markdown, is classified, and a valid RFP is split by department and consolidated for Sales.

    from data.pipelines.rfp_intake import run_intake     # what the API schedules and the CLI calls
    from data.pipelines.rfp_intake import build_graph    # the dedicated LangGraph (docs/rfp/design.md)

The HTTP layer (`services/routers/rfp.py`) only stores the upload, schedules `run_intake` and reads tickets back.
"""

from .config import ANALYZING, DEPARTMENTS, DISCARDED, FAILED, INTAKE_COMPLETE
from .graph import build_graph, compile_graph
from .proposal_runner import run_proposal
from .runner import run_intake

__all__ = ["ANALYZING", "DEPARTMENTS", "DISCARDED", "FAILED", "INTAKE_COMPLETE", "build_graph", "compile_graph", "run_intake", "run_proposal"]
