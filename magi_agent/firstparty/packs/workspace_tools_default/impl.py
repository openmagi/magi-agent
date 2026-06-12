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


def _file_edit(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    """The MOVED Gate5BFullToolHost._handle FileEdit branch, re-expressed over
    WorkspaceHostView. Read-ledger enforcement, the fuzzy-match cascade,
    format-on-write and the EditMatch receipt hand-back are all kernel
    mechanisms consumed through the view — error strings and result keys are
    byte-identical to the legacy branch."""
    from magi_agent.config.env import edit_fuzzy_match_enabled

    target = view.resolve_path(str(args.get("path") or args.get("filePath") or ""))
    view.enforce_read_before_mutation(target)
    old_text = str(args.get("oldText", args.get("old_text", "")))
    new_text = str(args.get("newText", args.get("new_text", "")))
    if not old_text:
        raise ValueError("empty_old_text")
    current = target.read_text(encoding="utf-8", errors="replace")
    # Call-time read (NOT import-time): the import-time constant froze before
    # profile env defaults were applied — same bug class the legacy branch fixed.
    fuzzy_enabled = edit_fuzzy_match_enabled()
    match_result: object = None
    if fuzzy_enabled:
        from magi_agent.coding.edit_matching import (
            MultipleMatchesError,
            NoMatchError,
            replace as fuzzy_replace,
        )

        try:
            match_result = fuzzy_replace(current, old_text, new_text)
        except NoMatchError:
            raise ValueError("old_text_not_found")
        except MultipleMatchesError:
            raise ValueError("old_text_not_unique")
        # Hand the structured match back so dispatch() builds the EditMatch
        # evidence receipt after the handler returns (kernel mechanism).
        view.store_edit_match_result(match_result)
        target.write_text(match_result.result, encoding="utf-8")
    else:
        if old_text not in current:
            raise ValueError("old_text_not_found")
        target.write_text(current.replace(old_text, new_text, 1), encoding="utf-8")
    view.format_after_write(target)
    edit_result: dict[str, object] = {
        "pathDigest": view.path_digest(target),
        "replacements": 1,
    }
    if fuzzy_enabled and match_result is not None:
        from magi_agent.coding.edit_matching import EditMatchResult

        if isinstance(match_result, EditMatchResult):
            edit_result["matchTier"] = match_result.tier
            edit_result["matchConfidence"] = match_result.confidence
    if view.config.format_on_write_enabled:
        content_digest = view.content_digest(target)
        if content_digest is not None:
            edit_result["contentDigest"] = content_digest
    return edit_result


def provide_file_edit(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("FileEdit", _file_edit)
