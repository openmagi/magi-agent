"""Parity tests for the secret-token redaction kernel single home (B2, N-02).

Follows the S-03 precedent (``tests/test_receipt_redaction_kernel.py``):
1. identity: transport/memory attributes are the same object as the ops.safety
   kernel primitives,
2. golden: transport-surface outputs are byte-identical to pre-move behavior,
3. identical-or-stricter: adk_bridge output never redacts less than main did.

Secret-shaped fixtures are assembled from fragments at runtime so GitHub push
protection never sees a contiguous credential literal. The pristine-main golden
outputs below were captured before the move.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from magi_agent.memory import adk_bridge
from magi_agent.ops import safety
from magi_agent.transport import tool_preview


# Fragments (never a contiguous credential literal).
_SK = "sk-" + "test" + "0123456789abcdef"
_GHP = "gh" + "p_" + "A" * 20
_RK = "rk_" + "live_" + "abcDEF123456"

_TOKEN_CORPUS = [
    "Authorization: Bearer abc.def",
    "Proxy-Authorization: Basic dXNlcg==",
    "Cookie: session=deadbeef; a=1",
    _SK,
    _GHP,
    _RK,
    "api_key: mysecretvalue123",
    '"secret_key": "topsecret"',
    "'client_secret': 'abc'",
    "session = zzz",
    "session_key=abc123def",
    "normal words only",
    "objectKey: public_ref",
]

# Byte-identical golden (transport surface unchanged by the pure move).
_TP_GOLDEN = {
    "Authorization: Bearer abc.def": "Authorization: Bearer [redacted]",
    "Proxy-Authorization: Basic dXNlcg==": "Proxy-Authorization: Basic [redacted]",
    "Cookie: session=deadbeef; a=1": "Cookie: [redacted]",
    _SK: "[redacted]",
    _GHP: "[redacted]",
    _RK: "[redacted]",
    "api_key: mysecretvalue123": "api_key: [redacted]",
    '"secret_key": "topsecret"': '"secret_key": "[redacted]"',
    "'client_secret': 'abc'": "'client_secret': '[redacted]'",
    "session = zzz": "session = [redacted]",
    "session_key=abc123def": "session_key=[redacted]",
    "normal words only": "normal words only",
    "objectKey: public_ref": "objectKey: public_ref",
}

_ADK_CORPUS = _TOKEN_CORPUS + [
    "https://api.telegram.org/bot123456:AbCdEfToken/sendMessage",
    "https://mybucket.s3.amazonaws.com/file?X-Amz-Signature=deadbeef",
    "Cookie: sid=abc; foo=bar",
]

# adk_bridge OLD (pristine main) outputs, captured before the kernel-LAST wiring.
_ADK_OLD = {
    "Authorization: Bearer abc.def": "Authorization: Bearer [redacted]",
    "Proxy-Authorization: Basic dXNlcg==": "Proxy-Authorization: Basic [redacted]",
    "Cookie: session=deadbeef; a=1": "[redacted-cookie]",
    _SK: "[redacted]",
    _GHP: "[redacted]",
    _RK: "[redacted]",
    "api_key: mysecretvalue123": "api_key: [redacted]",
    '"secret_key": "topsecret"': '"secret_key": "topsecret"',
    "'client_secret': 'abc'": "'client_secret': 'abc'",
    "session = zzz": "session = zzz",
    "session_key=abc123def": "session_key=abc123def",
    "normal words only": "normal words only",
    "objectKey: public_ref": "objectKey: public_ref",
    "https://api.telegram.org/bot123456:AbCdEfToken/sendMessage": "[redacted-telegram-url]",
    "https://mybucket.s3.amazonaws.com/file?X-Amz-Signature=deadbeef": "[redacted-url]",
    "Cookie: sid=abc; foo=bar": "[redacted-cookie]",
}

_TOKEN_RE_NAMES = [
    "_BEARER_TOKEN_RE",
    "_AUTHORIZATION_HEADER_RE",
    "_GITHUB_TOKEN_RE",
    "_OPENAI_TOKEN_RE",
    "_STRIPE_TOKEN_RE",
]
_KERNEL_RE_NAMES = [
    "BEARER_TOKEN_RE",
    "AUTHORIZATION_HEADER_RE",
    "GITHUB_TOKEN_RE",
    "OPENAI_TOKEN_RE",
    "STRIPE_TOKEN_RE",
]


def test_redact_secret_tokens_is_single_object() -> None:
    assert tool_preview.redact_secret_tokens is safety.redact_secret_tokens
    assert adk_bridge._kernel_redact_secret_tokens is safety.redact_secret_tokens


@pytest.mark.parametrize("private_name,kernel_name", list(zip(_TOKEN_RE_NAMES, _KERNEL_RE_NAMES)))
def test_tool_preview_token_res_are_kernel_objects(private_name: str, kernel_name: str) -> None:
    assert getattr(tool_preview, private_name) is getattr(safety, kernel_name)


@pytest.mark.parametrize("private_name,kernel_name", list(zip(_TOKEN_RE_NAMES, _KERNEL_RE_NAMES)))
def test_adk_bridge_token_res_are_kernel_objects(private_name: str, kernel_name: str) -> None:
    assert getattr(adk_bridge, private_name) is getattr(safety, kernel_name)


@pytest.mark.parametrize("text", _TOKEN_CORPUS)
def test_tool_preview_redact_secret_tokens_golden(text: str) -> None:
    assert tool_preview.redact_secret_tokens(text) == _TP_GOLDEN[text]


@pytest.mark.parametrize("text", _TOKEN_CORPUS)
def test_sanitize_tool_preview_golden(text: str) -> None:
    assert tool_preview.sanitize_tool_preview(text) == _TP_GOLDEN[text]


@pytest.mark.parametrize("text", _ADK_CORPUS)
def test_adk_bridge_redact_identical_or_stricter(text: str) -> None:
    old = _ADK_OLD[text]
    new = adk_bridge._redact_secret_text(text)
    assert new == old or new.count("[redacted") >= old.count("[redacted")
    # Anything the old pipeline redacted must still be redacted.
    if "[redacted" in old:
        assert "[redacted" in new


def test_adk_bridge_now_covers_quoted_and_session_shapes() -> None:
    # New stricter coverage the site pipeline lacked before kernel-LAST.
    assert adk_bridge._redact_secret_text('"secret_key": "topsecret"') != '"secret_key": "topsecret"'
    assert adk_bridge._redact_secret_text("session = zzz") != "session = zzz"
    assert "[redacted" in adk_bridge._redact_secret_text("session_key=abc123def")


def test_kernel_import_pulls_no_transport_or_network() -> None:
    code = (
        "import sys, magi_agent.ops.safety\n"
        "bad = [m for m in sys.modules if m.startswith('magi_agent.transport')"
        " or m in ('urllib', 'requests', 'httpx', 'socket')]\n"
        "print(bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]", result.stdout
