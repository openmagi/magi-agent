"""Helpers for storing model-generated explanation text safely.

Model ``reason`` fields are untrusted model output. They can quote secrets,
prompt fragments, or user-controlled text. Evidence should retain useful
decision metadata without persisting the raw model string.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

__all__ = ["SafeReason", "safe_model_reason"]

_DEFAULT_PREVIEW_CHARS = 160
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_WHITESPACE_RE = re.compile(r"\s+")
_SECRET_VALUE_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]+|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|token|secret|password|session[_-]?key)\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_ROLE_MARKER_RE = re.compile(r"\b(?:system|developer|assistant|user)\s*:", re.IGNORECASE)
_PROMPT_FRAGMENT_RE = re.compile(
    r"\b(?:"
    r"reveal\s+hidden\s+prompt|"
    r"hidden\s+prompt|"
    r"system\s+prompt|"
    r"developer\s+message|"
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"prompt\s+injection"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafeReason:
    """Public-safe representation of an untrusted model reason string."""

    label: str
    digest: str | None = None
    preview: str | None = None


def safe_model_reason(
    raw_reason: object,
    *,
    label: str,
    max_preview_chars: int = _DEFAULT_PREVIEW_CHARS,
) -> SafeReason:
    """Return safe metadata for an untrusted model-generated reason."""
    safe_label = label if _SAFE_LABEL_RE.match(label) else "model_reason"
    raw = "" if raw_reason is None else str(raw_reason)
    if not raw:
        return SafeReason(label=safe_label)

    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    preview = _redacted_preview(raw, max_preview_chars=max_preview_chars)
    return SafeReason(label=safe_label, digest=digest, preview=preview or None)


def _redacted_preview(raw: str, *, max_preview_chars: int) -> str:
    preview = raw.replace("\x00", " ")
    preview = _ROLE_MARKER_RE.sub("[redacted-role-marker]", preview)
    preview = _PROMPT_FRAGMENT_RE.sub("[redacted-prompt-fragment]", preview)
    preview = _SECRET_VALUE_RE.sub("[redacted-secret]", preview)
    preview = _WHITESPACE_RE.sub(" ", preview).strip()
    if max_preview_chars > 0 and len(preview) > max_preview_chars:
        preview = preview[:max_preview_chars].rstrip()
    return preview
