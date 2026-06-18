from magi_agent.computer.autonomous.safety_hooks import is_sensitive_action


def test_typing_into_secure_field_is_sensitive() -> None:
    assert is_sensitive_action("type", "axsecuretextfield password") is True


def test_destructive_target_is_sensitive() -> None:
    assert is_sensitive_action("click", "move to trash") is True
    assert is_sensitive_action("click", "delete forever") is True


def test_payment_target_is_sensitive() -> None:
    assert is_sensitive_action("click", "complete payment") is True


def test_system_settings_target_is_sensitive() -> None:
    assert is_sensitive_action("click", "security & privacy") is True


def test_terminal_run_is_sensitive() -> None:
    assert is_sensitive_action("key", "terminal return") is True


def test_benign_click_is_not_sensitive() -> None:
    assert is_sensitive_action("click", "ok button") is False


def test_capture_is_never_sensitive() -> None:
    assert is_sensitive_action("capture", "security & privacy") is False
    assert is_sensitive_action("done", None) is False
