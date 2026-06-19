"""TurnContext-shape golden (A-8 / P0.2).

``TurnContext`` is the single value object every governed surface threads
(CLI ``_drive``, serve, child). A-8 adds a THIRD distinct authority field
``permission_mode`` alongside the pre-existing ``permission_cap`` (tool
allowlist cap) and ``memory_mode``. They compose and are orthogonal — they
must NOT be collapsed.

This golden pins the frozen dataclass field set + defaults so that no later
surface adapter (P3.3 D-2/D-5, P3.5 D-7, P5.7) and no refactor can silently
drop ``permission_mode`` once it is threaded.
"""

from __future__ import annotations

import dataclasses

from magi_agent.runtime.turn_context import TurnContext


def _field_defaults() -> dict[str, object]:
    out: dict[str, object] = {}
    for f in dataclasses.fields(TurnContext):
        if f.default is not dataclasses.MISSING:
            out[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory()  # type: ignore[misc]
        else:
            out[f.name] = "<required>"
    return out


def test_turn_context_field_set_is_exactly_the_golden() -> None:
    names = tuple(f.name for f in dataclasses.fields(TurnContext))
    assert names == (
        "prompt",
        "session_id",
        "turn_id",
        "recipe",
        "permission_cap",
        "memory_mode",
        "permission_mode",
        "provider",
        "model",
        "depth",
        "budget_ms",
        "initial_messages",
    )


def test_three_authority_fields_are_distinct_and_present() -> None:
    names = {f.name for f in dataclasses.fields(TurnContext)}
    # The three distinct authority knobs must all exist and not be collapsed.
    assert {"permission_cap", "memory_mode", "permission_mode"} <= names


def test_authority_defaults_are_least_privilege() -> None:
    defaults = _field_defaults()
    # permission_cap defaults to None (no cap set yet -> caller decides);
    # memory_mode defaults to "normal"; permission_mode defaults to the
    # deny/ask "default" mode, NOT bypassPermissions.
    assert defaults["permission_cap"] is None
    assert defaults["memory_mode"] == "normal"
    assert defaults["permission_mode"] == "default"


def test_permission_mode_composes_with_permission_cap() -> None:
    ctx = TurnContext(
        prompt="p",
        session_id="s",
        turn_id="t",
        permission_mode="default",
        permission_cap=frozenset({"Read", "Grep"}),
    )
    # Orthogonal: a capped turn can still be in ask/default enforcement mode.
    assert ctx.permission_mode == "default"
    assert ctx.permission_cap == frozenset({"Read", "Grep"})


def test_to_turn_input_shape_is_byte_identical() -> None:
    ctx = TurnContext(prompt="p", session_id="s", turn_id="t")
    ti = ctx.to_turn_input()
    # permission_mode must NOT leak into the turn-input dict shape.
    assert set(ti) == {"prompt", "session_id", "turn_id", "harness_state"}
