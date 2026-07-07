"""Engine integration: the B9 no-tool finalizer forces a final answer.

When a tool-loop turn stops with no answer text, the driver runs ONE bounded
tool-less finalizer pass (re-invokes the runner with a finalizer message) so the
turn does not commit blank. Reuses the hermetic fake-adapter / fake-bridge shape
from ``test_engine_auto_continue.py`` (no real ADK / litellm import).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.empty_response_recovery import EmptyResponseRecoveryConfig
from magi_agent.runtime.no_tool_finalizer import NoToolFinalizerConfig

from tests.cli.test_engine_auto_continue import (
    FakeRunner,
    _ok_tool_end,
    _patch_lazy_deps,
    _run_drive,
)


def _delta(t: str) -> dict[str, Any]:
    # The driver reads the ``delta`` key (not ``text``) into emitted_text.
    return {"type": "text_delta", "delta": t}


def _payload_types(items: list[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        p = getattr(it, "payload", None)
        if isinstance(p, dict) and "type" in p:
            out.append(str(p["type"]))
    return out


def _answer_text(items: list[Any]) -> str:
    parts: list[str] = []
    for it in items:
        p = getattr(it, "payload", None)
        if isinstance(p, dict) and p.get("type") == "text_delta":
            d = p.get("delta")
            if isinstance(d, str):
                parts.append(d)
    return "".join(parts)


def test_blank_tool_turn_gets_finalizer_answer(monkeypatch):
    # Attempt 1: a failing tool, then STOP with no text. Finalizer pass: answer.
    runner = FakeRunner(
        events_per_call=[
            [_ok_tool_end(), {"type": "tool_end", "id": "call-x", "status": "error"}],
            [_delta("Here is what I found in the files.")],
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(
        runner=runner, user_id="cli", no_tool_finalizer=NoToolFinalizerConfig()
    )
    items = _run_drive(driver)

    assert len(runner.calls) == 2
    # The 2nd call is the finalizer message.
    assert "final" in runner.calls[1].new_message_text.lower()
    types = _payload_types(items)
    assert "no_tool_finalizer" in types
    # The answer text arrives after the finalizer start status.
    start_idx = types.index("no_tool_finalizer")
    assert "text_delta" in types[start_idx:]
    assert "Here is what I found" in _answer_text(items)


def test_no_finalizer_when_config_none(monkeypatch):
    # RED baseline: without the finalizer, the same script commits blank.
    runner = FakeRunner(
        events_per_call=[
            [_ok_tool_end(), {"type": "tool_end", "id": "call-x", "status": "error"}],
            [_delta("unreached")],
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_id="cli")  # no_tool_finalizer=None
    items = _run_drive(driver)

    assert len(runner.calls) == 1
    assert "no_tool_finalizer" not in _payload_types(items)
    assert _answer_text(items) == ""


def test_byte_identical_when_text_produced(monkeypatch):
    # A turn that DID answer must be unaffected by the finalizer (no extra call).
    def _make(cfg):
        r = FakeRunner(events_per_call=[[_ok_tool_end(), _delta("answer")]])
        _patch_lazy_deps(monkeypatch, r)
        d = MagiEngineDriver(runner=r, user_id="cli", no_tool_finalizer=cfg)
        return r, _run_drive(d)

    r_off, items_off = _make(None)
    r_on, items_on = _make(NoToolFinalizerConfig())
    assert len(r_off.calls) == 1 and len(r_on.calls) == 1
    assert _payload_types(items_off) == _payload_types(items_on)
    assert "no_tool_finalizer" not in _payload_types(items_on)


def test_at_most_one_pass_no_recursion(monkeypatch):
    # Finalizer itself also blank -> exactly 2 calls, producedText False, no 3rd.
    runner = FakeRunner(
        events_per_call=[
            [_ok_tool_end()],
            [],  # finalizer produces nothing
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(
        runner=runner, user_id="cli", no_tool_finalizer=NoToolFinalizerConfig()
    )
    items = _run_drive(driver)

    assert len(runner.calls) == 2
    end = [
        getattr(it, "payload", {})
        for it in items
        if isinstance(getattr(it, "payload", None), dict)
        and getattr(it, "payload").get("type") == "no_tool_finalizer"
        and getattr(it, "payload").get("phase") == "end"
    ]
    assert end and end[0].get("producedText") is False


def test_reasoning_only_blank_fires(monkeypatch):
    # No tools at all, empty first turn -> finalizer still fires (hosted parity).
    runner = FakeRunner(events_per_call=[[], [_delta("Final answer.")]])
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(
        runner=runner, user_id="cli", no_tool_finalizer=NoToolFinalizerConfig()
    )
    items = _run_drive(driver)
    assert len(runner.calls) == 2
    assert "Final answer." in _answer_text(items)


def test_deny_all_overlay_shape_and_restore():
    # Unit: the deny-all overlay returns a blocked dict and restores the callback.
    class _Agent:
        before_tool_callback = "ORIGINAL"

    class _Runner:
        agent = _Agent()

    class _Tool:
        name = "Bash"

    driver = MagiEngineDriver(runner=_Runner(), user_id="cli")
    attach = driver._attach_deny_all_tools(runner=driver._runner)
    assert attach is not None
    cb = driver._runner.agent.before_tool_callback[0]
    result = asyncio.run(cb(tool=_Tool(), args={}, tool_context=None))
    assert result["status"] == "blocked"
    assert result["error"] == "no_tool_finalizer_pass"
    driver._restore_gate_callback(attach)
    assert driver._runner.agent.before_tool_callback == "ORIGINAL"
