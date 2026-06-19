"""A-9: gateway bearer-token authorization must be constant-time.

The chat / control-request / streaming routes compared the incoming
``Authorization: Bearer <token>`` header to the expected value with a raw
``!=`` / ``==`` string compare, which is a timing side-channel on the gateway
token. They must use :func:`hmac.compare_digest` via a single shared helper.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.transport.chat_shared import bearer_auth_failed


class _Headers:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def get(self, key: str, default: str = "") -> str:
        if key == "authorization" and self._value is not None:
            return self._value
        return default


class _Request:
    def __init__(self, authorization: str | None) -> None:
        self.headers = _Headers(authorization)


class _Config:
    def __init__(self, gateway_token: str) -> None:
        self.gateway_token = gateway_token


class _Runtime:
    def __init__(self, gateway_token: str) -> None:
        self.config = _Config(gateway_token)


def test_bearer_auth_accepts_correct_token() -> None:
    runtime = _Runtime("s3cret-token")
    request = _Request("Bearer s3cret-token")
    assert bearer_auth_failed(request, runtime) is False


def test_bearer_auth_rejects_wrong_token() -> None:
    runtime = _Runtime("s3cret-token")
    request = _Request("Bearer wrong-token")
    assert bearer_auth_failed(request, runtime) is True


def test_bearer_auth_rejects_missing_header() -> None:
    runtime = _Runtime("s3cret-token")
    request = _Request(None)
    assert bearer_auth_failed(request, runtime) is True


def test_bearer_auth_uses_constant_time_compare() -> None:
    """The helper body must call ``hmac.compare_digest`` (constant-time)."""
    import inspect

    source = inspect.getsource(bearer_auth_failed)
    assert "compare_digest" in source


def test_no_raw_bearer_compare_in_authed_routes() -> None:
    """Meta-guard: the migrated route modules must not re-introduce a raw
    ``!=``/``==`` compare against an expected Bearer string."""
    root = Path(__file__).resolve().parents[1] / "magi_agent" / "transport"
    targets = ["chat_routes.py", "control_requests.py"]
    for name in targets:
        text = (root / name).read_text(encoding="utf-8")
        assert "auth != expected" not in text, f"{name} still uses raw != compare"
        assert "!= expected" not in text, f"{name} still uses raw != compare"
