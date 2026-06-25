"""F-QA2 parametrized matrix — turn-boundary slot drivers.

Iterates every legal ``(kind, slot, action)`` combo from
:data:`magi_agent.customize.custom_rules._LEGAL` whose slot is in
:data:`tests.e2e.customize.matrix.F_QA2_SLOTS` —
``before_turn_start`` / ``after_turn_end`` /
``on_user_prompt_submit`` / ``on_subagent_stop``.

These slots all funnel through
:func:`magi_agent.runtime.governed_turn.run_governed_turn`, the
canonical CLI / serve / child entry point. The triggers drive a real
``run_governed_turn`` with a fake "poison-recording" engine so the
asserter can verify:

* GATE slots (``before_turn_start`` / ``on_user_prompt_submit``) actually
  short-circuit BEFORE ``rt.engine.run_turn_stream`` is invoked when a
  ``block``-action rule's criterion fails. The poison-recording engine
  notes every call to ``run_turn_stream`` so a silent "engine still ran
  even though we yielded the synthetic terminal" regression is caught.

* ``after_turn_end`` is audit-only per ``_LEGAL`` — block is excluded.
  The asserter verifies the turn completes normally and no synthetic
  policy-blocked terminal appears.

* ``on_subagent_stop`` honors F-LIFE1's authorability-lift
  (``{audit, block, ask_approval}``) but runtime parent-surfacing is
  NOT built yet (TODO per F-LIFE1 review pass). The asserter verifies
  the child engine ran and no synthetic policy-blocked terminal
  appears; it explicitly does NOT assert any parent-side block.

Each test follows the same 5-step pattern as F-QA1
(``tests/e2e/customize/test_matrix_tool_use.py``):

1. **Author**: persist the rule via :func:`set_custom_rule`.
2. **Trigger**: run the per-slot driver (drives ``run_governed_turn``).
3. **Verify**: ``assert_action_honored`` checks the gate / audit /
   parent-surfacing contract per ``_LEGAL``.
4. **Delete**: remove the rule via the cleanup closure.
5. **Cleanup**: the fixture cascade (``flags_on`` +
   ``customize_path_fixture``) restores env / storage state via
   ``monkeypatch`` so the next row starts clean.

Test ids are ``[kind-slot-action]`` so a failing row points directly at
the offending combo.

OFF-path (default-OFF byte-identical) regressions belong in per-slot
firing tests
(``tests/customize_firing/test_lifecycle_audit_governed_turn_wire.py``,
``tests/customize_firing/test_user_prompt_submit_firing.py``,
``tests/customize_firing/test_subagent_stop_firing.py``) —
F-QA2 verifies the matrix's ON-path contract.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from magi_agent.customize.store import set_custom_rule

from tests.e2e.customize.asserter import assert_action_honored
from tests.e2e.customize.matrix import (
    F_QA2_SLOTS,
    iter_legal_combinations_for_slots,
)
from tests.e2e.customize.payload_factory import build_payload, rule_id_for
from tests.e2e.customize.triggers import (
    trigger_after_turn_end,
    trigger_before_turn_start,
    trigger_on_subagent_stop,
    trigger_on_user_prompt_submit,
)


_COMBOS: list[tuple[str, str, str]] = sorted(
    iter_legal_combinations_for_slots(F_QA2_SLOTS)
)


def _combo_id(combo: tuple[str, str, str]) -> str:
    """``[kind-slot-action]`` test id — surfaces in pytest failures."""
    kind, slot, action = combo
    return f"{kind}-{slot}-{action}"


def _skip_if_optional_dep_missing(kind: str) -> None:
    """Skip when a kind's optional runtime dep is absent on this host."""
    if kind == "shacl_constraint":
        pytest.importorskip("rdflib")
        pytest.importorskip("pyshacl")
    if kind in {"shell_command", "shell_check"} and sys.platform.startswith(
        "win"
    ):
        pytest.skip("shell_runner honest-degrades on Windows")


def _configure_judge_for_action(
    patcher: Any, *, expected_action: str
) -> None:
    """Install the judge verdict that matches the matrix-declared action.

    GATE slots: action=block needs the criterion to FAIL (the gate's
    block path triggers on ``passed=False``). audit / ask_approval rules
    can pass — they only need the judge to be invoked.
    """
    if expected_action == "block":
        patcher.set_verdict(passed=False, reason="fqa2-matrix-row-block")
    elif expected_action == "ask_approval":
        # ask_approval is honest-degrade today — the audit ledger captures
        # the requires_approval directive. We use a FAILED verdict so the
        # gate path can detect the ask intent (matches the production
        # _gate_decision_from_audits "only failed verdicts contribute"
        # contract).
        patcher.set_verdict(passed=False, reason="fqa2-matrix-row-ask")
    else:
        # audit — the judge fires but does not block.
        patcher.set_verdict(passed=True, reason="fqa2-matrix-row-ok")


@pytest.mark.parametrize("combo", _COMBOS, ids=_combo_id)
def test_legal_turn_boundary_combo_honored(
    combo: tuple[str, str, str],
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
    active_turn_identity: tuple[str, str],
    cleanup_rule,
) -> None:
    """Each ``(kind, slot, action)`` turn-boundary row honors per ``_LEGAL``.

    Drives a real ``run_governed_turn`` with a poison-recording fake
    engine. The asserter routes the outcome through
    :func:`_assert_turn_boundary_honored` (registered in the asserter
    via :data:`_F_QA2_TURN_BOUNDARY_SLOTS`).
    """
    kind, slot, action = combo
    _skip_if_optional_dep_missing(kind)
    _configure_judge_for_action(patched_judge, expected_action=action)

    rule = build_payload(kind, slot, action)
    rid = rule_id_for(kind, slot, action)
    assert rule["id"] == rid  # stable id contract

    # 1. Author
    set_custom_rule(rule, path=customize_path_fixture)

    # The per-test session id keeps the audit ledger isolated from the
    # parallel matrix rows. Threading it into the trigger keeps the
    # poison-recording engine's recorded turn_input session attribution
    # readable.
    session_id, _ = active_turn_identity

    try:
        # 2. Trigger — route by slot.
        if slot == "before_turn_start":
            outcome = asyncio.run(
                trigger_before_turn_start(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                    session_id=session_id,
                )
            )
        elif slot == "after_turn_end":
            outcome = asyncio.run(
                trigger_after_turn_end(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                    session_id=session_id,
                )
            )
        elif slot == "on_user_prompt_submit":
            outcome = asyncio.run(
                trigger_on_user_prompt_submit(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                    session_id=session_id,
                )
            )
        elif slot == "on_subagent_stop":
            outcome = asyncio.run(
                trigger_on_subagent_stop(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                    session_id=session_id,
                )
            )
        else:  # pragma: no cover — F_QA2_SLOTS guards this branch
            pytest.fail(f"unexpected F-QA2 slot {slot!r}")

        # 3. Verify
        assert_action_honored(
            outcome,
            kind=kind,
            slot=slot,
            rule_id=rid,
            expected_action=action,
        )
    finally:
        # 4. Delete (fixture restores env / storage in teardown).
        cleanup_rule(rid)
