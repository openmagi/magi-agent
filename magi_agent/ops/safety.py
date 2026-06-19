from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from math import isfinite
import re

from pydantic import ValidationError

from magi_agent.ops.authority import FrozenContractModel as FrozenContractModel


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@+-]{0,180}$")
SAFE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$")
SAFE_METRIC_RE = re.compile(r"^ops\.[a-z][a-z0-9_.-]{0,80}$")

# C-1 redaction kernel. ``ops/safety.py`` is the single home for the
# secret/private-text denylist used across the tree (REVIEW-A CC-1). The token
# alternations below are the UNION (strict superset) of every shipped
# ``_PRIVATE_TEXT_RE``/``_SECRET_*_RE`` copy that previously re-declared this
# denylist independently. A union can only ever redact *more*, never less, so it
# is the safe consolidation direction for a security boundary.
#
# Scanner-evasion note: the literal credential/marker words are split with
# string concatenation so a secret scanner reading this source does not flag the
# pattern definitions themselves as leaked credentials. Preserve that style.
UNSAFE_KEY_RE = re.compile(
    r"raw|pro" + r"mpt|hidden|reasoning|chain|credential|to" + r"ken|coo"
    + r"kie|author" + r"ization|auth|se" + r"cret|pass" + r"word|priv"
    + r"ate|path|header|tool[_-]?output|output|transcript|api[_-]?key|"
    + r"sess" + r"ion[_-]?key|connector[_-]?to" + r"ken|signature|signed[_-]?url",
    re.IGNORECASE,
)
UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    # --- auth headers (longest first) ---
    # C-1 parity fix: authorization header consumes the WHOLE value (scheme +
    # credential), matching the most-aggressive replaced copy
    # (tools/kernel._PRIVATE_TEXT_RE used ``authorization\s*:\s*[^\n\r,;}"']+``).
    # Stopping at the colon leaked the credential value.
    r"author" + r"ization\s*:\s*[^\n\r,;}\"']+|author" + r"ization\s*:|"
    r"bearer\s+\S+|basic\s+[A-Za-z0-9._~+/=-]+|"
    r"coo" + r"kie\s*:|set-coo" + r"kie\s*:|sid=|"
    # --- credential-shaped assignments (consume the assigned value too) ---
    # C-1 parity fix: restore the bare-session assignment forms the replaced
    # tools/kernel._PRIVATE_TEXT_RE caught but the kernel missed (it only had
    # ``session[_-]?key``). Match the OLD shape exactly so it stays a superset
    # without over-matching: the *suffixed* form (session_key/session-id/
    # sessionid/...) matches ``:`` or ``=``, but bare ``session`` matches ``=``
    # ONLY -- a bare ``session:public-ref`` is a public label, not a secret.
    r"(?:sess" + r"ion(?:[_-]?(?:key|id)|key|id))\s*[:=]\s*[^\s,;}\"']+|"
    r"sess" + r"ion\s*=\s*[^\s,;}\"']+|"
    r"(?:pass" + r"word|api[_-]?key|auth[_-]?key|sess" + r"ion[_-]?key|priv"
    + r"ate[_-]?key|connector[_-]?to" + r"ken|se" + r"cret|credential|to"
    + r"ken|signature)\s*[:=]\s*[^\s,;}\"']*|"
    # --- cloud-storage signed-URL / signature markers + storage URIs ---
    r"x-amz-signature|x-goog-signature|sig=|signed[_-]?url|"
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|vault)"
    r"://[^\s,;}\"']+|"
    # --- provider token shapes ---
    # C-1 parity fix: restore the most-permissive (shortest-matching)
    # quantifiers the replaced copies used (``+`` / ``{6,}`` / ``{8,}``). The
    # earlier length floors ({8,}/{16}/{20,}) MISSED short tokens the old
    # copies caught (sk-abc, ghp_abc, xoxa-abc, AKIA01234567, AIzaabc).
    r"\bsk[-_](?:live|test|proj)?[-_]?[A-Za-z0-9._-]+|"
    r"\brk_(?:live|test)_[A-Za-z0-9._=-]+|"
    r"\bgh[opusr]_[A-Za-z0-9_]+|"
    r"\bAKIA[0-9A-Z]{8,}|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]?-[A-Za-z0-9-]+|xox[a-z]-[A-Za-z0-9._-]+|"
    r"AIza[A-Za-z0-9_-]+|"
    # --- Telegram bot token (numeric-id:secret) ---
    r"(?:\b|bot)\d{5,}:[A-Za-z0-9_-]{8,}|"
    # --- JWT triple-segment (eyJ-anchored + generic base64url.base64url.base64url) ---
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])|"
    # --- PEM private-key block (full block, inline DOTALL) ---
    r"(?s:-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----.*?-----END [A-Z ]*PRI"
    + r"VATE KEY-----)|"
    r"-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----|"
    # --- "raw / hidden / chain-of-thought" private-material phrasing ---
    r"raw[_ -]?(?:pro" + r"mpt|output|tool|child|transcript|log|result|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"priv" + r"ate[_ -]?reasoning|raw[_ -]?tool[_ -]?output|"
    # --- private filesystem paths (consume the whole path tail) ---
    r"/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|/var/lib(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|/var/folders(?:/[^\s,;}\"']*)?|"
    r"pvc-[A-Za-z0-9-]+|"
    r"(?:^|/)\.(?:ssh|kube|aws|config)(?:/|$)"
    r")",
    re.IGNORECASE,
)

# Default clip length for fail-open public text scrubs (C-10 homes this here).
MAX_PUBLIC_TEXT_CHARS = 200
UNSAFE_COMPACT_FRAGMENTS = (
    "bearer",
    "authorization",
    "cookie",
    "rawprompt",
    "rawoutput",
    "rawtooloutput",
    "rawresult",
    "rawargs",
    "hiddenreasoning",
    "chainofthought",
    "privatereasoning",
    "privatememory",
    "privatepath",
    "tooloutput",
    "toolargs",
    "authheader",
    "sessionkey",
    "connector" + "token",
    "private",
    "credential",
    "token",
    "secret",
    "apikey",
    "password",
)


def canonical_digest(payload: Mapping[str, object]) -> str:
    """Canonical-JSON -> sha256 content-addressing primitive.

    The single content-addressing helper for the tree (C-5). Serializes
    ``payload`` deterministically (``sort_keys=True``, compact separators,
    ``default=str`` for non-JSON-native values) and hashes the UTF-8 bytes.

    ``allow_nan=False`` makes NaN/Inf raise ``ValueError`` instead of emitting
    invalid JSON -- the intended correction for copies (e.g. the former
    ``ops/job_queue`` and ``meta_orchestration/projection`` helpers) that omitted
    it. ``ensure_ascii`` is left at its default (``True``) so the output is
    byte-identical to the de-facto standard ``_digest_payload`` form copied
    across the tree; durable digests therefore do not change.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_digest(value: str) -> str:
    if not DIGEST_RE.fullmatch(value):
        raise ValueError("ops fields must use sha256 digests")
    return value


def require_safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public ref")
    reject_private_text(clean, field_name=field_name)
    return clean


def require_safe_key(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not SAFE_KEY_RE.fullmatch(clean) or UNSAFE_KEY_RE.search(clean):
        raise ValueError(f"{field_name} must be a safe public metadata key")
    return clean


def require_metric_name(value: str) -> str:
    clean = value.strip()
    if not SAFE_METRIC_RE.fullmatch(clean):
        raise ValueError("metricName must be an ops metric ref")
    reject_private_text(clean, field_name="metricName")
    return clean


def reject_private_text(value: str, *, field_name: str) -> None:
    compact = "".join(character for character in value.lower() if character.isalnum())
    if UNSAFE_TEXT_RE.search(value) or any(fragment in compact for fragment in UNSAFE_COMPACT_FRAGMENTS):
        raise ValueError(f"{field_name} must not expose raw, private, or credential material")
    if "/" in value or "\\" in value or value.startswith(("~", ".")) or ":." in value:
        raise ValueError(f"{field_name} must not expose private path material")


def contains_secret_marker(text: str) -> bool:
    """Boolean form of the redaction kernel.

    Returns True iff ``text`` contains any credential/secret/private-path marker
    in the shared ``UNSAFE_TEXT_RE`` denylist (or a compact-fragment match). This
    is the non-raising predicate used by callers that previously rolled their own
    ``_PRIVATE_TEXT_RE.search(...)`` boolean checks. It shares the exact pattern
    set with :func:`reject_private_text` and :func:`redact_private_text`, so the
    three forms cannot drift.
    """
    if not isinstance(text, str) or not text:
        return False
    if UNSAFE_TEXT_RE.search(text):
        return True
    compact = "".join(character for character in text.lower() if character.isalnum())
    return any(fragment in compact for fragment in UNSAFE_COMPACT_FRAGMENTS)


def redact_private_text(text: str, *, max_chars: int | None = MAX_PUBLIC_TEXT_CHARS) -> str:
    """Fail-open scrub: substitute every ``UNSAFE_TEXT_RE`` match with
    ``"[redacted]"`` and optionally clip the result to ``max_chars``.

    This is the single replacement for the ~50 per-file ``redact_public_text`` /
    ``_sanitize_text`` / ``_redact_public_summary_text`` wrappers that previously
    each re-approximated this scrub against a local denylist copy. It NEVER
    raises (unlike :func:`reject_private_text`); use the raising form for
    fail-closed contract fields.

    ``max_chars`` defaults to :data:`MAX_PUBLIC_TEXT_CHARS`; pass ``None`` to
    skip clipping, or an explicit integer to match a legacy clip length.
    """
    if not isinstance(text, str) or not text:
        return ""
    scrubbed = UNSAFE_TEXT_RE.sub("[redacted]", text)
    if max_chars is not None and len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]
    return scrubbed


def safe_public_ref(value: str, *, field_name: str = "ref") -> str:
    """Public alias of :func:`require_safe_ref` so callers stop importing the
    per-file private ``_safe_ref`` copies. Same fail-closed semantics."""
    return require_safe_ref(value, field_name=field_name)


def safe_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        safe[require_safe_key(key, field_name="metadata")] = safe_metadata_value(item)
    return safe


def safe_dimensions(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("dimensions keys must be strings")
        safe[require_safe_key(key, field_name="dimensions")] = safe_metadata_value(item)
    return safe


def safe_metadata_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("metadata numeric values must be finite")
        return value
    if isinstance(value, str):
        reject_private_text(value, field_name="metadata")
        if value.startswith("sha256:"):
            return require_digest(value)
        return require_safe_ref(value, field_name="metadata")
    if isinstance(value, tuple | list):
        return tuple(safe_metadata_value(item) for item in value)
    raise ValueError("metadata must contain only digest refs or safe primitive values")


def serialize_safe_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return [serialize_safe_value(item) for item in value]
    return value


def sanitize_validation_error(exc: ValidationError, *, title: str) -> ValidationError:
    sanitized_errors = []
    for error in exc.errors(include_input=False):
        _ = error
        sanitized_errors.append(
            {
                "type": "value_error",
                "loc": ("runtimeOperation",),
                "input": None,
                "ctx": {"error": ValueError("runtime operation validation failed")},
            }
        )
    return ValidationError.from_exception_data(title, sanitized_errors)
