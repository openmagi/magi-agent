"""Task 5 — operator selects the eval-gate decision rule via env (no default flip).

``eval_gate_config_from_env`` reads ``MAGI_LEARNING_GATE_*`` env vars into an
``EvalGateConfig``.  The hard invariant is *no silent default change*: with NO
env vars set the factory returns today's strict-band config (byte-identical to
``EvalGateConfig()``), and the executor honors an explicitly-passed config else
reads env.

Tests pass an explicit ``env`` dict — they NEVER mutate ``os.environ`` — so a
stray ``MAGI_LEARNING_GATE_*`` in the real environment cannot make these flaky.
"""
from __future__ import annotations

from magi_agent.learning.eval_gate import EvalGateConfig, eval_gate_config_from_env


# ---------------------------------------------------------------------------
# Default / unset → strict_band (no default flip)
# ---------------------------------------------------------------------------


def test_empty_env_is_strict_band_default() -> None:
    cfg = eval_gate_config_from_env(env={})
    assert cfg.decision_rule == "strict_band"
    assert cfg.z == 1.96
    assert cfg.n_repeats == 1
    assert cfg.max_repeats == 1
    # Byte-identical to today's no-config behavior.
    assert cfg == EvalGateConfig()


def test_unrelated_env_keys_ignored_stays_strict_band() -> None:
    cfg = eval_gate_config_from_env(env={"SOME_OTHER_VAR": "paired_significance"})
    assert cfg == EvalGateConfig()


# ---------------------------------------------------------------------------
# Rule selection
# ---------------------------------------------------------------------------


def test_rule_paired_significance_selected() -> None:
    cfg = eval_gate_config_from_env(
        env={"MAGI_LEARNING_GATE_RULE": "paired_significance"}
    )
    assert cfg.decision_rule == "paired_significance"


def test_rule_strict_band_selected_explicitly() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_RULE": "strict_band"})
    assert cfg.decision_rule == "strict_band"


def test_invalid_rule_falls_back_to_strict_band() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_RULE": "foo"})
    assert cfg.decision_rule == "strict_band"


def test_empty_string_rule_falls_back_to_strict_band() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_RULE": ""})
    assert cfg.decision_rule == "strict_band"


# ---------------------------------------------------------------------------
# Numeric parsing
# ---------------------------------------------------------------------------


def test_numeric_fields_parsed() -> None:
    cfg = eval_gate_config_from_env(
        env={
            "MAGI_LEARNING_GATE_RULE": "paired_significance",
            "MAGI_LEARNING_GATE_Z": "2.5",
            "MAGI_LEARNING_GATE_N_REPEATS": "3",
            "MAGI_LEARNING_GATE_MAX_REPEATS": "8",
        }
    )
    assert cfg.decision_rule == "paired_significance"
    assert cfg.z == 2.5
    assert cfg.n_repeats == 3
    assert cfg.max_repeats == 8


def test_malformed_z_falls_back_to_default() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_Z": "abc"})
    assert cfg.z == 1.96


def test_malformed_n_repeats_falls_back_to_default() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_N_REPEATS": "1.5"})
    assert cfg.n_repeats == 1


def test_malformed_max_repeats_falls_back_to_default() -> None:
    cfg = eval_gate_config_from_env(env={"MAGI_LEARNING_GATE_MAX_REPEATS": "xyz"})
    assert cfg.max_repeats == 1


def test_empty_numeric_string_falls_back_to_default() -> None:
    cfg = eval_gate_config_from_env(
        env={"MAGI_LEARNING_GATE_Z": "", "MAGI_LEARNING_GATE_N_REPEATS": ""}
    )
    assert cfg.z == 1.96
    assert cfg.n_repeats == 1


def test_env_mapping_is_not_mutated() -> None:
    env = {"MAGI_LEARNING_GATE_RULE": "paired_significance"}
    eval_gate_config_from_env(env=env)
    assert env == {"MAGI_LEARNING_GATE_RULE": "paired_significance"}
