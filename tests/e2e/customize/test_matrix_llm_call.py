"""F-QA3 parametrized matrix — per-LLM-call slot drivers.

Iterates every legal ``(kind, slot, action)`` combo from
:data:`magi_agent.customize.custom_rules._LEGAL` whose slot is in
:data:`tests.e2e.customize.matrix.F_QA3_SLOTS` —
``before_llm_call`` / ``after_llm_call``.

These slots funnel through the ADK plugin
:class:`magi_agent.adk_bridge.lifecycle_llm_call_control.LifecycleLlmCallAuditControl`
at the ``before_model_callback`` / ``after_model_callback`` boundary.
v1 ``_LEGAL`` accepts ``llm_criterion`` only; F-LIFE4a lifted the
action set from audit-only to ``{audit, block}``:

* ``block`` at ``before_llm_call`` — the plugin returns the synthetic
  policy-blocked ``LlmResponse`` (built by
  ``_build_policy_blocked_llm_response``); the ADK callback dispatcher
  suppresses the outbound model call.
* ``block`` at ``after_llm_call`` — the plugin REPLACES the
  just-emitted response with the synthetic refusal so the downstream
  consumer never sees the offending text.
* ``audit`` — the plugin returns ``None`` (model call proceeds); the
  audit ledger captures the judge's verdict via the patched_judge
  sentinel.

Each test follows the same 5-step pattern as F-QA1 / F-QA2:

1. **Author**: persist the rule via :func:`set_custom_rule`.
2. **Trigger**: run the per-slot driver
   (:func:`trigger_before_llm_call` / :func:`trigger_after_llm_call`).
3. **Verify**: ``assert_action_honored`` checks the slot-specific
   contract via ``_assert_llm_call_honored`` in
   :mod:`tests.e2e.customize.asserter`.
4. **Delete**: remove the rule via the cleanup closure.
5. **Cleanup**: the fixture cascade (``flags_on`` +
   ``customize_path_fixture``) restores env / storage state via
   ``monkeypatch`` so the next row starts clean.

Test ids are ``[kind-slot-action]`` so a failing row points directly at
the offending combo.

OFF-path (default-OFF byte-identical) regressions belong in
``tests/customize_firing/test_llm_call_hooks_firing.py`` — F-QA3
verifies the matrix's ON-path contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from magi_agent.customize.store import set_custom_rule

from tests.e2e.customize.asserter import assert_action_honored
from tests.e2e.customize.matrix import (
    F_QA3_SLOTS,
    iter_legal_combinations_for_slots,
)
from tests.e2e.customize.payload_factory import build_payload, rule_id_for
from tests.e2e.customize.triggers import (
    trigger_after_llm_call,
    trigger_before_llm_call,
)


_COMBOS: list[tuple[str, str, str]] = sorted(
    iter_legal_combinations_for_slots(F_QA3_SLOTS)
)


def _combo_id(combo: tuple[str, str, str]) -> str:
    """``[kind-slot-action]`` test id — surfaces in pytest failures."""
    kind, slot, action = combo
    return f"{kind}-{slot}-{action}"


def _configure_judge_for_action(
    patcher: Any, *, expected_action: str
) -> None:
    """Install the judge verdict that matches the matrix-declared action.

    The gate path consults ``derive_gate_verdict_from_audits`` which
    blocks on ``passed=False``. ``audit`` rules can pass; the patched
    judge still records the invocation so a "did the rule fire?" sanity
    check is available off the fixture's ``calls`` list.
    """
    if expected_action == "block":
        patcher.set_verdict(passed=False, reason="fqa3-matrix-row-block")
    else:
        # audit — judge fires but does not block the gate.
        patcher.set_verdict(passed=True, reason="fqa3-matrix-row-ok")


@pytest.mark.parametrize("combo", _COMBOS, ids=_combo_id)
def test_legal_llm_call_combo_honored(
    combo: tuple[str, str, str],
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
    cleanup_rule,
) -> None:
    """Each ``(kind, slot, action)`` LLM-call row honors per ``_LEGAL``.

    Drives the ADK plugin directly with a synthetic callback_context +
    LlmRequest / LlmResponse stub. The asserter routes the outcome
    through :func:`_assert_llm_call_honored` (registered via
    :data:`_F_QA3_LLM_CALL_SLOTS`).
    """
    kind, slot, action = combo
    _configure_judge_for_action(patched_judge, expected_action=action)

    rule = build_payload(kind, slot, action)
    rid = rule_id_for(kind, slot, action)
    assert rule["id"] == rid  # stable id contract

    # 1. Author
    set_custom_rule(rule, path=customize_path_fixture)

    try:
        # 2. Trigger — route by slot.
        if slot == "before_llm_call":
            outcome = asyncio.run(
                trigger_before_llm_call(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        elif slot == "after_llm_call":
            outcome = asyncio.run(
                trigger_after_llm_call(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        else:  # pragma: no cover — F_QA3_SLOTS guards this branch
            pytest.fail(f"unexpected F-QA3 slot {slot!r}")

        # 3. Verify
        assert_action_honored(
            outcome,
            kind=kind,
            slot=slot,
            rule_id=rid,
            expected_action=action,
        )

        # Sanity: the patched judge fired at least once for both audit and
        # block (audit fires + records ``passed=True``; block fires +
        # records ``passed=False`` which the gate reducer translates into
        # a block verdict). This catches a regression that lets a rule
        # silently no-op (e.g. master flag mis-read, identity resolution
        # failure) — the assertion above would still pass because
        # verdict=proceed satisfies the audit branch.
        assert patched_judge.calls, (
            f"patched_judge expected at least one call for kind={kind!r} "
            f"slot={slot!r} action={action!r} rule_id={rid!r}; got zero "
            f"invocations — likely a wire regression (master flag / "
            f"identity / factory) that lets the rule no-op"
        )
    finally:
        # 4. Delete (fixture restores env / storage in teardown).
        cleanup_rule(rid)
