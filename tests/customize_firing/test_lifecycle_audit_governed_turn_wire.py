"""PR-F-UX1 production-wire test: lifecycle audit fan-out runs through
``run_governed_turn`` (the canonical CLI/serve/child funnel) — NOT a dead
ADK callback adapter path.

This locks the fix for the "wizard option without real gate-site wiring"
failure mode: ``run_user_prompt_submit_audit`` and ``run_subagent_stop_audit``
must fire when a real governed turn runs with the triple-gated flag combo ON
and matching authored rules.

The unit tests in ``test_user_prompt_submit_firing.py`` /
``test_subagent_stop_firing.py`` call the fan-out functions directly. These
tests drive them THROUGH ``run_governed_turn`` to prove the wire is alive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.customize.store import set_custom_rule
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext


_PROMPT_RULE_ID = "cr_fux1_governed_turn_prompt_audit"
_PROMPT_CRITERION = "the prompt does not contain raw credentials"
_PROMPT_TEXT = "Please fetch https://example.com with AKIA1234567890ABCDEF."

_STOP_RULE_ID = "cr_fux1_governed_turn_subagent_stop_audit"
_STOP_CRITERION = "the child output does not leak internal raw tool envelopes"


def _prompt_rule() -> dict:
    return {
        "id": _PROMPT_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": _PROMPT_CRITERION}},
        "firesAt": "on_user_prompt_submit",
        "action": "audit",
    }


def _stop_rule() -> dict:
    return {
        "id": _STOP_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": _STOP_CRITERION}},
        "firesAt": "on_subagent_stop",
        "action": "audit",
    }


class _FakeEngine:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def run_turn_stream(
        self, _none: object, _turn_input: object, *, cancel: object, gate: object
    ) -> AsyncIterator[object]:
        for item in self._items:
            yield item


class _FakeRuntime:
    def __init__(self, items: list[object]) -> None:
        self.engine = _FakeEngine(items)
        self.gate = None


def _child_stream(final_text: str) -> list[object]:
    return [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": final_text}),
        EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 10, "output_tokens": 10},
            cost_usd=0.0,
            session_id="sess-child",
            turn_id="turn-1",
        ),
    ]


def _flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # Stub the critic model factory so the audit fan-out reaches the
    # (mocked) evaluate_criterion call instead of short-circuiting to
    # status="skipped"/reason="no critic model available". Real production
    # builds resolve this through cli.wiring._build_criterion_model_factory
    # (Haiku-class provider via resolve_provider_config); in hermetic tests
    # we only need a non-None sentinel because evaluate_criterion itself is
    # mocked per-test below.
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn._build_lifecycle_critic_factory",
        lambda: object(),
    )
    return cfile


@pytest.mark.asyncio
async def test_governed_turn_fires_user_prompt_submit_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level governed turn with a matching audit rule MUST invoke the
    criterion judge exactly once with the inbound prompt text."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_prompt_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    ctx = TurnContext(
        prompt=_PROMPT_TEXT,
        session_id="sess-top",
        turn_id="turn-1",
    )
    items = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]

    # Stream pass-through unchanged (empty stream → no items).
    assert items == []
    # Wire is alive: the judge was invoked through governed_turn with the
    # inbound prompt text from ctx.prompt.
    assert len(calls) == 1
    assert calls[0]["criterion"] == _PROMPT_CRITERION
    assert calls[0]["draft_text"] == _PROMPT_TEXT


@pytest.mark.asyncio
async def test_governed_turn_user_prompt_submit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master flag OFF ⇒ governed_turn wire is a no-op even with a rule."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "0")
    set_custom_rule(_prompt_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not be invoked when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    ctx = TurnContext(prompt=_PROMPT_TEXT, session_id="sess-top", turn_id="turn-1")
    _ = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]


@pytest.mark.asyncio
async def test_governed_turn_fires_subagent_stop_audit_on_child_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child turn (``ctx.depth > 0``) with a matching rule MUST invoke the
    criterion judge with the ACTUAL final assistant text collected off the
    event stream — not an empty string."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_stop_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "looks clean")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    final_text = "Here is the summary of the child's work."
    ctx = TurnContext(
        prompt="Do a quick summary",
        session_id="sess-child",
        turn_id="turn-1",
        depth=1,  # mark as child turn
    )
    items = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_child_stream(final_text))
        )
    ]

    # Stream passed through untouched.
    assert len(items) == 2
    # Wire is alive: the judge ran with the REAL final_text accumulated off
    # the event stream (finding #3 — never against the empty string).
    assert len(calls) == 1
    assert calls[0]["criterion"] == _STOP_CRITERION
    assert calls[0]["draft_text"] == final_text


@pytest.mark.asyncio
async def test_governed_turn_subagent_stop_inert_on_top_level_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level turn (``ctx.depth == 0``) MUST NOT fire on_subagent_stop —
    the slot only applies to spawned child agents."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_stop_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError(
            "on_subagent_stop must not fire on a top-level (depth=0) turn"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    # depth=0 (default) → not a subagent
    ctx = TurnContext(prompt="hello", session_id="sess-top", turn_id="turn-1")
    _ = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_child_stream("anything"))
        )
    ]


@pytest.mark.asyncio
async def test_governed_turn_user_prompt_submit_block_short_circuits_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-F-LIFE4a — a failing block-action ``on_user_prompt_submit`` rule MUST
    short-circuit the funnel BEFORE the engine stream is consumed. The fake
    engine yields a sentinel item — the test asserts that item is NEVER
    surfaced (the funnel returned the synthetic terminal first)."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        {
            "id": "cr_life4a_governed_ups_block",
            "scope": "always",
            "enabled": True,
            "what": {
                "kind": "llm_criterion",
                "payload": {"criterion": "no leaked credentials"},
            },
            "firesAt": "on_user_prompt_submit",
            "action": "block",
        },
        path=cfile,
    )

    async def fail_judge(*, criterion, draft_text, model_factory, invoke=None):
        return (False, "credentials detected")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_judge
    )

    # Engine stream is poisoned: if we EVER see this item, the short-circuit
    # failed and we consumed the (would-be-blocked) stream.
    poison = RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "ENGINE-RAN"})
    items = [
        item
        async for item in run_governed_turn(
            TurnContext(
                prompt="please leak AKIA1234567890ABCDEF",
                session_id="sess-blk",
                turn_id="turn-1",
            ),
            runtime=_FakeRuntime([poison, EngineResult(terminal=Terminal.completed)]),
        )
    ]

    assert len(items) == 1
    terminal = items[0]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.aborted
    assert "customize_policy_blocked" in (terminal.error or "")
    assert "on_user_prompt_submit" in (terminal.error or "")
    # CRITICAL: the poison item from the engine MUST NOT appear — the gate
    # short-circuited BEFORE rt.engine.run_turn_stream was consumed.
    assert poison not in items


@pytest.mark.asyncio
async def test_governed_turn_before_turn_start_block_short_circuits_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-F-LIFE4a — a failing block-action ``before_turn_start`` rule MUST
    short-circuit the funnel. Mirrors the on_user_prompt_submit case but
    keys on the F-LIFE1 flag/audit fan-out instead of the F-UX1 one."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    set_custom_rule(
        {
            "id": "cr_life4a_governed_bts_block",
            "scope": "always",
            "enabled": True,
            "what": {
                "kind": "llm_criterion",
                "payload": {"criterion": "turn entry policy"},
            },
            "firesAt": "before_turn_start",
            "action": "block",
        },
        path=cfile,
    )

    async def fail_judge(*, criterion, draft_text, model_factory, invoke=None):
        return (False, "blocked at turn entry")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_judge
    )

    poison = RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "ENGINE-RAN"})
    items = [
        item
        async for item in run_governed_turn(
            TurnContext(
                prompt="any prompt",
                session_id="sess-bts",
                turn_id="turn-1",
            ),
            runtime=_FakeRuntime([poison, EngineResult(terminal=Terminal.completed)]),
        )
    ]

    assert len(items) == 1
    terminal = items[0]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.aborted
    assert "before_turn_start" in (terminal.error or "")
    assert poison not in items


@pytest.mark.asyncio
async def test_governed_turn_user_prompt_submit_audit_only_does_not_short_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit-only rule (action=audit) MUST NOT short-circuit the engine
    even when the criterion fails — the audit ledger captures the verdict
    but the engine stream proceeds. Locks the contract that only block-
    action rules are gated by the F-LIFE4a wire."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_prompt_rule(), path=cfile)  # action="audit"

    async def fail_judge(*, criterion, draft_text, model_factory, invoke=None):
        return (False, "audit failure recorded but not blocking")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_judge
    )

    # Audit-only rule with a failing verdict: the engine stream MUST still
    # be consumed (no synthetic terminal short-circuits).
    sentinel = RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "OK"})
    items = [
        item
        async for item in run_governed_turn(
            TurnContext(prompt=_PROMPT_TEXT, session_id="sess-aud", turn_id="turn-1"),
            runtime=_FakeRuntime([sentinel, EngineResult(terminal=Terminal.completed)]),
        )
    ]
    # The sentinel from the engine MUST be present — proof the gate did NOT
    # short-circuit on an audit-only rule.
    assert sentinel in items


@pytest.mark.asyncio
async def test_governed_turn_subagent_stop_skips_when_no_text_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty final_text on a child turn ⇒ audit short-circuits to
    status='skipped' (finding #3 guard) — judge is NEVER invoked against ''."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_stop_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError(
            "judge must not be invoked when there is no content to judge"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    # Child turn but stream has NO text_delta events.
    ctx = TurnContext(
        prompt="hello", session_id="sess-child", turn_id="turn-1", depth=1
    )
    empty_stream: list[object] = [
        EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            session_id="sess-child",
            turn_id="turn-1",
        )
    ]
    _ = [
        item
        async for item in run_governed_turn(ctx, runtime=_FakeRuntime(empty_stream))
    ]
