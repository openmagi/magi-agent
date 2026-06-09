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
