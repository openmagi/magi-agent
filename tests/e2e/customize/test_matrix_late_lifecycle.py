"""F-QA4 parametrized matrix — late-lifecycle slot drivers.

Iterates every legal ``(kind, slot, action)`` combo from
:data:`magi_agent.customize.custom_rules._LEGAL` whose slot is in
:data:`tests.e2e.customize.matrix.F_QA4_SLOTS` — the late-lifecycle
emitters wired up across F-LIFE3 / F-LIFE4a / F-LIFE4b plus the F4
``spawn`` slot for ``capability_scope``:

* ``before_compaction`` / ``after_compaction`` →
  :meth:`magi_agent.adk_bridge.context_compaction.MagiContextCompactionPlugin._apply_tail_trim`.
* ``on_task_checkpoint`` →
  :meth:`magi_agent.missions.work_queue.driver.WorkQueueDriver.run_once`.
* ``on_artifact_created`` →
  :meth:`magi_agent.artifacts.file_delivery.FileDeliveryBoundary.execute`.
* ``on_task_complete`` →
  :class:`_OnTaskCompleteCollector` inside ``run_governed_turn``.
* ``on_session_start`` →
  :meth:`magi_agent.adk_bridge.lifecycle_session_control.LifecycleSessionControl.on_before_model`.
* ``spawn`` →
  :func:`magi_agent.customize.capability_scope.apply_capability_scope`.

``on_session_end`` is explicitly SKIPPED — F-LIFE4b ships no
transport-side emit wire in v1 (validator + helper round-trip only).
The rows still appear in the parametrize matrix (so a future PR that
lands the transport wire only needs to flip a skip marker) but are
auto-skipped via :func:`pytest.mark.skipif` keyed on the slot value.

Each test follows the same 5-step pattern as F-QA1 / F-QA2 / F-QA3:

1. **Author**: persist the rule via :func:`set_custom_rule` into the
   per-test ``customize.json`` (``customize_path_fixture``).
2. **Trigger**: run the per-slot driver from
   :mod:`tests.e2e.customize.triggers`.
3. **Verify**: ``assert_action_honored`` routes through
   ``_assert_late_lifecycle_honored`` (the F-QA4 branch in
   :mod:`tests.e2e.customize.asserter`).
4. **Delete**: remove the rule via the cleanup closure.
5. **Cleanup**: ``flags_on`` + ``customize_path_fixture`` restore env
   / storage state via ``monkeypatch`` so the next row starts clean.

Test ids are ``[kind-slot-action]`` so a failing row points directly
at the offending combo.

The matrix does NOT cover ``llm_criterion`` rows that require a live
LLM round-trip (none ship in F-QA4 — every row is judge-patched). The
``-k "not llm_criterion"`` invocation lets an operator narrow a smoke
run to the deterministic rows (capability_scope spawn + shell rules)
without spinning up the patched-judge fixture.

OFF-path (default-OFF byte-identical) regressions belong in
``tests/customize_firing/test_extra_emitters_firing.py`` /
``tests/customize_firing/test_session_task_emitters_firing.py`` —
F-QA4 verifies the matrix's ON-path contract.
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
    F_QA4_SLOTS,
    iter_legal_combinations_for_slots,
)
from tests.e2e.customize.payload_factory import build_payload, rule_id_for
from tests.e2e.customize.triggers import (
    ON_SESSION_END_SKIP_REASON,
    trigger_after_compaction,
    trigger_before_compaction,
    trigger_on_artifact_created,
    trigger_on_session_start,
    trigger_on_task_checkpoint,
    trigger_on_task_complete,
    trigger_spawn,
)


# Include on_session_end in the iteration (so it shows up in
# --collect-only output) but mark it for skip — the slot is
# wizard-authored only in v1 (no transport-side emit wire).
_F_QA4_SLOTS_WITH_SESSION_END: frozenset[str] = F_QA4_SLOTS | frozenset(
    {"on_session_end"}
)

_COMBOS: list[tuple[str, str, str]] = sorted(
    iter_legal_combinations_for_slots(_F_QA4_SLOTS_WITH_SESSION_END)
)


def _combo_id(combo: tuple[str, str, str]) -> str:
    """``[kind-slot-action]`` test id — surfaces in pytest failures."""
    kind, slot, action = combo
    return f"{kind}-{slot}-{action}"


def _skip_if_optional_dep_missing(kind: str) -> None:
    """Skip when a kind's optional runtime dep is absent on this host."""
    if kind in {"shell_command", "shell_check"} and sys.platform.startswith(
        "win"
    ):
        pytest.skip("shell_runner honest-degrades on Windows")


def _configure_judge_for_action(
    patcher: Any, *, expected_action: str
) -> None:
    """Install the judge verdict that matches the matrix-declared action.

    F-QA4 mirrors F-QA3 — block / ask_approval need a failed verdict
    so the audit fan-out's gate derivation sees a non-passing audit
    record; audit rules can pass.
    """
    if expected_action == "block":
        patcher.set_verdict(passed=False, reason="fqa4-matrix-row-block")
    elif expected_action == "ask_approval":
        # Honest-degrade today — the gate path derives ``"ask"`` from a
        # failed verdict in the audits (see
        # ``derive_gate_verdict_from_audits``). The verdict is recorded
        # in the audit ledger; runtime surfacing varies by slot.
        patcher.set_verdict(passed=False, reason="fqa4-matrix-row-ask")
    else:
        patcher.set_verdict(passed=True, reason="fqa4-matrix-row-ok")


@pytest.mark.parametrize("combo", _COMBOS, ids=_combo_id)
def test_legal_late_lifecycle_combo_honored(
    combo: tuple[str, str, str],
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
    cleanup_rule,
) -> None:
    """Each ``(kind, slot, action)`` late-lifecycle row honors per ``_LEGAL``.

    Routes by slot to the matching driver in
    :mod:`tests.e2e.customize.triggers`, then asserts via
    ``_assert_late_lifecycle_honored`` in
    :mod:`tests.e2e.customize.asserter`. ``on_session_end`` rows are
    skipped via the per-row guard below — F-LIFE4b ships no transport-
    side emit wire in v1 so there is nothing to drive.
    """
    kind, slot, action = combo
    # on_session_end honest-degrade — see docstring + triggers.py marker.
    if slot == "on_session_end":
        pytest.skip(ON_SESSION_END_SKIP_REASON)

    _skip_if_optional_dep_missing(kind)
    _configure_judge_for_action(patched_judge, expected_action=action)

    rule = build_payload(kind, slot, action)
    rid = rule_id_for(kind, slot, action)
    assert rule["id"] == rid  # stable id contract

    # 1. Author
    set_custom_rule(rule, path=customize_path_fixture)

    try:
        # 2. Trigger — route by slot.
        if slot == "before_compaction":
            outcome = asyncio.run(
                trigger_before_compaction(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "after_compaction":
            outcome = asyncio.run(
                trigger_after_compaction(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "on_task_checkpoint":
            outcome = asyncio.run(
                trigger_on_task_checkpoint(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "on_artifact_created":
            outcome = asyncio.run(
                trigger_on_artifact_created(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "on_task_complete":
            outcome = asyncio.run(
                trigger_on_task_complete(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "on_session_start":
            outcome = asyncio.run(
                trigger_on_session_start(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        elif slot == "spawn":
            outcome = asyncio.run(
                trigger_spawn(
                    kind=kind, rule_id=rid, expected_action=action
                )
            )
        else:  # pragma: no cover — F_QA4_SLOTS guards this branch
            pytest.fail(f"unexpected F-QA4 slot {slot!r}")

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
