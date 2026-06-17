from __future__ import annotations

from magi_agent.cli.real_runner import _build_customize_after_tool_controls
from magi_agent.customize.after_tool_gate import CustomizeAfterToolControl


def test_no_controls_when_flags_off(monkeypatch):
    # Flags are profile-aware default-ON, so the OFF path is now explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    assert _build_customize_after_tool_controls() == []


def test_no_controls_when_only_verification_on(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    assert _build_customize_after_tool_controls() == []


def test_control_registered_when_flags_on(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # egress gate stays off → criterion model factory is None (deterministic-only).
    monkeypatch.delenv("MAGI_EGRESS_GATE_ENABLED", raising=False)
    controls = _build_customize_after_tool_controls()
    assert len(controls) == 1
    assert isinstance(controls[0], CustomizeAfterToolControl)
    assert controls[0]._model_factory is None
