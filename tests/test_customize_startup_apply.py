"""Tests for startup-apply of persisted tool overrides."""
from __future__ import annotations


def test_startup_applies_tool_overrides(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # build a runtime once to learn a real tool name
    from tests.test_customize_routes import _TOKEN, _build_runtime
    probe = _build_runtime(tmp_path, gateway_token=_TOKEN)
    tool_name = probe.tool_registry.list_all()[0].name

    # write an override disabling that tool
    from magi_agent.customize.store import set_tool_override
    set_tool_override(tool_name, False, cfile)

    # a freshly constructed runtime must come up with that tool disabled
    fresh = _build_runtime(tmp_path, gateway_token=_TOKEN)
    assert fresh.tool_registry.resolve_registration(tool_name).enabled is False


def test_startup_applies_verification_policy_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    from magi_agent.customize.store import set_verification_override

    set_verification_override("harness_presets", "coding-verification", False, mode="deterministic", path=cfile)

    from tests.test_customize_routes import _TOKEN, _build_runtime

    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    assert runtime.customize_verification_policy.explicit_preset("coding-verification") is False
    assert runtime.customize_verification_policy.resolve_enabled("coding-verification", default=True) is False


def test_startup_skips_verification_policy_when_flag_off(tmp_path, monkeypatch):
    # Profile-aware default-ON, so OFF is explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    from magi_agent.customize.store import set_verification_override

    set_verification_override("harness_presets", "answer_quality", True, path=cfile)

    from tests.test_customize_routes import _TOKEN, _build_runtime

    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    assert not hasattr(runtime, "customize_verification_policy")
