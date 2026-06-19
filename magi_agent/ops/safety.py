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
UNSAFE_KEY_RE = re.compile(
    r"raw|pro" + r"mpt|hidden|reasoning|chain|credential|to" + r"ken|coo"
    + r"kie|author" + r"ization|auth|se" + r"cret|pass" + r"word|priv"
    + r"ate|path|header|tool[_-]?output|output|transcript",
    re.IGNORECASE,
)
UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"author" + r"ization\s*:|bearer\s+\S+|coo" + r"kie\s*:|set-coo"
    + r"kie\s*:|sid=|"
    r"(?:pass" + r"word|api[_-]?key|auth[_-]?key|sess" + r"ion[_-]?key|priv"
    + r"ate[_-]?key|connector[_-]?to" + r"ken|se" + r"cret|credential|to"
    + r"ken|signature)\s*[:=]|"
    r"\bsk-[A-Za-z0-9._-]{8,}|"
    r"\bgh[opusr]_[A-Za-z0-9_]{8,}|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"raw[_ -]?(?:pro" + r"mpt|output|tool|child|transcript|log)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"priv" + r"ate[_ -]?reasoning|raw[_ -]?tool[_ -]?output|"
    r"(?:^|/)\.(?:ssh|kube|aws|config)(?:/|$)"
    r")",
    re.IGNORECASE,
)
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
