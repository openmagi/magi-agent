"""F-QA5 parametrized matrix — shell_command + shell_check kinds (real subprocess).

Iterates every legal ``(kind, slot, action)`` combo from
:data:`magi_agent.customize.custom_rules._LEGAL` whose:

* slot is in :data:`tests.e2e.customize.matrix.F_QA5_SHELL_SLOTS`, AND
* kind is in ``{"shell_command", "shell_check"}``.

Each row authors a real :class:`magi_agent.customize.shell_runner.ShellPayload`
(via :func:`tests.e2e.customize.payload_factory.build_payload`),
persists it through :func:`magi_agent.customize.store.set_custom_rule`,
then drives the matching ``run_shell_<kind>_at_<slot>`` helper from
:mod:`magi_agent.customize.lifecycle_audit`. The helpers spawn a
**real subprocess** via :func:`magi_agent.customize.shell_runner.run_shell_payload`
(``asyncio.subprocess.create_subprocess_exec`` ⇒ bash + ``-c <inline>``).

Safe scripts only — the payload factory authors:

* ``exit 0`` for shell_command audit rows,
* ``exit 1`` for shell_command block rows,
* ``echo '{"passed": true}'`` for shell_check audit rows,
* ``exit 1`` for shell_check block rows.

The asserter routes through
:func:`tests.e2e.customize.asserter.assert_shell_action_honored` so the
shell-specific evidence (audit ledger + derived gate verdict) is the
matrix's pass/fail signal regardless of which lifecycle slot the row
maps onto. F-QA1's audit/block branches already pin
``before_tool_use`` / ``after_tool_use`` / ``pre_final`` for shell
kinds through the facade; F-QA2's turn-boundary branches pin
``on_user_prompt_submit`` / ``after_turn_end`` / etc. via
``run_governed_turn``. F-QA5 complements those by pinning the helper
fan-outs DIRECTLY end-to-end including the subprocess spawn.

Cost envelope: ~$0 in API calls (every spawn is a trivial ``bash -c
'exit 0'`` or ``echo '{...}'``); subprocess startup is the only
material cost (~10-50ms × ~22 rows ⇒ ~1-2 wall seconds plus pytest
overhead).

OFF-path regressions belong in per-kind firing tests
(``tests/customize_firing/test_shell_command_firing.py`` /
``test_shell_check_firing.py``) — F-QA5 verifies the matrix's ON-path
contract per-slot.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from magi_agent.customize.store import set_custom_rule

from tests.e2e.customize.asserter import assert_shell_action_honored
from tests.e2e.customize.matrix import (
    F_QA5_SHELL_SLOTS,
    iter_legal_combinations_for_slots,
)
from tests.e2e.customize.payload_factory import build_payload, rule_id_for
from tests.e2e.customize.triggers import (
    trigger_shell_check_at,
    trigger_shell_command_at,
)


# Narrow the full F-QA5 slot enumeration to the two shell kinds — the
# slot set is a superset of F-QA1 / F-QA2 / F-QA4 slots so a plain
# iter_legal_combinations_for_slots without the kind filter would
# re-collect llm_criterion / tool_perm / etc. combos already covered by
# the earlier matrices.
_SHELL_KINDS: frozenset[str] = frozenset({"shell_command", "shell_check"})

_COMBOS: list[tuple[str, str, str]] = sorted(
    combo
    for combo in iter_legal_combinations_for_slots(F_QA5_SHELL_SLOTS)
    if combo[0] in _SHELL_KINDS
)


def _combo_id(combo: tuple[str, str, str]) -> str:
    """``[kind-slot-action]`` test id — surfaces in pytest failures."""
    kind, slot, action = combo
    return f"{kind}-{slot}-{action}"


def _skip_if_optional_dep_missing(kind: str) -> None:
    """shell_runner honest-degrades on Windows — skip both kinds there."""
    if kind in {"shell_command", "shell_check"} and sys.platform.startswith(
        "win"
    ):
        pytest.skip("shell_runner honest-degrades on Windows")


@pytest.mark.parametrize("combo", _COMBOS, ids=_combo_id)
def test_legal_shell_combo_honored(
    combo: tuple[str, str, str],
    customize_path_fixture: Path,
    flags_on: None,
    active_turn_identity: tuple[str, str],
    cleanup_rule,
) -> None:
    """Each ``(kind, slot, action)`` shell row triggers + honors per ``_LEGAL``.

    Steps:

    1. **Author** — persist the rule into the per-test ``customize.json``
       via :func:`set_custom_rule`. The payload factory produces a
       deterministic inline script per (kind, action).
    2. **Trigger** — route by kind to one of the two F-QA5 drivers.
       ``shell_command`` rows fan out to 1 of 9 helpers; ``shell_check``
       rows to 1 of 2 primary helpers (or honest-degrade for
       validator-only slots without a v1 runtime helper).
    3. **Verify** — :func:`assert_shell_action_honored` pins the audit
       ledger + derived gate verdict against the matrix-declared action.
    4. **Delete** — cleanup closure removes the rule (the fixture
       cascade restores env / storage / budget state on teardown).

    The :func:`active_turn_identity` fixture publishes a per-test
    ``(session_id, turn_id)`` so the shell helpers see a real budget
    counter (not the ``None`` no-cap path). This matches the production
    contract: the governed-turn wrapper publishes identity at top-of-
    turn before any helper fires.

    The :func:`patched_judge` fixture is intentionally NOT requested —
    no llm_criterion rule ever fires in this slice; the F-QA1 / F-QA2 /
    F-QA4 matrices already cover the llm_criterion-keyed rows for the
    same slots.
    """
    kind, slot, action = combo
    _skip_if_optional_dep_missing(kind)

    rule = build_payload(kind, slot, action)
    rid = rule_id_for(kind, slot, action)
    assert rule["id"] == rid  # stable id contract

    # 1. Author
    set_custom_rule(rule, path=customize_path_fixture)

    try:
        # 2. Trigger — route by kind. Slot dispatch happens inside the
        # driver (one of 9 shell_command helpers; one of 2 shell_check
        # helpers + honest-degrade fallback).
        if kind == "shell_command":
            outcome = asyncio.run(
                trigger_shell_command_at(
                    kind=kind,
                    rule=rule,
                    slot=slot,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        elif kind == "shell_check":
            outcome = asyncio.run(
                trigger_shell_check_at(
                    kind=kind,
                    rule=rule,
                    slot=slot,
                    rule_id=rid,
                    expected_action=action,
                )
            )
        else:  # pragma: no cover — _SHELL_KINDS guards this branch
            pytest.fail(f"unexpected F-QA5 kind {kind!r}")

        # 3. Verify
        assert_shell_action_honored(
            outcome,
            kind=kind,
            slot=slot,
            rule_id=rid,
            expected_action=action,
        )
    finally:
        # 4. Delete (fixture restores env / storage / budget on teardown)
        cleanup_rule(rid)
