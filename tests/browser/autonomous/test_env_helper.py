from magi_agent.config.env import browser_tool_enabled


def test_off_by_default():
    assert browser_tool_enabled(env={}) is False


def test_on_when_set():
    assert browser_tool_enabled(env={"MAGI_BROWSER_TOOL_ENABLED": "true"}) is True
