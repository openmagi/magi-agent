"""Import-parity guard for the PR-G1 user-pack gate pure extraction.

PR-G1 moves the two self-state-free user-pack gate helpers
(``_run_user_validators`` / ``_run_user_evidence_producers``) out of the
engine driver into a dedicated module while keeping the driver methods as thin
delegations. This test freezes:

* the new module is importable at both the canonical ``magi_agent.engine``
  path and the ``magi_agent.cli`` alias path, and both resolve to the same
  module object;
* the module-level ``run_user_validators`` / ``run_user_evidence_producers``
  functions exist and stay callable (flag OFF returns ``[]``);
* the driver still exposes ``_run_user_validators`` /
  ``_run_user_evidence_producers`` and routing through the driver method yields
  the identical result as calling the module function directly.
"""

from __future__ import annotations


def test_module_importable_at_both_paths_same_object() -> None:
    from magi_agent.cli import engine_user_packs as cli_mod
    from magi_agent.engine import engine_user_packs as engine_mod

    assert cli_mod is engine_mod


def test_gate_functions_importable_from_cli_path() -> None:
    from magi_agent.cli.engine_user_packs import (
        run_user_evidence_producers,
        run_user_validators,
    )

    assert callable(run_user_validators)
    assert callable(run_user_evidence_producers)


def test_driver_methods_still_present() -> None:
    from magi_agent.engine.driver import MagiEngineDriver

    assert hasattr(MagiEngineDriver, "_run_user_validators")
    assert hasattr(MagiEngineDriver, "_run_user_evidence_producers")


def test_driver_validator_method_delegates_to_module_function() -> None:
    from magi_agent.cli.engine_user_packs import run_user_validators
    from magi_agent.engine.driver import MagiEngineDriver

    observed_a: set[str] = set()
    observed_b: set[str] = set()
    via_driver = MagiEngineDriver._run_user_validators(
        object(),
        required_validators=("some.validator",),
        observed_public_refs=observed_a,
        session_id="s",
        turn_id="t",
        final_text="f",
    )
    via_module = run_user_validators(
        required_validators=("some.validator",),
        observed_public_refs=observed_b,
        session_id="s",
        turn_id="t",
        final_text="f",
    )
    assert via_driver == via_module
    assert observed_a == observed_b


def test_driver_evidence_method_delegates_to_module_function() -> None:
    from magi_agent.cli.engine_user_packs import run_user_evidence_producers
    from magi_agent.engine.driver import MagiEngineDriver

    observed_a: set[str] = set()
    observed_b: set[str] = set()
    via_driver = MagiEngineDriver._run_user_evidence_producers(
        object(),
        required_evidence=("some.evidence",),
        observed_public_refs=observed_a,
        session_id="s",
        turn_id="t",
    )
    via_module = run_user_evidence_producers(
        required_evidence=("some.evidence",),
        observed_public_refs=observed_b,
        session_id="s",
        turn_id="t",
    )
    assert via_driver == via_module
    assert observed_a == observed_b
