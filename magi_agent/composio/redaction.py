from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_REPLACEMENT = "[redacted-composio-secret]"
_URL_REPLACEMENT = "[redacted-composio-connect-url]"
_MCP_URL_REPLACEMENT = "[redacted-composio-mcp-url]"
_ID_REPLACEMENT = "[redacted-composio-id]"
_OUTPUT_REPLACEMENT = "[redacted-composio-output]"
_COMPOSIO_SESSION_KEY_PATTERN = (
    r"(?:x[\s_-]?composio[\s_-]?session|composio[\s_-]?session)"
    r"(?:[\s_-]?(?:id|token))?"
)
_CONNECTED_ACCOUNT_KEY_PATTERN = (
    r"(?:connectedAccountId|connected_account_id|connectionId|connection_id)"
)

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https://mcp\.composio\.dev/[^\s)>'\"]+", re.IGNORECASE), _MCP_URL_REPLACEMENT),
    (re.compile(r"https://connect\.composio\.dev/[^\s)>'\"]+", re.IGNORECASE), _URL_REPLACEMENT),
    (
        re.compile(
            r"([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
            r"secret|client[_-]?secret|authorization|auth|bearer)=)"
            r"[^&#\s)>'\"]+",
            re.IGNORECASE,
        ),
        rf"\1{_SECRET_REPLACEMENT}",
    ),
    (
        re.compile(
            rf"(?<![A-Za-z0-9_])([\"']?{_COMPOSIO_SESSION_KEY_PATTERN}[\"']?"
            r"\s*[:=]\s*)"
            r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,\]\}\)]+)",
            re.IGNORECASE,
        ),
        rf"\1{_SECRET_REPLACEMENT}",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])([\"']?"
            r"(?:COMPOSIO_API_KEY|composio_api_key)"
            r"[\"']?\s*[:=]\s*)"
            r"(?![\"']?\[redacted(?:-[^\]\"']+)?\][\"']?)"
            r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,\]\}\)]+)",
            re.IGNORECASE,
        ),
        rf"\1{_SECRET_REPLACEMENT}",
    ),
    (
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        f"Bearer {_SECRET_REPLACEMENT}",
    ),
    (
        re.compile(
            rf"(?<![A-Za-z0-9_])([\"']?{_CONNECTED_ACCOUNT_KEY_PATTERN}[\"']?"
            r"\s*[:=]\s*)"
            r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,\]\}\)]+)",
            re.IGNORECASE,
        ),
        rf"\1{_ID_REPLACEMENT}",
    ),
    (re.compile(r"\b(?:ca|acct)_[A-Za-z0-9_.:-]+\b"), _ID_REPLACEMENT),
)
_REDACTION_MARKER_SUFFIX_RE = re.compile(
    r"(?:"
    r"\[redacted-composio-[^\]]+\]|"
    r"\bBearer\s+\[redacted(?:-[^\]]+)?\]"
    r")"
    r"(?:[A-Za-z0-9_/]|[.:-](?=[A-Za-z0-9_/]))"
    r"[A-Za-z0-9_./:-]*",
    re.IGNORECASE,
)
_SECRET_KEY_RE = re.compile(
    rf"(api[_-]?key|authorization|bearer|token|secret|connect[_-]?url|"
    rf"{_COMPOSIO_SESSION_KEY_PATTERN})",
    re.IGNORECASE,
)
_CONNECTED_ACCOUNT_KEY_RE = re.compile(
    rf"^{_CONNECTED_ACCOUNT_KEY_PATTERN}$",
    re.IGNORECASE,
)


def redact_composio_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if _has_redaction_marker_suffix_artifact(redacted):
        return _OUTPUT_REPLACEMENT
    return redacted


def _has_redaction_marker_suffix_artifact(value: str) -> bool:
    return bool(_REDACTION_MARKER_SUFFIX_RE.search(value))


def redact_composio_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_composio_text(value)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _CONNECTED_ACCOUNT_KEY_RE.fullmatch(key_text):
                redacted[key_text] = _ID_REPLACEMENT
            elif _SECRET_KEY_RE.search(key_text):
                redacted[key_text] = _SECRET_REPLACEMENT
            else:
                redacted[key_text] = redact_composio_value(item)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [redact_composio_value(item) for item in value]
    return value
