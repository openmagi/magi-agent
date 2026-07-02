"""Parity tests for the secret-key classifier + grammar single home (B3, N-03).

Covers the union of the three evidence _is_secret_key forks (ledger / reports /
tool_boundary) into ops.safety.is_secret_key, the SECRET_KEY_NAME grammar union,
and the adk_bridge _SECRET_KEY_VALUE_RE rebase. Assertions are non-weakening:
keys that classified True on main stay True, and the directional leaks the review
called out are now closed.

Secret-shaped fixtures are assembled from fragments at runtime.
"""

from __future__ import annotations

import pytest

from magi_agent.evidence import ledger, reports, tool_boundary
from magi_agent.memory import adk_bridge
from magi_agent.ops import safety


def test_classifier_constants_are_kernel_objects() -> None:
    assert ledger._SECRET_FIELD_FRAGMENTS is safety.SECRET_KEY_FRAGMENTS
    assert reports._SECRET_FIELD_FRAGMENTS is safety.SECRET_KEY_FRAGMENTS
    assert ledger._PUBLIC_SUMMARY_SECRET_FIELD_NAMES is safety.PUBLIC_CREDENTIAL_KEY_NAMES


# Directional cases the review specified (each fails on pristine main).
def test_directional_leaks_now_closed() -> None:
    assert "[redacted]" in ledger._redact_public_summary_text("session_key=abc123def")
    assert tool_boundary._is_secret_key("passphrase") is True
    assert reports._is_secret_key("aws_credentials") is True


# Every fragment/name recognized on main must still classify True (non-weakening).
_LEDGER_REPORTS_TRUE_KEYS = [
    "api_key",
    "apiKey",
    "auth_token",
    "bearer_token",
    "client_secret",
    "id_token",
    "password",
    "passphrase",
    "private_key",
    "refresh_token",
    "secret",
    "service_role_key",
    "session_token",
    "token",
]
_PUBLIC_CREDENTIAL_KEYS = [
    "authorization",
    "proxy_authorization",
    "proxyauthorization",
    "cookie",
    "set_cookie",
    "setcookie",
    "credential",
    "credentials",
]
_TOOL_BOUNDARY_TRUE_KEYS = [
    "authorization",
    "cookie",
    "apikey",
    "api_key",
    "secret",
    "token",
    "password",
    "privatekey",
    "private_key",
    "servicekey",
    "service_key",
    "service_role_key",
    "credential",
    "credential_id",
    "credentials",
    "key",
]


@pytest.mark.parametrize("key", _LEDGER_REPORTS_TRUE_KEYS)
def test_ledger_still_true_optout(key: str) -> None:
    assert ledger._is_secret_key(key) is True


@pytest.mark.parametrize("key", _LEDGER_REPORTS_TRUE_KEYS + _PUBLIC_CREDENTIAL_KEYS)
def test_reports_still_true(key: str) -> None:
    assert reports._is_secret_key(key) is True


@pytest.mark.parametrize("key", _PUBLIC_CREDENTIAL_KEYS)
def test_ledger_public_credential_optin_still_true(key: str) -> None:
    assert ledger._is_secret_key(key, include_public_credential_keys=True) is True


@pytest.mark.parametrize("key", _TOOL_BOUNDARY_TRUE_KEYS)
def test_tool_boundary_still_true(key: str) -> None:
    assert tool_boundary._is_secret_key(key) is True


# False-positive guard: bare "key" stays a tool_boundary-only axis.
@pytest.mark.parametrize("key", ["keyCount", "objectKey", "publicKey"])
def test_bare_key_not_secret_in_ledger_reports(key: str) -> None:
    assert ledger._is_secret_key(key) is False
    assert reports._is_secret_key(key) is False


@pytest.mark.parametrize("key", ["keyCount", "objectKey", "publicKey"])
def test_bare_key_still_secret_in_tool_boundary(key: str) -> None:
    assert tool_boundary._is_secret_key(key) is True


def test_secret_key_name_grammar_single_home() -> None:
    # adk_bridge rebased onto the kernel grammar (same object string).
    assert safety.SECRET_KEY_NAME in adk_bridge._SECRET_KEY_VALUE_RE.pattern


def test_adk_bridge_secret_key_value_re_now_covers_session_key() -> None:
    # Session-key was missing from adk_bridge's old local grammar (stricter now).
    assert "[redacted]" in adk_bridge._redact_secret_text("session_key: abc123def")
