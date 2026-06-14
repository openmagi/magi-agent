from magi_agent.cli.app import resolve_headless_permission_mode
from magi_agent.cli.real_runner import _build_default_runner_policy_assembly
from magi_agent.config.env import (
    general_automation_live_enabled,
    parse_evidence_completion_gate_enabled,
)
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)


def test_gate_default_on():
    assert parse_evidence_completion_gate_enabled({}) is True


def test_gate_explicit_off():
    assert parse_evidence_completion_gate_enabled({"MAGI_EVIDENCE_COMPLETION_GATE_ENABLED": "0"}) is False
    assert parse_evidence_completion_gate_enabled({"MAGI_EVIDENCE_COMPLETION_GATE_ENABLED": "false"}) is False
    assert parse_evidence_completion_gate_enabled({"MAGI_EVIDENCE_COMPLETION_GATE_ENABLED": "off"}) is False


def test_assembly_none_when_gate_off(monkeypatch):
    monkeypatch.setenv("MAGI_EVIDENCE_COMPLETION_GATE_ENABLED", "0")
    result = _build_default_runner_policy_assembly(
        model_provider="anthropic",
        model_label="anthropic/claude-sonnet-4-6",
        live_policy_callback_attached=False,
    )
    assert result is None


def test_eval_defaults_disable_delivery_machinery_keep_coding():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_eval_runtime_defaults(env)
    for k in (
        "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED", "MAGI_GA_LIVE_ENABLED",
        "MAGI_CODING_REPAIR_LOOP_ENABLED", "MAGI_SELF_REVIEW_ENABLED",
        "MAGI_AUTOPILOT", "MAGI_SESSION_PERSISTENCE_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED", "MAGI_SKILL_CURATOR_ENABLED",
    ):
        assert env[k] in {"0", "false"}, k
    assert env["MAGI_LEARNING_ENABLED"] in {"0", "false"}
    for k in (
        "MAGI_FIRST_PARTY_TOOLS_ENABLED", "MAGI_EDIT_FUZZY_MATCH_ENABLED",
        "MAGI_READ_LEDGER_ENABLED", "MAGI_RIPGREP_ENABLED",
        "MAGI_APPLY_PATCH_ENABLED", "MAGI_TRUSTED_LOCAL_SHELL_ENABLED",
    ):
        assert env[k] == "1", k
    assert env["MAGI_TASK_TYPES"] == "coding"


def test_eval_defaults_respect_explicit_override():
    env = {"MAGI_RUNTIME_PROFILE": "eval", "MAGI_SELF_REVIEW_ENABLED": "1"}
    apply_local_eval_runtime_defaults(env)
    assert env["MAGI_SELF_REVIEW_ENABLED"] == "1"  # setdefault must not override


def test_eval_profile_alone_is_not_full_runtime_profile():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    assert parse_evidence_completion_gate_enabled(env) is False
    assert general_automation_live_enabled(env) is False


def test_eval_profile_does_not_activate_local_full_defaults():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_full_runtime_defaults(env)
    assert env == {"MAGI_RUNTIME_PROFILE": "eval"}


def test_eval_default_flag_resolves_to_bypass():
    assert resolve_headless_permission_mode(
        permission_mode="default", flag_is_default=True, runtime_profile="eval"
    ) == "bypassPermissions"


def test_eval_default_flag_normalizes_runtime_profile():
    assert resolve_headless_permission_mode(
        permission_mode="default", flag_is_default=True, runtime_profile=" EVAL "
    ) == "bypassPermissions"


def test_eval_explicit_flag_wins():
    assert resolve_headless_permission_mode(
        permission_mode="acceptEdits", flag_is_default=False, runtime_profile="eval"
    ) == "acceptEdits"


def test_non_eval_default_flag_resolves_to_bypass_permissions():
    # Default installed CLI should be yolo/bypass for local tool use. Operators
    # who want prompts can still pass an explicit --permission-mode default.
    assert resolve_headless_permission_mode(
        permission_mode="default", flag_is_default=True, runtime_profile=None
    ) == "bypassPermissions"


def test_full_profile_default_flag_resolves_to_bypass_permissions():
    assert resolve_headless_permission_mode(
        permission_mode="default", flag_is_default=True, runtime_profile="full"
    ) == "bypassPermissions"


def test_explicit_default_mode_is_respected():
    # The operator explicitly asked for strict default mode — keep it.
    assert resolve_headless_permission_mode(
        permission_mode="default", flag_is_default=False, runtime_profile=None
    ) == "default"
