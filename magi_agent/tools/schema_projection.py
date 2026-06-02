from __future__ import annotations

from collections.abc import Mapping, Sequence
import re


_SECRET_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bsk-(?:live|test)?[-_A-Za-z0-9]{6,}|"
    r"gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]{6,}|"
    r"xox[a-z]-[A-Za-z0-9._-]{6,}|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"-----END [A-Z ]*PRIVATE KEY-----|"
    r"(?:token|secret|credential|password|api[_-]?key)\s*[:=]\s*[^,\s}{\n]{4,}"
    r")",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|"
    r"/tmp/(?:opencode-inspect|openmagi-inspect|openmagi-workspace-[^/\s,;}\"']+|"
    r"[^/\s,;}\"']*(?:workspace|inspect)[^/\s,;}\"']*)(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY_MARKERS = (
    "auth",
    "cookie",
    "credential",
    "key",
    "password",
    "path",
    "private",
    "raw",
    "secret",
    "session",
    "token",
)
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_NODES = 512
_MAX_SCHEMA_PROPERTIES = 64
_MAX_SCHEMA_SEQUENCE_ITEMS = 64


def project_public_tool_schema(value: object) -> dict[str, object]:
    """Project provider-controlled JSON schema into a public-safe schema."""

    if not isinstance(value, Mapping):
        return {"type": "object", "additionalProperties": False}
    projected, _tainted = _project_mapping(value, depth=0, node_count=[0])
    if not isinstance(projected.get("type"), str):
        projected["type"] = "object"
    if "additionalProperties" not in projected:
        projected["additionalProperties"] = False
    return projected


def redact_public_schema_text(value: str) -> str:
    clean = _SECRET_TEXT_RE.sub("[redacted-private]", value)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()[:1000]


def contains_private_schema_text(value: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(value) or _PRIVATE_PATH_RE.search(value))


def is_sensitive_schema_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_METADATA_KEY_MARKERS)


class _Drop:
    pass


_DROP = _Drop()


def _project_mapping(
    value: Mapping[object, object],
    *,
    depth: int,
    node_count: list[int],
) -> tuple[dict[str, object], bool]:
    if not _claim_node(depth, node_count):
        return {}, True
    safe: dict[str, object] = {}
    tainted = False
    allowed_properties: set[str] | None = None
    pending_required: object = None

    for index, (key, item) in enumerate(value.items()):
        if index >= _MAX_SCHEMA_PROPERTIES:
            tainted = True
            break
        key_text = str(key)
        if contains_private_schema_text(key_text) or is_sensitive_schema_key(key_text):
            tainted = True
            continue
        if key_text == "required":
            pending_required = item
            continue
        if key_text == "additionalProperties":
            safe[key_text] = False
            tainted = tainted or item is not False
            continue
        if key_text == "properties" and isinstance(item, Mapping):
            properties, property_tainted = _project_properties(item, depth=depth + 1, node_count=node_count)
            safe[key_text] = properties
            allowed_properties = set(properties)
            tainted = tainted or property_tainted
            continue
        projected, item_tainted = _project_value(item, depth=depth + 1, node_count=node_count)
        if projected is _DROP or item_tainted:
            tainted = True
            continue
        safe[key_text] = projected
        tainted = tainted or item_tainted

    if pending_required is not None:
        required = _project_required(pending_required, allowed_properties)
        if required:
            safe["required"] = required
        tainted = True
    if _is_object_like_schema(safe):
        safe["additionalProperties"] = False
    return safe, tainted


def _project_properties(
    value: Mapping[object, object],
    *,
    depth: int,
    node_count: list[int],
) -> tuple[dict[str, object], bool]:
    if not _claim_node(depth, node_count):
        return {}, True
    safe: dict[str, object] = {}
    tainted = False
    for index, (key, item) in enumerate(value.items()):
        if index >= _MAX_SCHEMA_PROPERTIES:
            tainted = True
            break
        key_text = str(key)
        if contains_private_schema_text(key_text) or is_sensitive_schema_key(key_text):
            tainted = True
            continue
        projected, item_tainted = _project_value(item, depth=depth + 1, node_count=node_count)
        if projected is _DROP or item_tainted:
            tainted = True
            continue
        safe[key_text] = projected
        tainted = tainted or item_tainted
    return safe, tainted


def _project_required(value: object, allowed_properties: set[str] | None) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    required: list[str] = []
    for item in list(value)[:_MAX_SCHEMA_SEQUENCE_ITEMS]:
        if not isinstance(item, str):
            continue
        if contains_private_schema_text(item) or is_sensitive_schema_key(item):
            continue
        if allowed_properties is not None and item not in allowed_properties:
            continue
        required.append(item)
    return required


def _project_value(value: object, *, depth: int, node_count: list[int]) -> tuple[object, bool]:
    if not _claim_node(depth, node_count):
        return _DROP, True
    if isinstance(value, Mapping):
        return _project_mapping(value, depth=depth, node_count=node_count)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        projected: list[object] = []
        tainted = len(value) > _MAX_SCHEMA_SEQUENCE_ITEMS
        for item in list(value)[:_MAX_SCHEMA_SEQUENCE_ITEMS]:
            nested, nested_tainted = _project_value(item, depth=depth + 1, node_count=node_count)
            if nested is _DROP:
                tainted = True
                continue
            projected.append(nested)
            tainted = tainted or nested_tainted
        return projected, tainted
    if isinstance(value, str):
        tainted = contains_private_schema_text(value)
        return redact_public_schema_text(value), tainted
    if isinstance(value, bool | int | float) or value is None:
        return value, False
    return _DROP, True


def _claim_node(depth: int, node_count: list[int]) -> bool:
    if depth > _MAX_SCHEMA_DEPTH or node_count[0] >= _MAX_SCHEMA_NODES:
        return False
    node_count[0] += 1
    return True


def _is_object_like_schema(value: Mapping[str, object]) -> bool:
    return value.get("type") == "object" or "properties" in value


__all__ = [
    "contains_private_schema_text",
    "is_sensitive_schema_key",
    "project_public_tool_schema",
    "redact_public_schema_text",
]
