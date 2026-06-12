"""First-party gate5b workspace tool handlers (no privilege, typed-view only).

Each provider receives ONLY the ToolProvideContext and binds a handler
``(args, WorkspaceHostView) -> output``. Bodies are MOVED verbatim from
``Gate5BFullToolHost._handle`` branches — behavior byte-identical (the C1.0
oracle proves it). A handler raising ValueError/OSError flows through the
unchanged dispatch error taxonomy.
"""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.packs.context import ToolProvideContext, WorkspaceHostView


def _clock(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    return {"nowMs": view.now_ms()}


def provide_clock(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("Clock", _clock)


def _calculation(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    # _evaluate_expression is a pure module-level stdlib-AST arithmetic helper;
    # importing it from the pack is library reuse, not privileged host access.
    from magi_agent.gates.gate5b_full_toolhost import _evaluate_expression

    return {"value": _evaluate_expression(str(args.get("expression", "0")))}


def provide_calculation(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("Calculation", _calculation)
