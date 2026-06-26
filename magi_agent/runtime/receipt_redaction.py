"""Single secret-scrubbing kernel for public receipt sanitization.

``runtime.receipt_utils`` and ``missions.receipts`` both project internal state
into public receipts and must strip credentials, private filesystem paths, and
unsafe content markers before anything leaves the trust boundary. The denylists
and the ``sanitize_public_text`` scrubber used to be copied verbatim into both
modules; a copy strengthened in one place but not the other silently leaks the
new secret shape through the un-updated path.

This leaf is the one authority for those primitives. It is intentionally
dependency-free (stdlib only) so any module may import it without a cycle. The
domain-specific pieces that legitimately differ between consumers (the set of
ref namespaces that survive as-is, and the fallback reason-code string) are
passed in by each consumer rather than re-implemented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re


_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$", re.IGNORECASE)
_SAFE_REDACTION_TOKEN_RE = re.compile(r"^\[[a-z0-9_.:-]*redacted[a-z0-9_.:-]*\]$")
_UNSAFE_REF_MARKER_RE = re.compile(
    r"raw[-_:]?(?:source|output|result|text|prompt|transcript|tool|log|args|"
    r"policy|snapshot|config|control|metadata|selector|recipe|authority|instruction)|"
    r"private[-_:]?(?:memory|mission|payload|path)|tool[-_:]?log|child[-_:]?prompt|"
    r"hidden[-_:]?reasoning|authorization|cookie|session|token|secret|credential|"
    r"private[-_:]?key|api[-_:]?key|bearer|connector[-_:]?token|password|"
    r"policy[-_:]?snapshot[-_:]?(?:text|prompt|payload|raw)|control[-_:]?metadata|"
    r"selector[-_:]?payload|recipe[-_:]?prompt|authority[-_:]?payload|"
    r"instruction[-_:]?payload",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
)


def sha256_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_ref(encoded)


def strict_sha256_ref(value: object) -> str:
    raw = str(value or "")
    if re.fullmatch(r"sha256:[a-f0-9]{64}", raw):
        return raw
    return sha256_ref(raw)


def sanitize_public_text(value: str) -> str:
    safe_lines = [
        line
        for line in str(value).splitlines()
        if line.strip() and not _RAW_PRIVATE_LINE_RE.search(line)
    ]
    clean = "\n".join(safe_lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def has_unsafe_marker(value: str) -> bool:
    return _UNSAFE_REF_MARKER_RE.search(value) is not None


def sanitize_public_ref(value: str, *, safe_ref_re: re.Pattern[str]) -> str:
    """Sanitize a public ref, allowing the caller's namespace vocabulary.

    ``safe_ref_re`` is the per-domain allowlist of ref prefixes that may survive
    verbatim (runtime activity/task/turn vs mission/mission-transition). The
    scrubbing and hash-fallback behaviour is identical across domains.
    """
    raw = str(value)
    path_sanitized = sanitize_public_text(raw)
    if path_sanitized.startswith("[redacted") and _SAFE_REDACTION_TOKEN_RE.fullmatch(
        path_sanitized,
    ):
        return path_sanitized
    clean = path_sanitized.strip()
    if _SAFE_REDACTION_TOKEN_RE.fullmatch(clean):
        return clean
    if safe_ref_re.fullmatch(clean) and not has_unsafe_marker(clean):
        return clean[:220]
    if _SAFE_ID_RE.fullmatch(clean) and not has_unsafe_marker(clean):
        return clean[:160]
    return "ref:" + sha256_ref(raw).removeprefix("sha256:")


def sanitize_reason_code(
    value: str,
    *,
    default: str,
    safe_codes: frozenset[str] = frozenset(),
) -> str:
    """Normalize a reason code, falling back to ``default`` when unsafe.

    ``safe_codes`` is the caller's allowlist of reason codes that pass through
    verbatim (missions keeps a lifecycle vocabulary); ``default`` is the
    domain's collapse value when the input is empty or carries an unsafe marker.
    """
    raw = str(value).strip().lower().replace(" ", "_")
    if raw in safe_codes:
        return raw
    if raw and all(char.isalnum() or char in "_:-." for char in raw) and not has_unsafe_marker(raw):
        return raw[:160]
    clean = sanitize_public_text(value).strip().lower().replace(" ", "_")
    if not clean or has_unsafe_marker(clean):
        return default
    return clean[:160]


def string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "canonical_digest",
    "has_unsafe_marker",
    "sanitize_public_ref",
    "sanitize_public_text",
    "sanitize_reason_code",
    "sha256_ref",
    "strict_sha256_ref",
    "string_tuple",
]
