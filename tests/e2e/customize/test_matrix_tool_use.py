"""F-QA1 parametrized matrix — pre_final / before_tool_use / after_tool_use.

Iterates every legal ``(kind, slot, action)`` combo from
:data:`magi_agent.customize.custom_rules._LEGAL` whose slot is in
:data:`tests.e2e.customize.matrix.F_QA1_SLOTS`.

Each test follows the 5-step pattern from the F-QA design doc:

1. **Author**: persist the rule via :func:`set_custom_rule` with the
   per-test ``customize.json``.
2. **Trigger**: run the per-slot driver that fires the matching
   runtime chokepoint.
3. **Verify**: ``assert_action_honored`` checks the verdict against the
   matrix-declared action contract.
4. **Delete**: remove the rule via the cleanup closure.
5. **Cleanup**: the fixture cascade (``flags_on`` + ``customize_path_fixture``
   + ``shell_budget_reset``) restores env / storage / budget state via
   ``monkeypatch`` so the next row starts clean.

Test ids are ``[kind-slot-action]`` so a failing row points directly at
the offending combo.

OFF-path (default-OFF byte-identical) regressions belong in per-kind
firing tests (``tests/customize_firing/``) — F-QA1 verifies the
matrix's ON-path contract.
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
    F_QA1_SLOTS,
    iter_legal_combinations_for_slots,
)
from tests.e2e.customize.payload_factory import build_payload, rule_id_for
from tests.e2e.customize.triggers import (
    trigger_after_tool_use,
    trigger_before_tool_use,
    trigger_pre_final,
)


_COMBOS: list[tuple[str, str, str]] = sorted(
    iter_legal_combinations_for_slots(F_QA1_SLOTS)
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
    """Install the judge verdict that matches the matrix-declared action."""
    if expected_action in {"block", "retry"}:
        patcher.set_verdict(passed=False, reason="matrix-row-block")
    elif expected_action == "override":
        patcher.set_verdict(passed=False, reason="matrix-row-override")
    else:
        # audit / ask_approval — gate evaluates but does not block.
        patcher.set_verdict(passed=True, reason="matrix-row-ok")


@pytest.mark.parametrize("combo", _COMBOS, ids=_combo_id)
def test_legal_combo_honored(
    combo: tuple[str, str, str],
    customize_path_fixture: Path,
    flags_on: None,
    patched_judge: Any,
    active_turn_identity: tuple[str, str],
    cleanup_rule,
) -> None:
    """Each ``(kind, slot, action)`` matrix row triggers + honors per ``_LEGAL``."""
    kind, slot, action = combo
    _skip_if_optional_dep_missing(kind)
    _configure_judge_for_action(patched_judge, expected_action=action)

    rule = build_payload(kind, slot, action)
    rid = rule_id_for(kind, slot, action)
    assert rule["id"] == rid  # stable id contract

    # 1. Author
    set_custom_rule(rule, path=customize_path_fixture)

    try:
        # 2. Trigger — route by slot.
        if slot == "pre_final":
            outcome = asyncio.run(
                trigger_pre_final(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        elif slot == "before_tool_use":
            outcome = asyncio.run(
                trigger_before_tool_use(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        elif slot == "after_tool_use":
            outcome = asyncio.run(
                trigger_after_tool_use(
                    kind=kind,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        else:  # pragma: no cover — F_QA1_SLOTS guards this branch
            pytest.fail(f"unexpected F-QA1 slot {slot!r}")

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
