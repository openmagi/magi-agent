"""Shared primitives for the decomposed Gate5B chat serving stack.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1). This module holds
the cross-cutting pieces used by two or more of the extracted chat modules
(route config + env builders, fallback/diagnostic contract builders, public-
safe context-continuity projection, tool-bundle predicates, and generic digest/
env/label helpers). It exists so the extracted modules form a DAG instead of
importing each other through ``transport.chat``; behavior is unchanged and
``transport.chat`` re-exports every name for compatibility.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from magi_agent.config.env import is_read_quality_enabled
from magi_agent.gates.gate1a_readonly_tools import (
    GATE1A_READONLY_TOOL_NAMES,
    Gate1AReadOnlyToolBundle,
    Gate1AReadOnlyToolConfig,
)
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolBundle,
    Gate5BFullToolHostConfig,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.user_visible_model_routing import _safe_label_or_none
from magi_agent.shadow.gate5b4c3_live_runner_boundary import AdkPrimitivesLoader
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)

MockedChatRunner = Callable[[Mapping[str, Any]], Mapping[str, Any]]


ClientDisconnectedProbe = Callable[[Request], bool | Awaitable[bool]]


_RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization:|Bearer\s+\S+|(?:Cookie|Set-Cookie):|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key)\b|"
    r"\b(?:prompt|output|request[_-]?body|response[_-]?body)\s*[:=]\s*\S+|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+"
    r")",
    re.IGNORECASE,
)


_CONTEXT_REASON_CODE_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization|Bearer|Cookie|Set-Cookie|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key|credential)\b|"
    r"private|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+|"
    r"^[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}$|"
    r"^[A-Za-z0-9_-]{32,}$"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True, init=False)
class Gate5BUserVisibleChatRouteConfig:
    enabled: bool
    kill_switch_enabled: bool
    selected_bot_digest: str
    selected_owner_user_id_digest: str
    environment: str
    environment_allowlist: tuple[str, ...]
    mocked_runner: MockedChatRunner | None
    adk_primitives_loader: AdkPrimitivesLoader | None
    client_disconnected_probe: ClientDisconnectedProbe | None

    def __init__(
        self,
        enabled: object = False,
        kill_switch_enabled: object = True,
        selected_bot_digest: str = "",
        selected_owner_user_id_digest: str = "",
        environment: str = "",
        environment_allowlist: tuple[str, ...] = (),
        mocked_runner: MockedChatRunner | None = None,
        adk_primitives_loader: AdkPrimitivesLoader | None = None,
        client_disconnected_probe: ClientDisconnectedProbe | None = None,
        *,
        killSwitchEnabled: object | None = None,
        selectedBotDigest: str | None = None,
        selectedOwnerUserIdDigest: str | None = None,
        environmentAllowlist: tuple[str, ...] | None = None,
        mockedRunner: MockedChatRunner | None = None,
        adkPrimitivesLoader: AdkPrimitivesLoader | None = None,
        clientDisconnectedProbe: ClientDisconnectedProbe | None = None,
    ) -> None:
        object.__setattr__(self, "enabled", enabled is True)
        object.__setattr__(
            self,
            "kill_switch_enabled",
            kill_switch_enabled if killSwitchEnabled is None else killSwitchEnabled,
        )
        object.__setattr__(self, "selected_bot_digest", selectedBotDigest or selected_bot_digest)
        object.__setattr__(
            self,
            "selected_owner_user_id_digest",
            selectedOwnerUserIdDigest or selected_owner_user_id_digest,
        )
        object.__setattr__(self, "environment", environment)
        object.__setattr__(
            self,
            "environment_allowlist",
            tuple(environmentAllowlist or environment_allowlist),
        )
        object.__setattr__(self, "mocked_runner", mockedRunner or mocked_runner)
        object.__setattr__(
            self,
            "adk_primitives_loader",
            adkPrimitivesLoader or adk_primitives_loader,
        )
        object.__setattr__(
            self,
            "client_disconnected_probe",
            clientDisconnectedProbe or client_disconnected_probe,
        )


def build_gate5b_user_visible_chat_route_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate5BUserVisibleChatRouteConfig:
    if _is_true(env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED")):
        return Gate5BUserVisibleChatRouteConfig(
            enabled=True,
            killSwitchEnabled=_env_bool_default_true(
                env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH")
            ),
            selectedBotDigest=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST",
                "",
            ).strip(),
            selectedOwnerUserIdDigest=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST",
                "",
            ).strip(),
            environment=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT",
                "",
            ).strip(),
            environmentAllowlist=_csv_values(
                env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST", "")
            ),
        )
    return Gate5BUserVisibleChatRouteConfig(
        enabled=_is_true(env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED")),
        killSwitchEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH")
        ),
        selectedBotDigest=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
            "",
        ).strip(),
        selectedOwnerUserIdDigest=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
            "",
        ).strip(),
        environment=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
            "",
        ).strip(),
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST", "")
        ),
    )


def build_gate1a_readonly_tools_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate1AReadOnlyToolConfig:
    del runtime_config
    return Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENABLED")
            ),
            "killSwitchEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_KILL_SWITCH", "1")
            ),
            "routeAttachmentEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ROUTE_ATTACHMENT", "1")
            ),
            "selectedBotDigest": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_SELECTED_BOT_DIGEST",
                "",
            ).strip(),
            "selectedOwnerDigest": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_TRUSTED_OWNER_USER_ID_DIGEST",
                "",
            ).strip(),
            "environment": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENVIRONMENT",
                "local",
            ).strip()
            or "local",
            "environmentAllowlist": _csv_values(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENV_ALLOWLIST", "")
            ),
            "allowedToolNames": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ALLOWLIST",
                    ",".join(GATE1A_READONLY_TOOL_NAMES),
                )
            ),
            "maxToolCallsPerTurn": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_CALLS_PER_TURN"),
                fallback=8,
            ),
            "maxPerToolOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_PER_TOOL_BYTES"),
                fallback=4096,
            ),
            "maxAggregateOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_AGGREGATE_BYTES"),
                fallback=16384,
            ),
        }
    )


def build_gate5b_full_toolhost_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate5BFullToolHostConfig:
    del runtime_config
    from magi_agent.config.env import (
        apply_patch_enabled,
        is_format_on_write_enabled,
        parse_lsp_diagnostics_env,
    )

    lsp_diagnostics = parse_lsp_diagnostics_env(env)
    return Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED")
            ),
            "killSwitchEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH", "1")
            ),
            "routeAttachmentEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ROUTE_ATTACHMENT", "1")
            ),
            "selectedBotDigest": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_SELECTED_BOT_DIGEST",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
                    "",
                ),
            ).strip(),
            "selectedOwnerDigest": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_TRUSTED_OWNER_USER_ID_DIGEST",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
                    "",
                ),
            ).strip(),
            "environment": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENVIRONMENT",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
                    "local",
                ),
            ).strip()
            or "local",
            "environmentAllowlist": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENV_ALLOWLIST",
                    env.get(
                        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST",
                        "",
                    ),
                )
            ),
            "allowedToolNames": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ALLOWLIST",
                    ",".join(GATE5B_FULL_TOOLHOST_TOOL_NAMES),
                )
            ),
            "maxToolCallsPerTurn": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_CALLS_PER_TURN"),
                fallback=16,
            ),
            "maxPerToolOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_PER_TOOL_BYTES"),
                fallback=8192,
            ),
            "commandTimeoutMs": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_COMMAND_TIMEOUT_MS"),
                fallback=5000,
            ),
            "formatOnWriteEnabled": is_format_on_write_enabled(env),
            "lspDiagnosticsEnabled": lsp_diagnostics.enabled,
            "lspDiagnosticsCap": lsp_diagnostics.cap,
            "lspDiagnosticsTimeoutMs": lsp_diagnostics.timeout_ms,
            "readQualityEnabled": is_read_quality_enabled(env),
            "readMaxLines": _int_env(
                env.get("MAGI_READ_QUALITY_MAX_LINES"),
                fallback=2000,
            ),
            "ripgrepEnabled": _is_true(env.get("MAGI_RIPGREP_ENABLED")),
            "applyPatchEnabled": apply_patch_enabled(env),
            "applyPatchModelId": (
                env.get("CORE_AGENT_MODEL", "").strip()
            ),
        }
    )


def _route_config(runtime: OpenMagiRuntime) -> Gate5BUserVisibleChatRouteConfig:
    config = getattr(runtime, "gate5b_user_visible_chat_route_config", None)
    if isinstance(config, Gate5BUserVisibleChatRouteConfig):
        return config
    return Gate5BUserVisibleChatRouteConfig()


def _shadow_generation_route_config(
    runtime: OpenMagiRuntime,
) -> Gate5B4C3ShadowGenerationRouteConfig:
    config = getattr(runtime, "gate5b4c3_shadow_generation_route_config", None)
    if isinstance(config, Gate5B4C3ShadowGenerationRouteConfig):
        return config
    return Gate5B4C3ShadowGenerationRouteConfig()


def _context_continuity_chat_diagnostic(
    runtime: OpenMagiRuntime,
) -> dict[str, object] | None:
    continuity = getattr(runtime.config, "context_continuity", None)
    if continuity is None or getattr(continuity, "enabled", False) is not True:
        return None
    metadata = getattr(continuity, "health_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    return _public_safe_context_continuity_metadata(
        {
            "schemaVersion": "pregate8.contextContinuityChatDiagnostic.v1",
            "source": "server_runtime_config",
            "phase": "pre_gate8",
            "localOnly": True,
            "diagnosticOnly": True,
            "responseAuthority": "none",
            "clientMessagesTrustedForContinuity": False,
            **metadata,
        }
    )


def _public_safe_context_continuity_metadata(
    value: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    safe: dict[str, object] = {
        "schemaVersion": "pregate8.contextContinuityChatDiagnostic.v1",
        "source": "server_runtime_config",
        "phase": "pre_gate8",
        "localOnly": True,
        "diagnosticOnly": True,
        "responseAuthority": "none",
        "clientMessagesTrustedForContinuity": False,
    }
    bool_fields = (
        "continuityEnabled",
        "continuityCanaryReady",
        "compactionApplied",
        "projectionDigestPresent",
        "modelVisibleDigestPresent",
        "sourceTranscriptHeadDigestPresent",
        "canaryEvidenceVerified",
        "productionAuthorityAllowed",
        "transcriptWriteAllowed",
        "sseWriteAllowed",
        "dbWriteAllowed",
    )
    int_fields = ("importedEventCount", "rejectedEntryCount")
    label_fields = ("mode", "canaryStatus", "fallbackStatus")
    for field in bool_fields:
        safe[field] = value.get(field) is True
    for field in int_fields:
        safe[field] = max(0, _int_for_public_metadata(value.get(field)))
    for field in label_fields:
        safe[field] = _safe_label_or_default(value.get(field), "missing")
    reason_codes = value.get("reasonCodes")
    safe["reasonCodes"] = (
        _public_safe_context_reason_codes(reason_codes)
        if isinstance(reason_codes, (list, tuple))
        else []
    )
    safe["productionAuthorityAllowed"] = False
    safe["transcriptWriteAllowed"] = False
    safe["sseWriteAllowed"] = False
    safe["dbWriteAllowed"] = False
    return safe


def _public_safe_context_reason_codes(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_codes: list[str] = []
    for value in values:
        safe_value = _safe_label_or_none(value)
        if safe_value is None or safe_value in safe_codes:
            continue
        if _CONTEXT_REASON_CODE_FORBIDDEN_RE.search(safe_value):
            continue
        safe_codes.append(safe_value)
        if len(safe_codes) >= 16:
            break
    return safe_codes


def _int_for_public_metadata(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _safe_label_or_default(value: object, fallback: str) -> str:
    return _safe_label_or_none(value) or fallback


def _bounded_public_text(value: str, *, max_chars: int = 8192) -> str:
    return value[:max_chars]


def _fallback_response(
    *,
    status_code: int,
    status: str,
    reason: str,
    runtime: OpenMagiRuntime,
    counter_state: object | None = None,
    counter_status: str = "closed",
    adk_invoked: bool = False,
    runner_error_diagnostic: Mapping[str, object] | None = None,
    extra_content: Mapping[str, object] | None = None,
) -> JSONResponse:
    content = (
        {
            "status": status,
            "fallbackStatus": "fallback_to_typescript",
            "responseAuthority": "typescript",
            "reason": reason,
            "runtime": runtime.config.runtime,
            "runtimeEngine": runtime.config.runtime_engine,
            "adk": {
                "available": runtime.adk_boundary.available,
                "invoked": adk_invoked,
            },
        }
        if status != "python_disabled"
        else {
            "status": status,
            "fallbackStatus": "fallback_to_typescript",
            "responseAuthority": "typescript",
            "reason": reason,
            "runtime": runtime.config.runtime,
            "runtimeEngine": runtime.config.runtime_engine,
        }
    )
    if extra_content is not None:
        content.update(dict(extra_content))
    if counter_state is not None and hasattr(counter_state, "model_dump"):
        content["counter"] = {
            "status": counter_status,
            "state": counter_state.model_dump(by_alias=True, mode="json"),
        }
    if runner_error_diagnostic:
        content["runnerErrorDiagnostic"] = dict(runner_error_diagnostic)
    context_continuity = _context_continuity_chat_diagnostic(runtime)
    if context_continuity is not None:
        content["contextContinuity"] = context_continuity
    return JSONResponse(status_code=status_code, content=content)


def _reason_for_gate_error(status: str) -> str:
    if status == "invalid_authority":
        return "authority_gate_not_satisfied"
    return "canary_gate_disabled"


def _route_tool_bundle_ready(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return bundle is not None and bundle.status == "ready"


def _route_tool_bundle_full(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return isinstance(bundle, Gate5BFullToolBundle) and bundle.status == "ready"


def _route_tool_bundle_readonly(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return isinstance(bundle, Gate1AReadOnlyToolBundle) and bundle.status == "ready"


def _route_tool_bundle_names(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> list[str]:
    if not _route_tool_bundle_ready(bundle):
        return []
    return list(bundle.exposed_tool_names)


def _route_tool_bundle_mode(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> str:
    if _route_tool_bundle_full(bundle):
        return "gate5b_selected_full_toolhost"
    if _route_tool_bundle_readonly(bundle):
        return "gate1a_readonly_tools"
    return "no_route_tools"


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _is_sha256_digest(value: object) -> bool:
    return isinstance(value, str) and re.match(r"^sha256:[a-f0-9]{64}$", value) is not None


def _is_true(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_default_true(value: object) -> bool:
    if value is None:
        return True
    normalized = str(value or "").strip().lower()
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return True


def _int_env(value: object, *, fallback: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")
