from magi_agent.config.env import parse_evidence_completion_gate_enabled
from magi_agent.cli.real_runner import _build_default_runner_policy_assembly


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


from magi_agent.runtime.local_defaults import apply_local_eval_runtime_defaults


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
