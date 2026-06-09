from magi_agent.browser.autonomous.safety_hooks import (
    navigation_block_reason,
    is_sensitive_url,
)


def test_blocks_loopback():
    assert navigation_block_reason("http://127.0.0.1/") is not None


def test_blocks_metadata():
    assert navigation_block_reason("http://169.254.169.254/latest/meta-data/") is not None


def test_allows_public_https():
    assert navigation_block_reason("https://example.com/pricing") is None


def test_sensitive_login_url():
    assert is_sensitive_url("https://example.com/login") is True


def test_sensitive_oauth_url():
    assert is_sensitive_url("https://example.com/oauth/authorize") is True


def test_non_sensitive_url():
    assert is_sensitive_url("https://example.com/pricing") is False
