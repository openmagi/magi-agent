from magi_agent.config.env import computer_tool_enabled


def test_disabled_by_default() -> None:
    assert computer_tool_enabled(env={}) is False


def test_enabled_flag_on() -> None:
    assert computer_tool_enabled(env={"MAGI_COMPUTER_TOOL_ENABLED": "true"}) is True


def test_kill_switch_overrides_enable() -> None:
    env = {"MAGI_COMPUTER_TOOL_ENABLED": "true", "MAGI_COMPUTER_TOOL_KILL_SWITCH": "1"}
    assert computer_tool_enabled(env=env) is False


def test_not_profile_default_on() -> None:
    # Even a "full"/"local" runtime profile must NOT enable computer-use implicitly.
    assert computer_tool_enabled(env={"MAGI_RUNTIME_PROFILE": "local-full"}) is False
