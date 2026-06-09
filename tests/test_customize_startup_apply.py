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
