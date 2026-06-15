from magi_agent.browser.autonomous.config import (
    BrowserToolConfig,
    browser_tool_active,
)


def test_active_by_default_in_full_profile():
    assert browser_tool_active(env={}) is True


def test_disabled_by_explicit_off():
    assert browser_tool_active(env={"MAGI_BROWSER_TOOL_ENABLED": "0"}) is False


def test_enabled_via_env():
    assert browser_tool_active(env={"MAGI_BROWSER_TOOL_ENABLED": "1"}) is True


def test_kill_switch_overrides_enable():
    env = {"MAGI_BROWSER_TOOL_ENABLED": "1", "MAGI_BROWSER_TOOL_KILL_SWITCH": "1"}
    assert browser_tool_active(env=env) is False


def test_config_defaults_are_off():
    cfg = BrowserToolConfig()
    assert cfg.enabled is False
    assert cfg.production_network_enabled is False
