from __future__ import annotations

from importlib.util import find_spec
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_serializer

ComposioEnabledMode = Literal["auto", "on", "off"]
ComposioCredentialSource = Literal["env", "hosted", "missing"]
ComposioDisabledReason = Literal[
    "disabled_by_config",
    "invalid_config",
    "missing_api_key",
    "missing_hosted_entity",
    "missing_python_package",
    "not_configured",
]

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,80}$")
_ENTITY_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_ENTITY_SEGMENT_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_COMPOSIO_MCP_HOSTNAME = "mcp.composio.dev"
_QUERY_DECODE_ROUNDS = 3
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "apikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "secret",
        "auth",
        "authorization",
        "bearer",
        "clientsecret",
    }
)
_CREDENTIAL_QUERY_SUBSTRINGS = frozenset(
    (
        "apikey",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "privatekey",
        "credential",
        "credentials",
        "password",
        "key",
        "session",
        "token",
        "secret",
        "auth",
        "bearer",
    )
)


class ComposioConfig(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    enabled_mode: ComposioEnabledMode = Field(default="auto", alias="enabledMode")
    active: bool = False
    configured: bool = False
    required: bool = False
    credential_source: ComposioCredentialSource = Field(
        default="missing",
        alias="credentialSource",
    )
    api_key: str | None = Field(default=None, alias="apiKey", repr=False, exclude=True)
    api_key_present: bool = Field(default=False, alias="apiKeyPresent")
    entity_id: str | None = Field(default=None, alias="entityId")
    entity_configured: bool = Field(default=False, alias="entityConfigured")
    toolkits: tuple[str, ...] = ()
    mcp_url_override: str | None = Field(
        default=None,
        alias="mcpUrlOverride",
        repr=False,
        exclude=True,
    )
    mcp_url_override_present: bool = Field(
        default=False,
        alias="mcpUrlOverridePresent",
    )
    disabled_reason: ComposioDisabledReason | None = Field(
        default=None,
        alias="disabledReason",
    )

    @field_serializer("api_key")
    def _serialize_api_key(self, _value: str | None) -> None:
        return None


def resolve_composio_config(
    env: Mapping[str, str],
    *,
    package_available: bool | None = None,
) -> ComposioConfig:
    enabled_mode, enabled_valid = _parse_enabled_mode(env.get("MAGI_COMPOSIO_ENABLED"))
    api_key = _trim(env.get("COMPOSIO_API_KEY"))
    package_ready = (
        _composio_package_available()
        if package_available is None
        else package_available
    )
    source, source_valid = _parse_credential_source(
        env.get("MAGI_COMPOSIO_CREDENTIAL_SOURCE"),
        api_key,
    )
    explicit_entity, entity_valid = _parse_explicit_entity_id(
        env.get("MAGI_COMPOSIO_ENTITY_ID"),
    )
    runtime_entity = _runtime_entity(env)
    entity_id = explicit_entity or runtime_entity or "default"
    toolkits, toolkits_valid = _parse_toolkits(env.get("MAGI_COMPOSIO_TOOLKITS"))
    mcp_url_override, mcp_url_override_valid = _parse_mcp_url_override(
        env.get("MAGI_COMPOSIO_MCP_URL")
    )

    configured = api_key is not None
    required = enabled_mode == "on"
    disabled_reason: ComposioDisabledReason | None = None
    active = False
    invalid_config = not (
        enabled_valid
        and source_valid
        and entity_valid
        and toolkits_valid
        and mcp_url_override_valid
    )

    if invalid_config:
        disabled_reason = "invalid_config"
    elif enabled_mode == "off":
        disabled_reason = "disabled_by_config"
    elif api_key is None:
        disabled_reason = (
            "missing_api_key" if enabled_mode == "on" else "not_configured"
        )
    elif enabled_mode == "auto" and not package_ready:
        disabled_reason = "missing_python_package"
    elif source == "hosted" and (
        entity_id == "default" or not runtime_entity and not explicit_entity
    ):
        disabled_reason = "missing_hosted_entity"
    else:
        active = True

    return ComposioConfig(
        enabledMode=enabled_mode,
        active=active,
        configured=configured,
        required=required,
        credentialSource=source,
        apiKey=api_key,
        apiKeyPresent=api_key is not None,
        entityId=entity_id if api_key is not None and entity_valid else None,
        entityConfigured=bool(explicit_entity or runtime_entity),
        toolkits=toolkits,
        mcpUrlOverride=mcp_url_override,
        mcpUrlOverridePresent=mcp_url_override is not None,
        disabledReason=disabled_reason,
    )


def _parse_enabled_mode(raw: str | None) -> tuple[ComposioEnabledMode, bool]:
    if raw is None or not raw.strip():
        return "auto", True
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return "on", True
    if value in _FALSE_VALUES:
        return "off", True
    if value == "auto":
        return "auto", True
    return "auto", False


def _composio_package_available() -> bool:
    return find_spec("composio") is not None


def _parse_credential_source(
    raw: str | None,
    api_key: str | None,
) -> tuple[ComposioCredentialSource, bool]:
    if raw is None or not raw.strip():
        if api_key:
            return "env", True
        return "missing", True

    value = raw.strip().lower()
    if value == "hosted":
        return "hosted", True
    if value == "env":
        return "env", True
    if value == "missing" and not api_key:
        return "missing", True
    return ("env" if api_key else "missing"), False


def _parse_toolkits(raw: str | None) -> tuple[tuple[str, ...], bool]:
    if raw is None or not raw.strip():
        return (), True

    values: list[str] = []
    for item in raw.split(","):
        normalized = item.strip().lower().replace("-", "_")
        if normalized and _SAFE_TOKEN_RE.fullmatch(normalized) and normalized not in values:
            values.append(normalized)
    if not values:
        return (), False
    return tuple(values), True


def _parse_mcp_url_override(raw: str | None) -> tuple[str | None, bool]:
    value = _trim(raw)
    if value is None:
        return None, True

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return None, False

    if parsed.scheme != "https":
        return None, False
    if hostname != _COMPOSIO_MCP_HOSTNAME:
        return None, False
    if parsed.username is not None or parsed.password is not None:
        return None, False
    if parsed.fragment:
        return None, False

    for key in _query_keys(parsed.query):
        if _is_credential_query_key(key):
            return None, False

    return value, True


def _query_keys(query: str) -> tuple[str, ...]:
    keys: list[str] = []
    for source in _query_variants(query):
        keys.extend(key for key, _item in parse_qsl(source, keep_blank_values=True))
        for raw_segment in re.split(r"[&;]", source):
            key, separator, _value = raw_segment.partition("=")
            if separator:
                keys.append(key)
    return tuple(keys)


def _query_variants(query: str) -> tuple[str, ...]:
    variants = [query]
    current = query
    for _ in range(_QUERY_DECODE_ROUNDS):
        decoded = unquote_plus(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(dict.fromkeys(variants))


def _is_credential_query_key(key: str) -> bool:
    normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
    return normalized_key in _CREDENTIAL_QUERY_KEYS or any(
        term in normalized_key for term in _CREDENTIAL_QUERY_SUBSTRINGS
    )


def _parse_explicit_entity_id(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, True
    raw_value = raw.strip()
    if not raw_value:
        return None, False
    entity_id = _sanitize_safe_value(raw_value, _ENTITY_ID_UNSAFE_RE, 160)
    if entity_id is None:
        return None, False
    return entity_id, True


def _runtime_entity(env: Mapping[str, str]) -> str | None:
    user_id = _trim(env.get("USER_ID"))
    bot_id = _trim(env.get("BOT_ID"))
    if not user_id or not bot_id:
        return None
    user_segment = _safe_entity_segment(user_id)
    bot_segment = _safe_entity_segment(bot_id)
    if not user_segment or not bot_segment:
        return None
    return f"openmagi:user:{user_segment}:bot:{bot_segment}"


def _safe_entity_segment(value: str) -> str:
    return _sanitize_safe_value(value, _ENTITY_SEGMENT_UNSAFE_RE, 128) or ""


def _sanitize_safe_value(
    value: str,
    unsafe_pattern: re.Pattern[str],
    limit: int,
) -> str | None:
    sanitized = unsafe_pattern.sub("_", value.strip())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_.:-")
    sanitized = sanitized[:limit].rstrip("_.:-")
    if not sanitized or not re.search(r"[A-Za-z0-9]", sanitized):
        return None
    return sanitized


def _trim(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
