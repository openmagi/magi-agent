"""Deep-solve verification-and-refinement pipeline — orchestrator core.

Pure module: no imports from magi_agent.runtime, magi_agent.tools,
or magi_agent.transport. All external dependencies are injected via
DeepSolveDeps.
"""
from magi_agent.solving.deep_solve import (
    DeepSolveConfig,
    DeepSolveDeps,
    DeepSolveOutcome,
    DeepSolveRunState,
    DeepSolveVerdictData,
    ExecutionReport,
    Finding,
    FindingCategory,
    StageResult,
    assemble_refold,
    run_deep_solve,
)
from magi_agent.solving.templates import (
    DOMAIN_TEMPLATES,
    DomainTemplate,
    get_template,
)

__all__ = [
    "DeepSolveConfig",
    "DeepSolveDeps",
    "DeepSolveOutcome",
    "DeepSolveRunState",
    "DeepSolveVerdictData",
    "ExecutionReport",
    "Finding",
    "FindingCategory",
    "StageResult",
    "assemble_refold",
    "run_deep_solve",
    "DOMAIN_TEMPLATES",
    "DomainTemplate",
    "get_template",
]
