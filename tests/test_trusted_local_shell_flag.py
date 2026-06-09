from magi_agent.config.env import parse_trusted_local_shell_enabled


def test_default_on():
    assert parse_trusted_local_shell_enabled({}) is True


def test_explicit_off():
    assert parse_trusted_local_shell_enabled({"MAGI_TRUSTED_LOCAL_SHELL_ENABLED": "0"}) is False
    assert parse_trusted_local_shell_enabled({"MAGI_TRUSTED_LOCAL_SHELL_ENABLED": "false"}) is False


def test_explicit_on():
    assert parse_trusted_local_shell_enabled({"MAGI_TRUSTED_LOCAL_SHELL_ENABLED": "1"}) is True
