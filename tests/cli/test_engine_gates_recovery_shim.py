"""Same-object re-export guard for the PR-G3 gate-stack and recovery extraction,
plus the N-34 lock test for the renamed cleanup-error suppression CM.

PR-G3 pure-moves the pre-final gate stack into ``engine.engine_gates`` and the
recovery/continuation helpers into ``engine.engine_recovery``. The driver
re-imports every moved name (and keeps a ``_suppress_cancel`` back-compat
alias), so existing import paths and object identity are preserved. N-34 renames
``_suppress_cancel`` to ``_suppress_cleanup_errors`` and adds an honest docstring
plus a debug log when a non-cancel exception is swallowed; the suppression scope
is behavior-preserved.
"""

from __future__ import annotations

import asyncio
import importlib
import logging

import pytest

GATE_SYMBOLS = [
    "_CODING_TASK_TYPES",
    "_NON_CODING_TASK_TYPES",
    "_pre_final_gate_applies",
    "_build_coding_repair_decision_payload",
    "_latest_coding_test_evidence",
    "_evidence_mapping",
    "_is_coding_test_evidence",
    "_string_values",
    "_evidence_observed_at",
    "_coding_repair_loop_enabled",
    "_document_coverage_blocks",
    "_is_research_recipe_scope",
    "_resolve_document_coverage_mode_with_preset",
    "_run_shacl_rules_for_turn",
    "_load_shacl_policy_if_enabled",
    "_coding_repair_max_attempts",
    "_build_repair_continuation_message",
    "_build_pre_final_verifier_bus_payload",
    "_extract_task_types",
    "_normalize_task_type",
]

RECOVERY_SYMBOLS = [
    "EngineRecoveryPolicy",
    "build_engine_recovery_policy",
    "build_output_continuation_config",
    "build_empty_response_recovery_config",
    "should_reprompt_for_zero_edits",
    "_is_continuation_output_event",
]


@pytest.mark.parametrize("name", GATE_SYMBOLS)
def test_driver_reexports_gate_symbol(name: str) -> None:
    driver = importlib.import_module("magi_agent.engine.driver")
    gates = importlib.import_module("magi_agent.engine.engine_gates")
    assert getattr(driver, name) is getattr(gates, name)


@pytest.mark.parametrize("name", RECOVERY_SYMBOLS)
def test_driver_reexports_recovery_symbol(name: str) -> None:
    driver = importlib.import_module("magi_agent.engine.driver")
    recovery = importlib.import_module("magi_agent.engine.engine_recovery")
    assert getattr(driver, name) is getattr(recovery, name)


def test_legacy_cli_engine_paths_still_work() -> None:
    from magi_agent.cli.engine import EngineRecoveryPolicy as via_cli
    from magi_agent.engine.engine_recovery import EngineRecoveryPolicy as via_new

    assert via_cli is via_new


def test_suppress_cancel_back_compat_alias() -> None:
    driver = importlib.import_module("magi_agent.engine.driver")
    recovery = importlib.import_module("magi_agent.engine.engine_recovery")
    assert driver._suppress_cancel is recovery._suppress_cleanup_errors
    assert driver._suppress_cleanup_errors is recovery._suppress_cleanup_errors


# --- N-34 lock test: suppression scope is behavior-preserved -----------------


def test_n34_cancelled_error_is_suppressed() -> None:
    from magi_agent.engine.engine_recovery import _suppress_cleanup_errors

    with _suppress_cleanup_errors():
        raise asyncio.CancelledError()
    # Reaching here means the CancelledError was swallowed.


def test_n34_plain_exception_is_suppressed_and_debug_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from magi_agent.engine.engine_recovery import _suppress_cleanup_errors

    with caplog.at_level(logging.DEBUG, logger="magi_agent.engine.engine_recovery"):
        with _suppress_cleanup_errors():
            raise ValueError("boom during cleanup")
    debug_records = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "suppressed cleanup exception" in r.message
    ]
    assert len(debug_records) == 1


def test_n34_keyboard_interrupt_propagates() -> None:
    from magi_agent.engine.engine_recovery import _suppress_cleanup_errors

    with pytest.raises(KeyboardInterrupt):
        with _suppress_cleanup_errors():
            raise KeyboardInterrupt()
