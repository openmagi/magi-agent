"""First-party DeepSolve tool metadata provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``ToolProvideContext`` (D5) — identical capability to
any user-authored tool provider — and registers a ``ToolManifest`` via its
single ``register`` capability. Modeled on
``magi_agent/firstparty/packs/tools_persistent_python/impl.py``.

Pure declaration — catalog metadata for discoverability + Customize surfacing.
The runtime HANDLER lives in ``magi_agent.plugins.native.deep_solve`` and is
registered via ``plugins/native_catalog.py`` (U3), exactly like SpawnAgent
(design D2/B2; a pack-authored handler is a future authoring-ABI gap).
Removable via ``config.toml [packs] disable`` / dashboard remove — the
handler's ``_deep_solve_pack_enabled`` dispatch gate honors removal so the
pack stays an honest install axis.
"""
from __future__ import annotations

from magi_agent.packs.context import ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_SOURCE
from magi_agent.tools.manifest import Budget, ToolManifest

DEEP_SOLVE_TOOL_NAME = "DeepSolve"

_DESCRIPTION = (
    "Run the deep-solve verification-and-refinement pipeline (arXiv 2507.15855) "
    "on a hard, well-posed problem: iterative solve/verify/adjudicate/refine "
    "cycles with isolated child agents, ground-truth test execution when a "
    "test_command is supplied, and an honest accept/reject verdict "
    "(tests_passed / n_consecutive_clean / rejected). Heavyweight multi-child "
    "run — invoke on explicit user request or confirmation."
)

# Input schema mirrors the handler's argument surface
# (magi_agent/plugins/native/deep_solve.py::_run_deep_solve_live).
_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "problem": {
            "type": "string",
            "description": "Full problem statement to solve (verbatim).",
        },
        "test_command": {
            "type": "string",
            "description": (
                "Shell command that grades the candidate artifact "
                "(exit 0 = pass). Strongly recommended for executable "
                "problems — it drives ground-truth acceptance."
            ),
        },
        "tests": {
            "type": "string",
            "description": "Alias for test_command.",
        },
        "domain": {
            "type": "string",
            "description": (
                "Domain template: competitive_programming, math_proof, or "
                "general_analysis. Inferred when omitted."
            ),
        },
        "consecutive_clean_passes": {
            "type": "integer",
            "description": (
                "Consecutive clean verification rounds required to accept a "
                "proof/general problem (default 3)."
            ),
        },
        "language": {
            "type": "string",
            "description": "Implementation language for executable problems (default python3).",
        },
        "provider": {
            "type": "string",
            "description": "Optional provider override for stage children.",
        },
        "model": {
            "type": "string",
            "description": "Optional model override for stage children.",
        },
    },
    "required": ["problem"],
}


def provide_deep_solve(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name=DEEP_SOLVE_TOOL_NAME,
            description=_DESCRIPTION,
            kind="core",
            source=CORE_TOOL_SOURCE,
            permission="execute",
            input_schema=_INPUT_SCHEMA,
            # Long multi-child pipeline: generous wall-clock budget.
            timeout_ms=1_800_000,
            budget=Budget(max_calls_per_turn=2, max_parallel=1),
            dangerous=True,
            is_concurrency_safe=False,
            # Writes run-scoped artifacts under the workspace for test execution.
            mutates_workspace=True,
            parallel_safety="unsafe",
            cost_class="high",
            latency_class="background",
            available_in_modes=("act",),
            tags=("solve", "verify", "code", "execute", "subagent"),
            enabled_by_default=True,
            opt_out=True,
        )
    )


__all__ = ["DEEP_SOLVE_TOOL_NAME", "provide_deep_solve"]
