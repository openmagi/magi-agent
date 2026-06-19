from magi_agent.computer.autonomous.config import ComputerToolConfig, computer_tool_active


def test_defaults() -> None:
    cfg = ComputerToolConfig()
    assert cfg.enabled is False
    assert cfg.max_steps == 25


def test_frozen() -> None:
    cfg = ComputerToolConfig()
    try:
        cfg.max_steps = 99  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ComputerToolConfig must be frozen")


def test_active_delegates_to_env_gate() -> None:
    assert computer_tool_active(env={}) is False
    assert computer_tool_active(env={"MAGI_COMPUTER_TOOL_ENABLED": "true"}) is True
