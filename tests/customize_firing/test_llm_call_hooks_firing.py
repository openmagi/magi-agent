"""PR-F-LIFE2 production-wire test: per-LLM-call audit fan-outs fire from
the ADK ``LifecycleLlmCallAuditControl`` plugin at the
``before_model_callback`` / ``after_model_callback`` boundary, capped by
the per-turn critic budget.

The unit-level fan-out signatures in
``magi_agent.customize.lifecycle_audit`` are exercised here through the
``LifecycleLlmCallAuditControl`` adapter so the wire (identity resolution,
budget decrement, OFF-path short-circuit, budget_exhausted skip record)
is locked alongside the audit invocation.

Three scenarios per slot:

* ON path with a matching rule under remaining budget → judge runs once.
* OFF path (master flag OFF) → judge MUST NOT run; the helper short-
  circuits before any policy load.
* Budget exhausted → the 4th invocation in a turn returns ONE
  ``status="budget_exhausted"`` skip record without invoking the judge.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.adk_bridge.lifecycle_llm_call_control import (
    DEFAULT_LLM_CALL_AUDIT_BUDGET,
    LLM_CALL_AUDIT_BUDGET_ENV,
    LifecycleLlmCallAuditControl,
    build_lifecycle_llm_call_control,
)
from magi_agent.customize.lifecycle_audit import (
    llm_call_hooks_enabled,
    run_after_llm_call_audit,
    run_before_llm_call_audit,
)
from magi_agent.customize.store import set_custom_rule


_BEFORE_RULE_ID = "cr_flife2_before_llm_call_audit"
_BEFORE_CRITERION = "the outbound prompt does not leak credentials"
_AFTER_RULE_ID = "cr_flife2_after_llm_call_audit"
_AFTER_CRITERION = "the model output does not echo internal tool envelopes"


def _before_rule() -> dict:
    return {
        "id": _BEFORE_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": _BEFORE_CRITERION},
        },
        "firesAt": "before_llm_call",
        "action": "audit",
    }


def _after_rule() -> dict:
    return {
        "id": _AFTER_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": _AFTER_CRITERION},
        },
        "firesAt": "after_llm_call",
        "action": "audit",
    }


def _flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # Sentinel non-None factory so the audit fan-out reaches the (mocked)
    # evaluate_criterion call instead of short-circuiting to
    # status="skipped" with reason "no critic model available".
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        lambda: object(),
    )
    return cfile


def _llm_request(text: str) -> SimpleNamespace:
    """Build a minimal ADK-shaped ``LlmRequest`` with one user-role chunk."""
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(role="user", parts=[part])
    return SimpleNamespace(contents=[content])


def _llm_response(text: str) -> SimpleNamespace:
    """Build a minimal ADK-shaped ``LlmResponse`` carrying ``text``."""
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(content=content)


def _callback_context(session_id: str, invocation_id: str) -> SimpleNamespace:
    session = SimpleNamespace(id=session_id, events=[])
    return SimpleNamespace(session=session, invocation_id=invocation_id)


# ---------------------------------------------------------------------------
# llm_call_hooks_enabled triple-gate
# ---------------------------------------------------------------------------


def test_llm_call_hooks_enabled_master_flag_off_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert llm_call_hooks_enabled() is False


def test_llm_call_hooks_enabled_full_stack_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert llm_call_hooks_enabled() is True


# ---------------------------------------------------------------------------
# build_lifecycle_llm_call_control gate
# ---------------------------------------------------------------------------


def test_build_returns_none_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", raising=False)
    assert build_lifecycle_llm_call_control() is None


def test_build_returns_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    control = build_lifecycle_llm_call_control()
    assert isinstance(control, LifecycleLlmCallAuditControl)


# ---------------------------------------------------------------------------
# Fan-out unit tests via run_before_llm_call_audit / run_after_llm_call_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_llm_call_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_before_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_before_llm_call_audit(
        prompt_text="hello model",
        model_factory=lambda: object(),
        critic_budget_remaining=DEFAULT_LLM_CALL_AUDIT_BUDGET,
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert len(calls) == 1
    assert calls[0]["criterion"] == _BEFORE_CRITERION
    assert calls[0]["draft_text"] == "hello model"


@pytest.mark.asyncio
async def test_after_llm_call_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_after_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_after_llm_call_audit(
        draft_text="model emitted some text",
        model_factory=lambda: object(),
        critic_budget_remaining=DEFAULT_LLM_CALL_AUDIT_BUDGET,
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert len(calls) == 1
    assert calls[0]["criterion"] == _AFTER_CRITERION
    assert calls[0]["draft_text"] == "model emitted some text"


@pytest.mark.asyncio
async def test_before_llm_call_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master flag OFF ⇒ fan-out is a no-op even with a matching rule.
    Locks the per-call zero-overhead OFF contract."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "0")
    set_custom_rule(_before_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_before_llm_call_audit(
        prompt_text="hello",
        model_factory=lambda: object(),
        critic_budget_remaining=DEFAULT_LLM_CALL_AUDIT_BUDGET,
    )
    assert audits == []


@pytest.mark.asyncio
async def test_before_llm_call_budget_exhausted_emits_skip_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """budget_remaining <= 0 ⇒ ONE status='budget_exhausted' record without
    invoking the critic. The ledger sees the cost-ceiling decision."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_before_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError(
            "critic must not run when the per-turn budget is exhausted"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_before_llm_call_audit(
        prompt_text="hello",
        model_factory=lambda: object(),
        critic_budget_remaining=0,
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "budget_exhausted"
    assert audits[0]["passed"] is True


@pytest.mark.asyncio
async def test_after_llm_call_budget_exhausted_emits_skip_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_after_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("critic must not run when budget is exhausted")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_after_llm_call_audit(
        draft_text="some output",
        model_factory=lambda: object(),
        critic_budget_remaining=0,
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "budget_exhausted"


# ---------------------------------------------------------------------------
# F-QA3 carry-over: intra-call budget — the fan-out must STOP invoking the
# critic the moment the remaining budget is consumed WITHIN a single call.
# Before this guard the fan-out only checked budget once at entry, so 1
# remaining + 3 enabled rules meant 3 critic invocations (the cap was
# silently violated; the caller decremented 3 from the shared counter
# afterward but the cost had already been paid).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_fan_out_intra_call_budget_caps_at_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 remaining + 3 enabled rules ⇒ exactly 1 critic invocation; the
    remaining 2 must surface as ``budget_exhausted`` skip records."""
    cfile = _flags_on(monkeypatch, tmp_path)
    for i in range(3):
        rule = _before_rule()
        rule["id"] = f"cr_before_intra_{i}"
        rule["what"]["payload"]["criterion"] = f"{_BEFORE_CRITERION} #{i}"
        set_custom_rule(rule, path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_before_llm_call_audit(
        prompt_text="hello",
        model_factory=lambda: object(),
        critic_budget_remaining=1,
    )

    evaluated = [a for a in audits if a.get("status") == "evaluated"]
    exhausted = [a for a in audits if a.get("status") == "budget_exhausted"]
    assert len(calls) == 1, (
        f"only 1 critic invocation allowed under budget=1; got {len(calls)}"
    )
    assert len(evaluated) == 1
    assert len(exhausted) == 2, (
        "the 2 over-budget rules must each surface as budget_exhausted skip "
        "records so the ledger captures the cost-ceiling decision per rule"
    )


@pytest.mark.asyncio
async def test_after_fan_out_intra_call_budget_caps_at_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric guard for ``after_llm_call``: 2 remaining + 4 rules ⇒
    2 critic invocations, 2 ``budget_exhausted`` skips."""
    cfile = _flags_on(monkeypatch, tmp_path)
    for i in range(4):
        rule = _after_rule()
        rule["id"] = f"cr_after_intra_{i}"
        rule["what"]["payload"]["criterion"] = f"{_AFTER_CRITERION} #{i}"
        set_custom_rule(rule, path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_after_llm_call_audit(
        draft_text="model output",
        model_factory=lambda: object(),
        critic_budget_remaining=2,
    )

    evaluated = [a for a in audits if a.get("status") == "evaluated"]
    exhausted = [a for a in audits if a.get("status") == "budget_exhausted"]
    assert len(calls) == 2, (
        f"only 2 critic invocations allowed under budget=2; got {len(calls)}"
    )
    assert len(evaluated) == 2
    assert len(exhausted) == 2


# ---------------------------------------------------------------------------
# ADK plugin wire — LifecycleLlmCallAuditControl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_invokes_critic_under_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plugin's on_before_model must invoke the criterion judge with
    the outbound prompt text extracted from llm_request."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_before_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    control = LifecycleLlmCallAuditControl()
    ctx = _callback_context("sess-1", "turn-1")
    await control.on_before_model(
        callback_context=ctx,
        llm_request=_llm_request("please answer this question"),
    )
    assert len(calls) == 1
    assert calls[0]["draft_text"] == "please answer this question"


@pytest.mark.asyncio
async def test_plugin_after_model_extracts_response_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_after_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    control = LifecycleLlmCallAuditControl()
    ctx = _callback_context("sess-1", "turn-1")
    await control.on_after_model(
        callback_context=ctx,
        llm_response=_llm_response("the answer is 42"),
    )
    assert len(calls) == 1
    assert calls[0]["draft_text"] == "the answer is 42"


@pytest.mark.asyncio
async def test_plugin_budget_exhausts_after_n_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 4th call within a single turn (budget=3) MUST emit the
    budget_exhausted skip record without invoking the critic."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_before_rule(), path=cfile)
    # Default budget is 3. Three successful invocations exhaust it; the
    # fourth call must short-circuit.
    monkeypatch.delenv(LLM_CALL_AUDIT_BUDGET_ENV, raising=False)

    call_count = {"n": 0}

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        call_count["n"] += 1
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    control = LifecycleLlmCallAuditControl()
    ctx = _callback_context("sess-1", "turn-1")
    req = _llm_request("question")

    for _ in range(DEFAULT_LLM_CALL_AUDIT_BUDGET):
        await control.on_before_model(callback_context=ctx, llm_request=req)
    # Budget should be exhausted now.
    assert call_count["n"] == DEFAULT_LLM_CALL_AUDIT_BUDGET

    # 4th call: budget=0, fan-out returns budget_exhausted record without
    # invoking the critic.
    await control.on_before_model(callback_context=ctx, llm_request=req)
    assert call_count["n"] == DEFAULT_LLM_CALL_AUDIT_BUDGET, (
        "critic must not be invoked when per-turn budget is exhausted"
    )


@pytest.mark.asyncio
async def test_plugin_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OFF-path silence: the plugin's hot path MUST short-circuit at the
    helper without invoking the critic when the master flag is OFF."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "0")
    set_custom_rule(_before_rule(), path=cfile)
    set_custom_rule(_after_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run with master flag OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    control = LifecycleLlmCallAuditControl()
    ctx = _callback_context("sess-1", "turn-1")
    # Both hot paths must short-circuit.
    await control.on_before_model(
        callback_context=ctx, llm_request=_llm_request("x")
    )
    await control.on_after_model(
        callback_context=ctx, llm_response=_llm_response("y")
    )


@pytest.mark.asyncio
async def test_plugin_budget_per_turn_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new (session, turn) tuple MUST reset the budget — the budget is
    per-LOGICAL-turn, not per-process."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_before_rule(), path=cfile)
    monkeypatch.delenv(LLM_CALL_AUDIT_BUDGET_ENV, raising=False)

    call_count = {"n": 0}

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        call_count["n"] += 1
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    control = LifecycleLlmCallAuditControl()
    req = _llm_request("q")
    # Exhaust budget for turn-1.
    for _ in range(DEFAULT_LLM_CALL_AUDIT_BUDGET + 1):
        await control.on_before_model(
            callback_context=_callback_context("sess-1", "turn-1"),
            llm_request=req,
        )
    after_turn_1 = call_count["n"]
    assert after_turn_1 == DEFAULT_LLM_CALL_AUDIT_BUDGET

    # New turn (turn-2) under the same session ⇒ budget resets.
    await control.on_before_model(
        callback_context=_callback_context("sess-1", "turn-2"),
        llm_request=req,
    )
    assert call_count["n"] == after_turn_1 + 1


# ---------------------------------------------------------------------------
# Production wire: control_plane:lifecycle-llm-call-audit@1 pack provider
#
# build_default_plane composes the F-LIFE2 control in the legacy/compat
# surface, but the live runner path (cli/real_runner.py +
# transport/gate5b_governance.py) goes through build_default_plugin →
# build_control_plane_from_packs which loads providers declared in
# pack.toml. Without the pack provider entry the operator-facing
# serve/REPL/child paths would silently never register the control. These
# tests pin the wiring so a future regression that drops the entry from
# pack.toml fails loudly.
# ---------------------------------------------------------------------------


def _has_lifecycle_llm_call_audit(controls) -> bool:
    return any(isinstance(c, LifecycleLlmCallAuditControl) for c in controls)


def test_pack_provider_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_default_plugin (the live runner path) MUST surface the
    LifecycleLlmCallAuditControl when MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED
    is ON — proving the pack.toml provider entry is wired into the pack
    loader (build_control_plane_from_packs), not just the legacy
    build_default_plane composition."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plugin = build_default_plugin(
        {
            "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_llm_call_audit(plugin._p._controls)


def test_pack_provider_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-OFF byte-identical contract: no-arg build_default_plugin()
    MUST NOT register the F-LIFE2 control. Locks the strict default-OFF
    semantics on the live runner path."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    monkeypatch.delenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", raising=False)
    plugin = build_default_plugin({})
    assert not _has_lifecycle_llm_call_audit(plugin._p._controls)


def test_pack_loader_registers_control_when_master_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_control_plane_from_packs (the inner pack-loader seam that
    build_default_plugin delegates to) MUST surface the
    LifecycleLlmCallAuditControl when the master flag is ON. Pinning the
    inner seam in addition to build_default_plugin catches a regression
    that bypasses the pack loader entirely."""
    from magi_agent.packs.registries import build_control_plane_from_packs

    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")

    plane = build_control_plane_from_packs(
        os_environ={
            "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert _has_lifecycle_llm_call_audit(plane._controls)
