from magi_agent.config.env import browser_tool_enabled


def test_on_by_default_in_full_profile():
    assert browser_tool_enabled(env={}) is True


def test_off_when_set_false():
    assert browser_tool_enabled(env={"MAGI_BROWSER_TOOL_ENABLED": "false"}) is False


def test_on_when_set():
    assert browser_tool_enabled(env={"MAGI_BROWSER_TOOL_ENABLED": "true"}) is True


def test_kill_switch_overrides_enable():
    assert (
        browser_tool_enabled(
            env={
                "MAGI_BROWSER_TOOL_ENABLED": "1",
                "MAGI_BROWSER_TOOL_KILL_SWITCH": "1",
            }
        )
        is False
    )
