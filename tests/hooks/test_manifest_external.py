"""Tests for HookManifest external execution extension (PR 1).

Covers:
1. Existing handler manifests unchanged (no execution_type → defaults to "handler")
2. Command manifest with command set → valid
3. Command manifest without command → validation error
4. HTTP manifest with url set → valid
5. HTTP manifest without url → validation error
6. HTTP manifest with custom headers and method → valid
7. Handler manifest with command set → allowed (field is accepted, ignored at runtime)
8. Executor protocol compliance
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.executors import HookExecutor, ExecutionType, get_executor
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SOURCE = ToolSource(kind="builtin", package="magi_agent.hooks.tests")

_BASE = dict(
    name="test-hook",
    point=HookPoint.BEFORE_TURN_START,
    description="A test hook",
    source=_SOURCE,
)


# ---------------------------------------------------------------------------
# 1. Existing handler manifests still work unchanged
# ---------------------------------------------------------------------------

def test_handler_manifest_no_execution_type_defaults_to_handler():
    """Existing manifests without execution_type must still be valid and default to 'handler'."""
    m = HookManifest(**_BASE)
    assert m.execution_type == "handler"
    assert m.command is None
    assert m.url is None
    assert m.http_headers is None
    assert m.http_method == "POST"


def test_handler_manifest_explicit_execution_type():
    """Explicitly setting execution_type='handler' also works."""
    m = HookManifest(**_BASE, executionType="handler")
    assert m.execution_type == "handler"


# ---------------------------------------------------------------------------
# 2. Command manifest with command set → valid
# ---------------------------------------------------------------------------

def test_command_manifest_with_command_is_valid():
    m = HookManifest(**_BASE, executionType="command", command="/usr/local/bin/validate.sh")
    assert m.execution_type == "command"
    assert m.command == "/usr/local/bin/validate.sh"


# ---------------------------------------------------------------------------
# 3. Command manifest without command → validation error
# ---------------------------------------------------------------------------

def test_command_manifest_without_command_raises():
    with pytest.raises(ValidationError) as exc_info:
        HookManifest(**_BASE, executionType="command")
    errors = exc_info.value.errors()
    assert any("command" in str(e).lower() for e in errors)


# ---------------------------------------------------------------------------
# 4. HTTP manifest with url set → valid
# ---------------------------------------------------------------------------

def test_http_manifest_with_url_is_valid():
    m = HookManifest(**_BASE, executionType="http", url="https://example.com/hook")
    assert m.execution_type == "http"
    assert m.url == "https://example.com/hook"


# ---------------------------------------------------------------------------
# 5. HTTP manifest without url → validation error
# ---------------------------------------------------------------------------

def test_http_manifest_without_url_raises():
    with pytest.raises(ValidationError) as exc_info:
        HookManifest(**_BASE, executionType="http")
    errors = exc_info.value.errors()
    assert any("url" in str(e).lower() for e in errors)


# ---------------------------------------------------------------------------
# 6. HTTP manifest with custom headers and method → valid
# ---------------------------------------------------------------------------

def test_http_manifest_with_custom_headers_and_method():
    m = HookManifest(
        **_BASE,
        executionType="http",
        url="https://example.com/hook",
        httpHeaders={"Authorization": "Bearer token123", "X-Custom": "value"},
        httpMethod="PUT",
    )
    assert m.execution_type == "http"
    assert m.http_headers == {"Authorization": "Bearer token123", "X-Custom": "value"}
    assert m.http_method == "PUT"


# ---------------------------------------------------------------------------
# 7. Handler manifest with command set → allowed (ignored at runtime)
# ---------------------------------------------------------------------------

def test_handler_manifest_with_command_is_allowed():
    """command field on a handler manifest is accepted (ignored by the handler executor)."""
    m = HookManifest(**_BASE, executionType="handler", command="/some/script.sh")
    assert m.execution_type == "handler"
    assert m.command == "/some/script.sh"


# ---------------------------------------------------------------------------
# 8. Executor protocol compliance
# ---------------------------------------------------------------------------

class _ConcreteExecutor:
    """Minimal concrete executor used to validate the Protocol."""

    async def execute(self, context: HookContext, manifest: HookManifest) -> HookResult:
        return HookResult(action="continue")


def test_concrete_executor_satisfies_protocol():
    """A class with the correct signature must be recognised as a HookExecutor."""
    executor = _ConcreteExecutor()
    assert isinstance(executor, HookExecutor)


def test_get_executor_returns_registered_external_executors():
    """get_executor returns registered executors for built-in external execution types."""
    assert get_executor("handler") is None
    assert get_executor("command") is not None
    assert get_executor("http") is not None
    assert get_executor("llm") is not None


def test_get_executor_returns_none_for_unknown_type():
    """get_executor returns None for completely unknown types."""
    assert get_executor("unknown-type") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 9. Manifest is frozen (immutability)
# ---------------------------------------------------------------------------

def test_manifest_is_frozen():
    """HookManifest must remain immutable after construction."""
    m = HookManifest(**_BASE, executionType="command", command="/bin/check.sh")
    with pytest.raises(Exception):
        m.command = "/bin/other.sh"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. ExecutionType literal is correct
# ---------------------------------------------------------------------------

def test_execution_type_literal_values():
    """ExecutionType must accept exactly the three expected values."""
    for value in ("handler", "command", "http"):
        m = HookManifest(
            **_BASE,
            executionType=value,
            **({"command": "/x"} if value == "command" else {}),
            **({"url": "https://x.com"} if value == "http" else {}),
        )
        assert m.execution_type == value
