"""Tests for HttpHookExecutor (PR 3).

Covers:
1.  200 with valid JSON continue → action="continue"
2.  200 with stopReason → action="block"
3.  200 with updatedInput → action="replace"
4.  200 with permissionDecision → action="permission_decision"
5.  200 with additionalContext → action="continue" with metadata
6.  204 No Content → action="continue" (no-op)
7.  4xx fail-open → action="continue"
8.  4xx fail-closed → action="block"
9.  5xx fail-open → action="continue"
10. 5xx fail-closed → action="block"
11. Timeout fail-open → action="continue"
12. Timeout fail-closed → action="block"
13. Malformed JSON body (200) → action="continue"
14. Non-object JSON body (200, e.g. array) → action="continue"
15. Custom headers are sent to the server
16. Custom HTTP method (PUT) is used
17. Default Content-Type header is always sent
18. Operator headers override default Content-Type
19. Sanitized body — no secrets forwarded
20. Sanitized body — no filesystem paths forwarded
21. TLS verification enabled by default
22. TLS verification disabled via MAGI_HOOK_HTTP_VERIFY_TLS=false
23. 200 empty body → action="continue"
24. Registry integration: http executor is registered
25. Unexpected HTTP status (3xx) → fail-open continue
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors import get_executor
from openmagi_core_agent.hooks.executors.http_executor import (
    HttpHookExecutor,
    _parse_http_response_body,
    _tls_verify,
)
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.result import HookResult
from openmagi_core_agent.tools.manifest import ToolSource

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SOURCE = ToolSource(kind="builtin", package="test.fixtures")

_BASE_MANIFEST = dict(
    name="test-http-hook",
    point=HookPoint.BEFORE_TOOL_USE,
    description="Test http hook",
    source=_SOURCE,
    executionType="http",
    url="https://example.com/hook",
)

_CONTEXT = HookContext(
    botId="bot-http-test",
    sessionId="sess-abc",
    turnId="turn-001",
    channel="web",
)

_CONTEXT_NO_OPTIONAL = HookContext(botId="bot-minimal")


def _make_manifest(
    *,
    url: str = "https://example.com/hook",
    fail_open: bool = True,
    timeout_ms: int = 5_000,
    http_method: str = "POST",
    http_headers: dict[str, str] | None = None,
) -> HookManifest:
    kwargs: dict = {
        **_BASE_MANIFEST,
        "url": url,
        "failOpen": fail_open,
        "timeoutMs": timeout_ms,
        "httpMethod": http_method,
    }
    if http_headers is not None:
        kwargs["httpHeaders"] = http_headers
    return HookManifest(**kwargs)


def _mock_response(status_code: int, body: str = "") -> MagicMock:
    """Build a mock ``httpx.Response``-like object."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = body
    return resp


def _make_async_client_cm(response: MagicMock) -> MagicMock:
    """Wrap *response* in an async context manager that returns a client whose
    ``request`` coroutine returns *response*."""
    client_mock = AsyncMock()
    client_mock.request = AsyncMock(return_value=response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client_mock


# ---------------------------------------------------------------------------
# 1. 200 with valid JSON continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_continue_json():
    cm, client = _make_async_client_cm(_mock_response(200, json.dumps({"continue": True})))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 2. 200 with stopReason → block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_stop_reason_returns_block():
    body = json.dumps({"stopReason": "policy violation"})
    cm, client = _make_async_client_cm(_mock_response(200, body))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "block"
    assert result.reason == "policy violation"


# ---------------------------------------------------------------------------
# 3. 200 with updatedInput → replace
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_updated_input_returns_replace():
    body = json.dumps({"updatedInput": {"newKey": "newValue"}})
    cm, client = _make_async_client_cm(_mock_response(200, body))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "replace"
    assert result.value == {"newKey": "newValue"}


# ---------------------------------------------------------------------------
# 4. 200 with permissionDecision → permission_decision
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_permission_decision_approve():
    body = json.dumps({"permissionDecision": "approve"})
    cm, client = _make_async_client_cm(_mock_response(200, body))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "permission_decision"
    assert result.decision == "approve"


@pytest.mark.anyio
async def test_200_permission_decision_deny_with_reason():
    body = json.dumps({"permissionDecision": "deny", "reason": "not allowed"})
    cm, client = _make_async_client_cm(_mock_response(200, body))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "permission_decision"
    assert result.decision == "deny"
    assert result.reason == "not allowed"


# ---------------------------------------------------------------------------
# 5. 200 with additionalContext
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_additional_context_in_metadata():
    body = json.dumps({"additionalContext": "some extra info"})
    cm, client = _make_async_client_cm(_mock_response(200, body))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"
    assert result.metadata.get("additionalContext") == "some extra info"


# ---------------------------------------------------------------------------
# 6. 204 No Content → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_204_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(204, ""))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 7. 4xx fail-open → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_404_fail_open_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(404))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


@pytest.mark.anyio
async def test_400_fail_open_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(400))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 8. 4xx fail-closed → block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_401_fail_closed_returns_block():
    cm, client = _make_async_client_cm(_mock_response(401))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"
    assert result.reason is not None
    assert "401" in result.reason


@pytest.mark.anyio
async def test_403_fail_closed_returns_block():
    cm, client = _make_async_client_cm(_mock_response(403))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"


# ---------------------------------------------------------------------------
# 9. 5xx fail-open → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_500_fail_open_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(500))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


@pytest.mark.anyio
async def test_503_fail_open_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(503))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 10. 5xx fail-closed → block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_502_fail_closed_returns_block():
    cm, client = _make_async_client_cm(_mock_response(502))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"
    assert result.reason is not None
    assert "502" in result.reason


# ---------------------------------------------------------------------------
# 11. Timeout fail-open → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_fail_open_returns_continue():
    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 12. Timeout fail-closed → block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_fail_closed_returns_block():
    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"
    assert result.reason is not None
    assert "timed out" in result.reason.lower()


# ---------------------------------------------------------------------------
# 13. Malformed JSON body (200)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_malformed_json_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(200, "not-valid-json"))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 14. Non-object JSON body (200, e.g. array)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_json_array_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(200, json.dumps([1, 2, 3])))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 15. Custom headers are sent to the server
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_custom_headers_are_forwarded():
    custom_headers = {"X-Magi-Hook-Secret": "abc123", "X-Custom": "value"}
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest(http_headers=custom_headers))

    call_kwargs = client.request.call_args
    sent_headers: dict = call_kwargs.kwargs["headers"]
    assert sent_headers.get("X-Magi-Hook-Secret") == "abc123"
    assert sent_headers.get("X-Custom") == "value"


# ---------------------------------------------------------------------------
# 16. Custom HTTP method (PUT)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_custom_http_method_put():
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest(http_method="PUT"))

    call_kwargs = client.request.call_args
    assert call_kwargs.kwargs.get("method") == "PUT"


@pytest.mark.anyio
async def test_custom_http_method_patch():
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest(http_method="PATCH"))

    call_kwargs = client.request.call_args
    assert call_kwargs.kwargs.get("method") == "PATCH"


# ---------------------------------------------------------------------------
# 17. Default Content-Type header is always sent
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_default_content_type_header():
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest())

    call_kwargs = client.request.call_args
    sent_headers: dict = call_kwargs.kwargs.get("headers", {})
    assert sent_headers.get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# 18. Operator headers override default Content-Type
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_operator_headers_override_content_type():
    custom_headers = {"Content-Type": "application/json; charset=utf-8"}
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest(http_headers=custom_headers))

    call_kwargs = client.request.call_args
    sent_headers: dict = call_kwargs.kwargs.get("headers", {})
    assert sent_headers.get("Content-Type") == "application/json; charset=utf-8"


# ---------------------------------------------------------------------------
# 19. Sanitized body — no secrets forwarded
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sanitized_body_no_secrets():
    """The JSON payload must not contain API keys even if they appear in context fields."""
    # Inject a context where a field contains a secret-like string
    ctx_with_secret = HookContext(
        botId="bot-sk-secretSECRETsecret12345",
        sessionId="sess-Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    )
    captured_body: list[bytes] = []

    async def capture_request(*args, **kwargs):
        captured_body.append(kwargs.get("content", b""))
        return _mock_response(204)

    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=capture_request)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(ctx_with_secret, _make_manifest())

    body_str = captured_body[0].decode()
    assert "eyJhbGciOiJIUzI1NiJ9" not in body_str
    assert "sk-secretSECRETsecret12345" not in body_str
    assert "<redacted_secret>" in body_str


# ---------------------------------------------------------------------------
# 20. Sanitized body — no filesystem paths forwarded
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sanitized_body_no_paths():
    """Filesystem paths embedded in context fields must be redacted."""
    ctx_with_path = HookContext(
        botId="bot-1",
        channel="/Users/kevin/workspace/project",
    )
    captured_body: list[bytes] = []

    async def capture_request(*args, **kwargs):
        captured_body.append(kwargs.get("content", b""))
        return _mock_response(204)

    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=capture_request)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        await HttpHookExecutor().execute(ctx_with_path, _make_manifest())

    body_str = captured_body[0].decode()
    assert "kevin" not in body_str
    assert "<redacted_path>" in body_str


# ---------------------------------------------------------------------------
# 21. TLS verification enabled by default
# ---------------------------------------------------------------------------

def test_tls_verify_default(monkeypatch):
    monkeypatch.delenv("MAGI_HOOK_HTTP_VERIFY_TLS", raising=False)
    assert _tls_verify() is True


# ---------------------------------------------------------------------------
# 22. TLS verification disabled via env var
# ---------------------------------------------------------------------------

def test_tls_verify_disabled_env_false(monkeypatch):
    monkeypatch.setenv("MAGI_HOOK_HTTP_VERIFY_TLS", "false")
    assert _tls_verify() is False


def test_tls_verify_disabled_env_FALSE_uppercase(monkeypatch):
    monkeypatch.setenv("MAGI_HOOK_HTTP_VERIFY_TLS", "FALSE")
    assert _tls_verify() is False


def test_tls_verify_not_disabled_env_true(monkeypatch):
    monkeypatch.setenv("MAGI_HOOK_HTTP_VERIFY_TLS", "true")
    assert _tls_verify() is True


@pytest.mark.anyio
async def test_tls_verify_param_passed_to_client(monkeypatch):
    monkeypatch.setenv("MAGI_HOOK_HTTP_VERIFY_TLS", "false")
    cm, client = _make_async_client_cm(_mock_response(204))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm) as mock_cls:
        await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    call_kwargs = mock_cls.call_args
    assert call_kwargs.kwargs.get("verify") is False


# ---------------------------------------------------------------------------
# 23. 200 empty body → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_200_empty_body_returns_continue():
    cm, client = _make_async_client_cm(_mock_response(200, ""))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest())
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 24. Registry integration
# ---------------------------------------------------------------------------

def test_http_executor_registered():
    """Importing the module must register HttpHookExecutor in the global registry."""
    from openmagi_core_agent.hooks.executors.http_executor import HttpHookExecutor as _Exec
    executor = get_executor("http")
    assert executor is not None
    assert isinstance(executor, _Exec)


# ---------------------------------------------------------------------------
# 25. Unexpected HTTP status (3xx) → fail-open continue / fail-closed block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_redirect_3xx_fail_open_returns_continue():
    # httpx follows redirects by default but if it surfaces a 3xx to us, treat as error
    cm, client = _make_async_client_cm(_mock_response(302))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


@pytest.mark.anyio
async def test_redirect_3xx_fail_closed_returns_block():
    cm, client = _make_async_client_cm(_mock_response(302))
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"


# ---------------------------------------------------------------------------
# 26. _parse_http_response_body unit tests (pure function)
# ---------------------------------------------------------------------------

def test_parse_body_permission_ask():
    result = _parse_http_response_body(json.dumps({"permissionDecision": "ask"}), "hook")
    assert result.action == "permission_decision"
    assert result.decision == "ask"


def test_parse_body_unknown_permission_falls_through():
    result = _parse_http_response_body(json.dumps({"permissionDecision": "maybe"}), "hook")
    assert result.action == "continue"


def test_parse_body_updated_input_with_context():
    body = json.dumps({"updatedInput": {"x": 1}, "additionalContext": "ctx"})
    result = _parse_http_response_body(body, "hook")
    assert result.action == "replace"
    assert result.value == {"x": 1}
    assert result.metadata.get("additionalContext") == "ctx"


def test_parse_body_continue_false_deprecated():
    result = _parse_http_response_body(json.dumps({"continue": False}), "hook")
    assert result.action == "block"


def test_parse_body_whitespace_only_returns_continue():
    result = _parse_http_response_body("   \n\t  ", "hook")
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 27. Exception fallback (non-timeout network error)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_network_error_fail_open_returns_continue():
    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=httpx.NetworkError("connection refused"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=True))
    assert result.action == "continue"


@pytest.mark.anyio
async def test_network_error_fail_closed_returns_block():
    client_mock = AsyncMock()
    client_mock.request = AsyncMock(side_effect=httpx.NetworkError("connection refused"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("openmagi_core_agent.hooks.executors.http_executor.httpx.AsyncClient", return_value=cm):
        result = await HttpHookExecutor().execute(_CONTEXT, _make_manifest(fail_open=False))
    assert result.action == "block"
