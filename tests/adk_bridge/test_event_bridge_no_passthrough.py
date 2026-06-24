"""D-10 — :class:`OpenMagiEventBridge` carries no pure pass-through method.

Seven instance methods used to repeat the same shape: re-declare a
module function's full kwarg signature, then ``return module_func(**same_kwargs)``.
REVIEW-A engine M1 (D-10) flagged the duplication: changing one
signature meant editing both copies. The fix moves the seven methods
to ``staticmethod`` aliases of the module functions so the kwarg
signatures only live once.

This module locks the post-fix invariant via an AST scan:

1. Every method on :class:`OpenMagiEventBridge` whose body is **solely**
   ``return <module_func>(**kwargs)`` (or a thin equivalent that just
   forwards every parameter) is forbidden — those belong as
   ``staticmethod`` aliases.
2. ``project_adk_event`` is exempt because it consumes ``self`` state
   (the per-turn ``_streamed_partial_text`` accumulator).
3. ``__init__`` is exempt for the obvious reason.

The seven pass-throughs ARE still callable as ``bridge.x(...)`` after
the refactor (the ``staticmethod`` assignment preserves the call form);
the existing behavioural suite under
``tests/test_adk_runner_lifecycle_events.py`` proves it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge


_EXEMPT_METHODS: frozenset[str] = frozenset(
    {
        "__init__",
        # ``project_adk_event`` consumes per-turn ``self._streamed_partial_text``
        # state, so it is a real method, not a pass-through. The D-10 plan
        # explicitly carves it out.
        "project_adk_event",
    }
)


def _bridge_class_node() -> ast.ClassDef:
    """Parse ``event_adapter.py`` and return the ``OpenMagiEventBridge``
    class node so we can inspect each method body without depending on
    runtime attribute lookup (which would already see the staticmethods
    bound at module-import time)."""

    source_path = Path(inspect.getsourcefile(OpenMagiEventBridge) or "")
    assert source_path.exists(), source_path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "OpenMagiEventBridge":
            return node
    raise AssertionError("OpenMagiEventBridge class not found in module source")


def _is_passthrough(method: ast.FunctionDef) -> bool:
    """Return True when the method body is solely ``return <name>(...)`` —
    a pure forwarder that should be a ``staticmethod`` alias instead.

    Conservative: a body containing anything OTHER than a single
    ``return Call(...)`` (or that ``return Call(...)`` does anything
    other than forward to a Name) is treated as "real work" and is
    NOT a pass-through.
    """

    body = method.body
    # Strip a leading docstring if present.
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1:
        return False
    stmt = body[0]
    if not isinstance(stmt, ast.Return):
        return False
    if stmt.value is None or not isinstance(stmt.value, ast.Call):
        return False
    return isinstance(stmt.value.func, ast.Name)


def test_event_bridge_carries_no_pure_passthrough_method() -> None:
    cls = _bridge_class_node()
    offenders: list[str] = []
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in _EXEMPT_METHODS:
            continue
        if _is_passthrough(node):
            offenders.append(node.name)
    assert offenders == [], (
        "OpenMagiEventBridge re-acquired a pure pass-through method. "
        "Route through a module function as a ``staticmethod`` alias "
        "instead. Offenders: " + ", ".join(offenders)
    )


def test_pass_through_aliases_still_callable_via_bridge() -> None:
    """The behavioural half: the seven aliases must still respond when
    called via ``bridge.x(...)``. We check ``__func__`` resolution since
    a ``staticmethod`` exposes the underlying callable that way."""

    pass_through_names = (
        "project_runner_start_event",
        "project_runner_phase_event",
        "project_runner_heartbeat_event",
        "project_runner_model_fallback_event",
        "project_runner_retry_event",
        "project_runner_llm_progress_event",
        "project_runner_end_event",
    )
    bridge = OpenMagiEventBridge()
    for name in pass_through_names:
        attr = getattr(bridge, name, None)
        assert callable(attr), f"OpenMagiEventBridge.{name} is not callable"
