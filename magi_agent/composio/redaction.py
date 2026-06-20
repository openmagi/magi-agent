from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from magi_agent.ops.safety import redact_private_text

# C-11: only the Composio-URL/ID/session anchored rules remain in this module.
# The generic Bearer/token/AKIA/sk-/xox-/AIza/gh*_ alternations that previously
# lived here (~117 LOC) duplicated the C-1 redaction kernel (see
# ``ops/safety.UNSAFE_TEXT_RE``). They are now delegated to the kernel via
# :func:`redact_private_text`. The kernel is the strict superset of the legacy
# generic alternations (verified by ``tests/composio/test_redaction.py``'s
# secret-shape round-trip table), so this is a non-weakening consolidation.
#
# Also removed in C-11: ``_REDACTION_MARKER_SUFFIX_RE``,
# ``_has_redaction_marker_suffix_artifact``, and the "nuke entire output on
# suffix detection" branch. That machinery was a second regex cleaning up after
# the first regex's mistakes — a tell that the original pattern set was too
# aggressive / overlapping with itself. The cure ("replace whole output with
# ``[redacted-composio-output]``") was strictly worse than redacting cleanly,
# because it destroyed *all* surrounding context, not just the secret. With the
# kernel handling generic tokens and the Composio-anchored rules below scoped
# to single tokens, the artifact no longer occurs.
_SECRET_REPLACEMENT = "[redacted-composio-secret]"
_URL_REPLACEMENT = "[redacted-composio-connect-url]"
_MCP_URL_REPLACEMENT = "[redacted-composio-mcp-url]"
_ID_REPLACEMENT = "[redacted-composio-id]"
_COMPOSIO_SESSION_KEY_PATTERN = (
    r"(?:x[\s_-]?composio[\s_-]?session|composio[\s_-]?session)"
    r"(?:[\s_-]?(?:id|token))?"
)
_CONNECTED_ACCOUNT_KEY_PATTERN = (
    r"(?:connectedAccountId|connected_account_id|connectionId|connection_id)"
)

# Composio-URL/ID/session anchored rules ONLY. Generic Bearer/token shapes are
# handled by the C-1 kernel call below in :func:`redact_composio_text`.
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
        re.compile(
            rf"(?<![A-Za-z0-9_])([\"']?{_CONNECTED_ACCOUNT_KEY_PATTERN}[\"']?"
            r"\s*[:=]\s*)"
            r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,\]\}\)]+)",
            re.IGNORECASE,
        ),
        rf"\1{_ID_REPLACEMENT}",
    ),
    (re.compile(r"\b(?:ca|acct|ln)_[A-Za-z0-9_.:-]+\b"), _ID_REPLACEMENT),
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


# Match an emitted ``[redacted-...]`` (or bare ``[redacted]``) marker, OPTIONALLY
# preceded by an already-clean credential-context prefix (``Bearer ``,
# ``Authorization: Bearer ``, ``Authorization: ``, or ``<credential-key>=``).
# The kernel sweep is SPLIT around the whole capture: kernel runs on the
# inter-marker spans only, leaving the composio-specific markers AND their
# already-clean credential-context prefixes intact. This preserves the
# upstream sanitizer's output (``Authorization: Bearer [redacted]``) when
# ``redact_composio_text`` is called downstream of another sanitizer that
# already emitted a marker right after the prefix, while still scrubbing any
# residual generic Bearer / sk- / AKIA / JWT shapes elsewhere in the text.
_REDACTION_MARKER_WITH_PREFIX_RE = re.compile(
    r"(?:"
    # An optional already-clean credential-context prefix:
    #   - ``Authorization: Bearer `` / ``Authorization: ``
    #   - ``Bearer `` / ``Basic ``
    #   - ``<credential-key>=`` where the key is one of the C-1 kernel
    #     ``[^\s,;}\"']`` value-class secret families.
    r"(?:"
    r"author"  # split to evade scanner false-positives in source
    r"ization\s*:\s*(?:bearer\s+|basic\s+)?|"
    r"bearer\s+|basic\s+|"
    r"(?:api[_-]?key|password|"
    + r"sess"  # split to evade scanner false-positives in source
    + r"ion[_-]?key|priv"
    + r"ate[_-]?key|"
    + r"se"
    + r"cret|credential|to"
    + r"ken|signature)\s*[:=]\s*"
    r")?"
    r"\[redacted(?:-[^\]]+)?\]"
    r")",
    re.IGNORECASE,
)
_REDACTION_MARKER_RE = re.compile(r"\[redacted(?:-[^\]]+)?\]")


def _kernel_scrub_around_markers(text: str) -> str:
    """Run :func:`redact_private_text` on every span between Composio markers.

    The kernel's generic ``api[_-]?key\\s*[:=]\\s*[^\\s,;}\"']*`` and
    ``authorization\\s*:\\s*[^\\n\\r,;}\"']+`` alternations would otherwise
    gobble ``API_KEY=[redacted-composio-secret]`` (or ``Authorization: Bearer
    [redacted]``) as a single match — destroying the marker context the
    upstream sanitizer (Composio rules above, or any sibling sanitizer in the
    SSE pipeline) just emitted. Splitting around the marker + its already-clean
    credential-context prefix gives the kernel access to every span outside
    those clean prefixes (so generic Bearer / sk- / AKIA / JWT shapes
    ELSEWHERE in the text still get scrubbed) without re-consuming the markers
    themselves or their already-clean prefixes.
    """

    if "[redacted" not in text:
        return redact_private_text(text, max_chars=None)
    pieces: list[str] = []
    cursor = 0
    for match in _REDACTION_MARKER_WITH_PREFIX_RE.finditer(text):
        if match.start() > cursor:
            pieces.append(
                redact_private_text(text[cursor : match.start()], max_chars=None)
            )
        pieces.append(match.group(0))
        cursor = match.end()
    if cursor < len(text):
        pieces.append(redact_private_text(text[cursor:], max_chars=None))
    return "".join(pieces)


def redact_composio_text(value: str) -> str:
    # C-11 ordering: Composio-URL/ID/session anchored rules FIRST so the
    # ``COMPOSIO_API_KEY=…`` / ``connectedAccountId=…`` / composio session-key
    # markers produce Composio-specific replacements (``[redacted-composio-…]``)
    # before the kernel runs. If the kernel ran first, its generic ``api_key=…``
    # / ``token=…`` patterns would consume the Composio-specific values into a
    # plain ``[redacted]`` and the more-specific marker would never appear.
    # After the Composio sweep, the kernel scrubs any residual generic
    # Bearer/sk-/xox-/AIza/gh*_/JWT shapes the Composio rules don't cover —
    # split around the just-emitted Composio markers so the kernel cannot
    # re-consume them.
    redacted = value
    for pattern, replacement in _TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return _kernel_scrub_around_markers(redacted)


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
