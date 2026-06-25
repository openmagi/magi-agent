"""PR-F-LIFE4b production-wire tests: three NEW task / session boundary
emitter slots fire from their respective runtime chokepoints when the
master flag is ON, and stay silent when it is OFF.

Slots covered:

* ``on_task_complete`` — fired by the
  :class:`_OnTaskCompleteCollector` inside
  :func:`magi_agent.runtime.governed_turn.run_governed_turn`'s
  ``finally`` block, only when the aggregated final assistant text
  carries a ``<task_done>`` marker (honest-degrade signal).
* ``on_session_start`` — fired by
  :class:`magi_agent.adk_bridge.lifecycle_session_control
  .LifecycleSessionControl` at the ADK ``on_before_model`` boundary,
  only on the FIRST model call per session (FIFO-bounded "seen"
  OrderedDict, cap 128).
* ``on_session_end`` — v1 honest-degrade: the audit helper exists
  and is wired into the policy → criterion-judge fan-out shape, but
  no transport-side emit wire ships in this PR. The test below
  verifies the validator + fan-out helper round-trip cleanly so the
  follow-up emit wire only needs to call the helper.

Three scenarios per slot (where wired):

* triple-gate ON + matching rule → judge runs.
* triple-gate OFF (master flag missing) → judge MUST NOT run.
* no matching rule → fan-out short-circuits (empty list).

Plus the on_session_start first-fire-per-session isolation: the second
model call in the same session MUST NOT re-fire the audit.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.adk_bridge.lifecycle_session_control import (
    LifecycleSessionControl,
    build_lifecycle_session_control,
)
from magi_agent.customize.lifecycle_audit import (
    run_session_end_audit,
    run_session_start_audit,
    run_task_complete_audit,
    session_task_emitters_enabled,
)
from magi_agent.customize.store import set_custom_rule


_TASK_COMPLETE_RULE_ID = "cr_flife4b_on_task_complete_audit"
_TASK_COMPLETE_CRITERION = "the final assistant message summarizes the task"
_SESSION_START_RULE_ID = "cr_flife4b_on_session_start_audit"
_SESSION_START_CRITERION = "the first prompt is not empty"
_SESSION_END_RULE_ID = "cr_flife4b_on_session_end_audit"
_SESSION_END_CRITERION = "the session summary records the outcome"


def _rule(*, rid: str, fires_at: str, criterion: str, action: str = "audit") -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": criterion}},
        "firesAt": fires_at,
        "action": action,
    }


def _flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "1"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


# ---------------------------------------------------------------------------
# session_task_emitters_enabled triple-gate
# ---------------------------------------------------------------------------


def test_session_task_emitters_enabled_master_flag_off_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", raising=False
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert session_task_emitters_enabled() is False


def test_session_task_emitters_enabled_full_stack_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "1"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert session_task_emitters_enabled() is True


def test_session_task_emitters_enabled_verification_off_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "1"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert session_task_emitters_enabled() is False


# ---------------------------------------------------------------------------
# on_task_complete fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_complete_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_TASK_COMPLETE_RULE_ID,
            fires_at="on_task_complete",
            criterion=_TASK_COMPLETE_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_task_complete_audit(
        final_text="Task done <task_done>; summary attached.",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert calls[0]["criterion"] == _TASK_COMPLETE_CRITERION
    assert "<task_done>" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_task_complete_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "0"
    )
    set_custom_rule(
        _rule(
            rid=_TASK_COMPLETE_RULE_ID,
            fires_at="on_task_complete",
            criterion=_TASK_COMPLETE_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_task_complete_audit(
        final_text="<task_done> anything",
        model_factory=lambda: object(),
    )
    assert audits == []


@pytest.mark.asyncio
async def test_task_complete_audit_empty_when_no_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_on(monkeypatch, tmp_path)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when no rule is authored")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_task_complete_audit(
        final_text="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# on_session_start fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_SESSION_START_RULE_ID,
            fires_at="on_session_start",
            criterion=_SESSION_START_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_session_start_audit(
        prompt_text="hello, write me a haiku",
        session_id="sess-abc-123",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    # The composed frame must include both the session id + prompt text
    # so the critic can disambiguate.
    assert "sess-abc-123" in calls[0]["draft_text"]
    assert "haiku" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_session_start_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "0"
    )
    set_custom_rule(
        _rule(
            rid=_SESSION_START_RULE_ID,
            fires_at="on_session_start",
            criterion=_SESSION_START_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_session_start_audit(
        prompt_text="anything",
        session_id="sess-1",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# on_session_end fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_end_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_SESSION_END_RULE_ID,
            fires_at="on_session_end",
            criterion=_SESSION_END_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_session_end_audit(
        summary_text="session closed cleanly; 5 turns processed",
        session_id="sess-xyz-9",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert "sess-xyz-9" in calls[0]["draft_text"]
    assert "5 turns processed" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_session_end_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", "0"
    )
    set_custom_rule(
        _rule(
            rid=_SESSION_END_RULE_ID,
            fires_at="on_session_end",
            criterion=_SESSION_END_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_session_end_audit(
        summary_text="anything",
        session_id="sess-1",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# LifecycleSessionControl: first-fire-per-session isolation
# ---------------------------------------------------------------------------


def _llm_request(text: str) -> SimpleNamespace:
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(role="user", parts=[part])
    return SimpleNamespace(contents=[content])


def _callback_context(session_id: str, invocation_id: str) -> SimpleNamespace:
    session = SimpleNamespace(id=session_id, events=[])
    return SimpleNamespace(session=session, invocation_id=invocation_id)


@pytest.mark.asyncio
async def test_lifecycle_session_control_fires_once_per_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second model call within the same session MUST NOT re-fire the
    on_session_start audit (the first-fire-per-session contract is the
    whole point of LifecycleSessionControl)."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_SESSION_START_RULE_ID,
            fires_at="on_session_start",
            criterion=_SESSION_START_CRITERION,
        ),
        path=cfile,
    )

    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_session_control._build_critic_factory",
        lambda: object(),
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    control = LifecycleSessionControl()

    # First model call in session sess-A → fires.
    await control.on_before_model(
        callback_context=_callback_context("sess-A", "inv-1"),
        llm_request=_llm_request("first prompt of session A"),
    )
    assert len(calls) == 1, "first model call must fire on_session_start"

    # Second model call in SAME session sess-A → MUST NOT re-fire.
    await control.on_before_model(
        callback_context=_callback_context("sess-A", "inv-2"),
        llm_request=_llm_request("second prompt of session A"),
    )
    assert len(calls) == 1, "second model call in same session must NOT re-fire"

    # First model call in DIFFERENT session sess-B → fires.
    await control.on_before_model(
        callback_context=_callback_context("sess-B", "inv-3"),
        llm_request=_llm_request("first prompt of session B"),
    )
    assert len(calls) == 2, "first model call in new session must fire"


@pytest.mark.asyncio
async def test_lifecycle_session_control_off_path_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF-path contract: when the master flag is OFF the control's
    on_before_model MUST NOT invoke the criterion judge."""
    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", raising=False
    )

    def fail_eval(*args, **kwargs):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    control = LifecycleSessionControl()
    await control.on_before_model(
        callback_context=_callback_context("sess-A", "inv-1"),
        llm_request=_llm_request("anything"),
    )


def test_build_lifecycle_session_control_returns_none_when_flag_off() -> None:
    """Default-OFF byte-identical contract — build helper returns None
    when the master flag is unset so the control plane never sees the
    control (mirror of build_lifecycle_llm_call_control)."""
    control = build_lifecycle_session_control({})
    assert control is None


def test_build_lifecycle_session_control_returns_instance_when_flag_on() -> None:
    control = build_lifecycle_session_control(
        {
            "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED": "1",
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED": "1",
        }
    )
    assert isinstance(control, LifecycleSessionControl)
