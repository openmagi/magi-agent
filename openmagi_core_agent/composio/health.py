from __future__ import annotations

from importlib.util import find_spec

from openmagi_core_agent.composio.config import ComposioConfig
from openmagi_core_agent.composio.mcp import ComposioToolsetBundle
from openmagi_core_agent.composio.redaction import redact_composio_text


def composio_health_metadata(
    config: ComposioConfig,
    bundle: ComposioToolsetBundle | None = None,
    *,
    package_available: bool | None = None,
) -> dict[str, object]:
    package_ready = (
        composio_package_available() if package_available is None else package_available
    )
    last_error_class = bundle.last_error_class if bundle else None
    last_error_preview = bundle.last_error_preview if bundle else None
    disabled_reason = config.disabled_reason if bundle is None else bundle.reason
    active = config.active if bundle is None else bool(config.active and bundle.active)

    if config.active and not package_ready:
        active = False
        disabled_reason = "missing_python_package"

    return {
        "configured": config.configured,
        "active": active,
        "enabledMode": config.enabled_mode,
        "credentialSource": config.credential_source,
        "entityConfigured": config.entity_configured,
        "packageInstalled": package_ready,
        "toolkits": list(config.toolkits),
        "disabledReason": disabled_reason,
        "lastErrorClass": last_error_class,
        "lastErrorPreview": (
            _redact_last_error_preview(last_error_preview, config)
            if last_error_preview
            else None
        ),
        "nextAction": _next_action(config, disabled_reason),
    }


def composio_package_available() -> bool:
    return find_spec("composio") is not None


def _redact_last_error_preview(
    last_error_preview: str,
    config: ComposioConfig,
) -> str:
    preview = redact_composio_text(last_error_preview)
    if config.api_key:
        preview = preview.replace(config.api_key, "[redacted-composio-secret]")
    return preview


def _next_action(config: ComposioConfig, disabled_reason: str | None) -> str | None:
    if disabled_reason == "missing_api_key":
        return "set COMPOSIO_API_KEY to enable integrations"
    if disabled_reason == "disabled_by_config":
        return "set MAGI_COMPOSIO_ENABLED=auto or on"
    if disabled_reason == "missing_hosted_entity":
        return "set USER_ID and BOT_ID or MAGI_COMPOSIO_ENTITY_ID for hosted Composio"
    if disabled_reason == "missing_python_package":
        return "install the composio optional extra to enable integrations"
    return None
