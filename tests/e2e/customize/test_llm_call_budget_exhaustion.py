"""F-QA3 per-turn critic budget regression — caps llm_criterion invocations.

The per-LLM-call audit fan-out is hard-capped by
``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET`` (default ``3``). The budget is
maintained per-(session_id, turn_id) by
:class:`magi_agent.adk_bridge.lifecycle_llm_call_control.LifecycleLlmCallAuditControl`
and SHARED across ``on_before_model`` / ``on_after_model`` so a
single misbehaving rule cannot multiply critic cost without bound.

Two regression tests:

* :func:`test_per_turn_budget_caps_critic_invocations`. Author N+1
  rules at ``before_llm_call`` and call ``on_before_model`` N+1
  times. The cap holds at TWO layers (per the post-#1045
  :func:`run_before_llm_call_audit` intra-call guard):

  1. Intra-call. Within ONE plugin invocation the fan-out
     decrements a local budget per ``status in {evaluated, error}``
     audit. Authoring N+1 rules with ``remaining=N`` produces N
     ``evaluated`` records plus 1 ``budget_exhausted`` record; the
     judge runs N times in that single call (not N+1).
  2. Cross-call. The plugin decrements the shared
     ``state.remaining`` per ``evaluated`` audit returned by the
     fan-out, so once intra-call drained the cap the subsequent N
     ``on_before_model`` invocations each emit ONE
     ``budget_exhausted`` record at the fan-out's entry guard without
     touching the judge.

  Total judge invocations across the (N+1) plugin calls equal N
  (all from the first call).

* :func:`test_budget_shared_across_before_after` — author rules at
  BOTH ``before_llm_call`` AND ``after_llm_call``; the per-turn cap
  is the union (the plugin maintains one counter per (session, turn)
  that decrements regardless of which slot fired). 4 total before/
  after calls cap critic invocations at 3.

The default budget (3) is read from
:data:`LLM_CALL_AUDIT_BUDGET_ENV`. Tests delete the env var first so
they exercise the fail-open default path (``_parse_budget`` returns
``DEFAULT_LLM_CALL_AUDIT_BUDGET`` on missing / malformed value).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.adk_bridge.lifecycle_llm_call_control import (
    DEFAULT_LLM_CALL_AUDIT_BUDGET,
    LLM_CALL_AUDIT_BUDGET_ENV,
    LifecycleLlmCallAuditControl,
)
from magi_agent.customize.store import set_custom_rule


def _llm_request(text: str) -> SimpleNamespace:
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(role="user", parts=[part])
    return SimpleNamespace(contents=[content])


def _llm_response(text: str) -> SimpleNamespace:
    part = SimpleNamespace(text=text)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(content=content)


def _callback_context(session_id: str, invocation_id: str) -> SimpleNamespace:
    """Build a synthetic ADK callback_context matching the plugin's resolver shape."""
    session = SimpleNamespace(id=session_id, events=[])
    return SimpleNamespace(session=session, invocation_id=invocation_id)


def _author_llm_criterion_rule(
    *,
    rid: str,
    fires_at: str,
    path: Path,
    criterion: str | None = None,
) -> None:
    """Persist one llm_criterion rule at *fires_at* via the customize store."""
    rule = {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {
                "criterion": criterion
                or f"the response does not violate rule {rid}",
            },
        },
        "firesAt": fires_at,
        "action": "audit",
    }
    set_custom_rule(rule, path=path)


@pytest.mark.asyncio
async def test_per_turn_budget_caps_critic_invocations(
    monkeypatch: pytest.MonkeyPatch,
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
) -> None:
    """Budget=N ⇒ critic fires AT MOST N times per (session, turn).

    Scenario: author N+1 (N=3 default) llm_criterion rules at
    ``before_llm_call``. Use a single (session_id, turn_id). Call
    ``plugin.on_before_model`` (N+1) times.

    Two layers of cap (see module docstring):

    * Intra-call. The first plugin call's fan-out evaluates rules
      one-by-one and decrements a local budget per
      ``status in {evaluated, error}`` audit. With ``remaining=N``
      and N+1 rules authored, the judge fires exactly N times in this
      single call; the (N+1)-th rule observes ``budget <= 0`` and
      surfaces a ``status="budget_exhausted"`` skip record.
    * Cross-call. The plugin decrements the shared
      ``state.remaining`` per ``evaluated`` audit, so once the cap is
      drained the subsequent N plugin calls each emit ONE
      ``budget_exhausted`` record from the fan-out's entry guard
      WITHOUT touching the judge.

    Total recorded judge invocations across the (N+1) plugin calls
    equal N.
    """
    # Reset env so we use the default budget (3); _parse_budget snaps
    # malformed/missing to the default.
    monkeypatch.delenv(LLM_CALL_AUDIT_BUDGET_ENV, raising=False)
    cfile = customize_path_fixture
    n = DEFAULT_LLM_CALL_AUDIT_BUDGET  # 3
    # Author N+1 rules (each at before_llm_call). The rule count is
    # incidental — see docstring.
    for idx in range(n + 1):
        _author_llm_criterion_rule(
            rid=f"cr_fqa3_budget_blc_{idx}",
            fires_at="before_llm_call",
            path=cfile,
            criterion=f"binary-check-{idx}",
        )

    control = LifecycleLlmCallAuditControl()
    session_id = "sess_fqa3_budget"
    turn_id = "turn_fqa3_budget"
    ctx = _callback_context(session_id, turn_id)
    req = _llm_request("budget exhaustion probe")

    # Call (N+1) times — the (N+1)-th call MUST short-circuit to
    # budget_exhausted without invoking the judge again.
    for _ in range(n + 1):
        await control.on_before_model(
            callback_context=ctx, llm_request=req,
        )

    judge_calls = len(patched_judge.calls)
    # 1st call: budget=N, N+1 rules authored. The fan-out's
    # intra-call guard decrements after each evaluated audit; the
    # (N+1)-th rule observes budget==0 and emits a budget_exhausted
    # skip record without invoking the judge. So the first call
    # contributes EXACTLY N judge invocations (not N+1).
    # 2nd .. (N+1)-th calls: budget=0 at the fan-out's entry guard;
    # each emits ONE budget_exhausted record, judge NOT invoked.
    # Total judge invocations across the N+1 plugin calls = N.
    assert judge_calls == n, (
        f"cap held at TWO layers. intra-call: budget={n} plus {n+1} "
        f"rules yields {n} evaluated plus 1 budget_exhausted on the "
        f"first plugin call. cross-call: subsequent {n} calls (budget "
        f"exhausted) emit one budget_exhausted record each WITHOUT "
        f"invoking the judge. expected total judge_calls=={n}; "
        f"got {judge_calls}"
    )

    # Probe the per-turn state to confirm the budget hit zero (any
    # further call would emit a budget_exhausted record). Access the
    # private state map for a tight assertion — the plugin is
    # F-QA3-internal so coupling is acceptable here.
    state = control._turns.get((session_id, turn_id))
    assert state is not None, (
        f"expected per-turn budget state for "
        f"(session_id={session_id!r}, turn_id={turn_id!r}); "
        f"got None (identity resolution regression?)"
    )
    assert state.remaining == 0, (
        f"after {n+1} on_before_model calls with budget={n} the per-turn "
        f"counter MUST be exhausted (remaining=0); got "
        f"remaining={state.remaining}"
    )


@pytest.mark.asyncio
async def test_budget_shared_across_before_after(
    monkeypatch: pytest.MonkeyPatch,
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
) -> None:
    """Total critic invocations cap = MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET
    regardless of how the calls split across before/after.

    Scenario: author rules at BOTH ``before_llm_call`` AND
    ``after_llm_call``. Single (session, turn). Alternate calls so the
    shared budget is consumed from both slots. Assert the total judge
    invocation count is bounded by the per-turn cap.
    """
    monkeypatch.delenv(LLM_CALL_AUDIT_BUDGET_ENV, raising=False)
    cfile = customize_path_fixture

    _author_llm_criterion_rule(
        rid="cr_fqa3_budget_shared_before",
        fires_at="before_llm_call",
        path=cfile,
        criterion="before-shared-binary-check",
    )
    _author_llm_criterion_rule(
        rid="cr_fqa3_budget_shared_after",
        fires_at="after_llm_call",
        path=cfile,
        criterion="after-shared-binary-check",
    )

    control = LifecycleLlmCallAuditControl()
    session_id = "sess_fqa3_shared"
    turn_id = "turn_fqa3_shared"
    ctx = _callback_context(session_id, turn_id)
    req = _llm_request("question")
    resp = _llm_response("answer")

    # Alternate before/after across 6 total calls. The shared per-turn
    # counter is decremented after each successful audit (one per
    # plugin call here — single matching rule per slot). With budget=3,
    # exactly 3 calls reach the judge and the remaining 3 short-circuit
    # to budget_exhausted (no judge invocation).
    await control.on_before_model(callback_context=ctx, llm_request=req)
    await control.on_after_model(callback_context=ctx, llm_response=resp)
    await control.on_before_model(callback_context=ctx, llm_request=req)
    after_three = len(patched_judge.calls)
    # Three calls under budget=3 ⇒ judge invoked 3 times.
    assert after_three == DEFAULT_LLM_CALL_AUDIT_BUDGET, (
        f"shared budget after 3 alternating before/after calls expected "
        f"judge_calls=={DEFAULT_LLM_CALL_AUDIT_BUDGET}; "
        f"got {after_three} (cross-slot decrement regression?)"
    )

    # 3 more calls — budget is now exhausted; judge MUST NOT fire again.
    await control.on_after_model(callback_context=ctx, llm_response=resp)
    await control.on_before_model(callback_context=ctx, llm_request=req)
    await control.on_after_model(callback_context=ctx, llm_response=resp)
    after_six = len(patched_judge.calls)
    assert after_six == DEFAULT_LLM_CALL_AUDIT_BUDGET, (
        f"shared budget after 6 alternating before/after calls expected "
        f"judge_calls=={DEFAULT_LLM_CALL_AUDIT_BUDGET} (cap unchanged "
        f"once exhausted); got {after_six} — the shared budget did NOT "
        f"hold across before/after"
    )

    # The per-turn state MUST be exhausted regardless of which slot
    # drained it last.
    state = control._turns.get((session_id, turn_id))
    assert state is not None, (
        f"expected per-turn budget state for "
        f"(session_id={session_id!r}, turn_id={turn_id!r}); got None"
    )
    assert state.remaining == 0, (
        f"after 6 cross-slot calls the per-turn counter MUST be "
        f"exhausted (remaining=0); got remaining={state.remaining}"
    )
