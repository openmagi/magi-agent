"""``_build_runtime`` must thread ``ctx.permission_mode`` (A-8 / P0.2).

The single governed-turn primitive's fallback runtime builder
(``runtime/governed_turn.py:_build_runtime``) hard-coded
``permission_mode="bypassPermissions"`` — the most permissive mode — for every
serve/child turn that did not pre-build a runtime. The fail-closed fix threads
``ctx.permission_mode`` (which defaults to ``"default"`` = ask/no-preapproval).
Bypass is only used when a caller EXPLICITLY sets it on the TurnContext.
"""

from __future__ import annotations

from unittest import mock

from magi_agent.runtime.governed_turn import _build_runtime
from magi_agent.runtime.turn_context import TurnContext


def test_build_runtime_defaults_to_ask_not_bypass() -> None:
    ctx = TurnContext(prompt="p", session_id="s", turn_id="t")
    with mock.patch(
        "magi_agent.cli.wiring.build_headless_runtime"
    ) as build:
        _build_runtime(ctx)
    build.assert_called_once()
    kwargs = build.call_args.kwargs
    assert kwargs["permission_mode"] == "default"
    assert kwargs["permission_mode"] != "bypassPermissions"


def test_build_runtime_threads_explicit_bypass_opt_in() -> None:
    ctx = TurnContext(
        prompt="p",
        session_id="s",
        turn_id="t",
        permission_mode="bypassPermissions",
    )
    with mock.patch(
        "magi_agent.cli.wiring.build_headless_runtime"
    ) as build:
        _build_runtime(ctx)
    assert build.call_args.kwargs["permission_mode"] == "bypassPermissions"


def test_build_runtime_threads_accept_edits() -> None:
    ctx = TurnContext(
        prompt="p",
        session_id="s",
        turn_id="t",
        permission_mode="acceptEdits",
    )
    with mock.patch(
        "magi_agent.cli.wiring.build_headless_runtime"
    ) as build:
        _build_runtime(ctx)
    assert build.call_args.kwargs["permission_mode"] == "acceptEdits"
