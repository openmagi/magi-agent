from __future__ import annotations

import os
import re
import tempfile
import weakref
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    PrivateAttr,
    SerializationInfo,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import RuntimeMode, ToolManifest
from magi_agent.tools.result import ToolResult


Gate2ShadowToolMode = Literal["recorded_output", "synthetic_local"]

_SHADOW_TOOL_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)
_REJECTED_PERMISSION_CLASSES = frozenset({"write", "execute", "net"})
_REJECTED_TOOL_KINDS = frozenset({"custom", "external"})
_REJECTED_SOURCE_KINDS = frozenset({"custom-plugin", "external"})
_APPROVAL_REQUIRED_TAGS = frozenset({"approval_required", "requires_approval"})
_REPORT_ENVELOPE_FIELD_NAMES = frozenset(
    {
        "posture",
        "shadow_mode",
        "tool_name",
        "tool_kind",
        "source_kind",
        "permission_class",
        "side_effect_class",
        "mode",
    }
)
_ALLOWED_SHADOW_TOOL_MODES = frozenset({"recorded_output", "synthetic_local"})
_ALLOWED_SHADOW_TOOL_KINDS = frozenset({"core", "native", "skill-compat"})
_ALLOWED_SHADOW_SOURCE_KINDS = frozenset({"builtin", "native-plugin", "skill", "runtime"})
_ALLOWED_SHADOW_PERMISSION_CLASSES = frozenset({"read", "meta"})
_ALLOWED_SHADOW_SIDE_EFFECT_CLASSES = frozenset({"none"})
_ALLOWED_RUNTIME_MODES = frozenset({"plan", "act"})
_ALLOWED_TOOL_RESULT_STATUSES = frozenset(
    {"ok", "error", "blocked", "needs_approval"}
)
_CANONICAL_DIAGNOSTIC_METADATA = {
    "posture": "diagnostic_non_authoritative",
    "output_scope": "local_shadow_report_only",
    "production_authority": False,
    "adk_runner_attached": False,
    "production_route_attached": False,
    "user_visible": False,
    "production_transcript_append": False,
    "network_sse": False,
    "route_attached": False,
    "traffic_attached": False,
    "canary_attached": False,
    "production_attached": False,
    "execution_attached": False,
}
_OUTPUT_FLAG_FIELD_NAMES = frozenset(
    {
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "route_attached",
        "traffic_attached",
        "canary_attached",
        "production_attached",
    }
)
_FORBIDDEN_PUBLIC_AUTHORITY_CLAIM_KEYS = frozenset(
    {
        "api",
        "api_route",
        "api_route_attached",
        "canary",
        "dashboard",
        "dashboard_route",
        "dashboard_route_attached",
        "production",
        "production_route",
        "route",
        "runtime_selector",
        "telegram",
        "telegram_attached",
        "traffic",
        "ts_runtime",
        "type_script_runtime",
        "typescript_runtime",
        "ts_runtime_authoritative",
    }
)
_PUBLIC_PAYLOAD_REPORT_ENVELOPE_CLAIM_KEYS = (
    _REPORT_ENVELOPE_FIELD_NAMES
    | _FORBIDDEN_PUBLIC_AUTHORITY_CLAIM_KEYS
    | frozenset({"output_flags"})
)
_SECRET_KEY_MARKERS = frozenset(
    {
        "api_key",
        "auth_header",
        "authorization",
        "cookie",
        "credential",
        "github_token",
        "password",
        "private_key",
        "secret",
        "session_key",
        "service_role",
        "service_role_key",
        "token",
    }
)
_MAX_PUBLIC_STRING_LENGTH = 512
_KNOWN_OS_TEMP_ROOTS = (
    "/tmp",
    "/var/tmp",
    "/private/tmp",
    "/private/var/tmp",
)
_MACOS_TEMP_PARENT_ROOTS = (
    "/var/folders",
    "/private/var/folders",
)
_TEMP_ENV_VARS = ("TMPDIR", "TEMP", "TMP")
_PRODUCTION_WORKSPACE_MARKERS = (
    "/data/",
    "/workspace",
    "/workspaces",
    "/var/lib/kubelet",
    "/var/lib/longhorn",
    "/mnt/",
    "bot-",
    "magi-",
    "k8s",
    "kubernetes",
    "longhorn",
    "openclaw-home",
    "pvc",
    "workspace",
)
_FIRST_CAP_RE = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_NON_KEY_WORD_RE = re.compile(r"[^A-Za-z0-9]+")
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[opsru]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)([^&\s\"']+)",
    re.IGNORECASE,
)
_REPORT_ENVELOPE_SNAPSHOTS: dict[
    int,
    tuple[weakref.ReferenceType[object], Mapping[str, object]],
] = {}


class _FrozenJsonMapping(tuple[tuple[str, object], ...], Mapping[str, object]):
    __slots__ = ()

    def __new__(cls, data: Mapping[str, object]) -> Self:
        return tuple.__new__(cls, tuple(data.items()))

    def __getitem__(self, key: str) -> object:
        for stored_key, stored_value in tuple.__iter__(self):
            if stored_key == key:
                return stored_value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in tuple.__iter__(self))

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(tuple.__iter__(self)) == dict(other.items())
        return False

    def __repr__(self) -> str:
        return repr(dict(tuple.__iter__(self)))


class _FrozenJsonList(tuple[object, ...]):
    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return super().__eq__(other)


class Gate2ShadowToolPolicyError(ValueError):
    def __init__(
        self,
        reason: str,
        *,
        tool_name: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.reason = reason
        self.tool_name = tool_name
        self.metadata = dict(metadata or {})
        detail = reason if tool_name is None else f"{tool_name}: {reason}"
        super().__init__(detail)


class _ShadowToolModel(BaseModel):
    model_config = _SHADOW_TOOL_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate2ShadowToolOutputFlags(_ShadowToolModel):
    user_visible: Literal[False] = Field(default=False, alias="userVisible")
    production_transcript_append: Literal[False] = Field(
        default=False,
        alias="productionTranscriptAppend",
    )
    network_sse: Literal[False] = Field(default=False, alias="networkSse")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")

    def __getattribute__(self, name: str) -> object:
        if name in _OUTPUT_FLAG_FIELD_NAMES:
            return False
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _reject_non_false_values(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        for flag_value in value.values():
            if flag_value is not False:
                raise ValueError("Gate 2 shadow tool output flags must be JSON false")
        return value

    @field_serializer(
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "route_attached",
        "traffic_attached",
        "canary_attached",
        "production_attached",
    )
    def _serialize_false_flags(self, value: object) -> bool:
        return False


class Gate2ShadowToolReport(_ShadowToolModel):
    _canonical_envelope: Mapping[str, object] | None = PrivateAttr(default=None)

    posture: Literal["diagnostic_non_authoritative"] = "diagnostic_non_authoritative"
    shadow_mode: Gate2ShadowToolMode = Field(alias="shadowMode")
    tool_name: str = Field(alias="toolName")
    tool_kind: str = Field(alias="toolKind")
    source_kind: str = Field(alias="sourceKind")
    permission_class: str = Field(alias="permissionClass")
    side_effect_class: str = Field(alias="sideEffectClass")
    mode: RuntimeMode
    output_flags: Gate2ShadowToolOutputFlags = Field(
        default_factory=Gate2ShadowToolOutputFlags,
        alias="outputFlags",
    )
    tool_result: ToolResult = Field(alias="toolResult")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )

    def __getattribute__(self, name: str) -> object:
        if name == "__dict__":
            return _shadow_report_to_public_python_mapping(self)
        if name in _REPORT_ENVELOPE_FIELD_NAMES:
            return _canonical_report_envelope_value(self, name)
        if name == "output_flags":
            return Gate2ShadowToolOutputFlags()
        if name == "diagnostic_metadata":
            raw_state = object.__getattribute__(self, "__dict__")
            raw_value = raw_state.get("diagnostic_metadata", {})
            return _validate_and_freeze_diagnostic_metadata(raw_value)
        if name == "tool_result":
            envelope = _canonical_report_envelope(self)
            raw_value = _raw_report_field(self, "tool_result")
            if not isinstance(raw_value, ToolResult):
                raise Gate2ShadowToolPolicyError(
                    "tool result is not safe for Gate 2 shadow report: expected ToolResult",
                    tool_name=str(envelope["tool_name"]),
                )
            return _validate_and_freeze_shadow_tool_result(
                raw_value,
                tool_name=str(envelope["tool_name"]),
            )
        return super().__getattribute__(name)

    def __iter__(self) -> Iterator[tuple[str, object]]:  # type: ignore[override]
        return iter(_shadow_report_to_public_python_mapping(self).items())

    @field_validator("output_flags", mode="before")
    @classmethod
    def _normalize_output_flags(cls, _value: object) -> Gate2ShadowToolOutputFlags:
        return Gate2ShadowToolOutputFlags()

    @model_validator(mode="after")
    def _normalize_guarded_fields(self) -> Self:
        envelope = _canonical_report_envelope(self)
        _store_report_envelope(self, envelope)
        object.__setattr__(self, "output_flags", Gate2ShadowToolOutputFlags())
        object.__setattr__(
            self,
            "tool_result",
            _validate_and_freeze_shadow_tool_result(
                self.tool_result,
                tool_name=str(envelope["tool_name"]),
            ),
        )
        object.__setattr__(
            self,
            "diagnostic_metadata",
            _validate_and_freeze_diagnostic_metadata(self.diagnostic_metadata),
        )
        return self

    @model_serializer(mode="plain")
    def _serialize_report(self, info: SerializationInfo) -> dict[str, object]:
        by_alias = bool(info.by_alias)
        envelope = _canonical_report_envelope(self)
        tool_result = _raw_report_field(self, "tool_result")
        if not isinstance(tool_result, ToolResult):
            raise TypeError("tool result must be a ToolResult")
        safe_result = _validate_and_freeze_shadow_tool_result(
            tool_result,
            tool_name=str(envelope["tool_name"]),
        )
        diagnostic_metadata = _validate_and_freeze_diagnostic_metadata(
            _raw_report_field(self, "diagnostic_metadata"),
        )
        return _shadow_report_to_json_like(
            envelope,
            tool_result=safe_result,
            diagnostic_metadata=diagnostic_metadata,
            by_alias=by_alias,
        )

    @field_serializer("output_flags")
    def _serialize_output_flags(
        self,
        _value: object,
        info: FieldSerializationInfo,
    ) -> dict[str, bool]:
        return Gate2ShadowToolOutputFlags().model_dump(
            by_alias=bool(info.by_alias),
            mode=info.mode,
            warnings=False,
        )

    @field_serializer("tool_result")
    def _serialize_tool_result(
        self,
        value: object,
        info: FieldSerializationInfo,
    ) -> dict[str, object]:
        if not isinstance(value, ToolResult):
            raise TypeError("tool result must be a ToolResult")
        safe_result = _validate_and_freeze_shadow_tool_result(
            value,
            tool_name=str(self.tool_name),
        )
        return _tool_result_to_json_like(safe_result, by_alias=bool(info.by_alias))

    @field_serializer("diagnostic_metadata")
    def _serialize_diagnostic_metadata(self, value: object) -> object:
        return _thaw_json(_validate_and_freeze_diagnostic_metadata(value))


def run_gate2_recorded_tool_output(
    manifest: ToolManifest,
    *,
    recorded_result: ToolResult,
    arguments: Mapping[str, object] | None = None,
    mode: RuntimeMode,
) -> Gate2ShadowToolReport:
    _reject_manifest_for_gate2_shadow(manifest)
    safe_result = _validate_and_freeze_shadow_tool_result(
        recorded_result,
        tool_name=manifest.name,
    )
    metadata = _base_diagnostic_metadata(manifest, shadow_mode="recorded_output")
    metadata.update(
        {
            "handlerCalled": False,
            "resultScope": "diagnostic_metadata_only",
            "recordedToolResult": _tool_result_to_json_like(
                safe_result,
                by_alias=True,
            ),
            "recordedArguments": dict(arguments or {}),
        }
    )
    return _build_report(
        manifest,
        shadow_mode="recorded_output",
        tool_result=safe_result,
        diagnostic_metadata=metadata,
        mode=mode,
    )


async def run_gate2_synthetic_local_tool(
    dispatcher: ToolDispatcher,
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
    *,
    mode: RuntimeMode,
) -> Gate2ShadowToolReport:
    manifest = dispatcher.registry.resolve(tool_name)
    if manifest is None:
        raise Gate2ShadowToolPolicyError(
            "tool manifest not found for Gate 2 shadow policy",
            tool_name=tool_name,
        )

    _reject_manifest_for_gate2_shadow(manifest)
    _reject_non_temp_workspace_root(context.workspace_root, tool_name=manifest.name)

    safe_context = _sanitize_synthetic_local_context(context)
    dispatched_result = await dispatcher.dispatch(tool_name, arguments, safe_context, mode=mode)
    result = _validate_and_freeze_shadow_tool_result(
        _strip_synthetic_dispatcher_policy_metadata(
            dispatched_result,
            manifest=manifest,
            mode=mode,
        ),
        tool_name=manifest.name,
    )
    metadata = _base_diagnostic_metadata(manifest, shadow_mode="synthetic_local")
    metadata.update(
        {
            "toolHostMediated": True,
            "dispatcherOnly": True,
            "workspaceRoot": safe_context.workspace_root,
            "workspaceRootPolicy": "absent_or_local_temp",
        }
    )
    return _build_report(
        manifest,
        shadow_mode="synthetic_local",
        tool_result=result,
        diagnostic_metadata=metadata,
        mode=mode,
    )


def _reject_manifest_for_gate2_shadow(manifest: ToolManifest) -> None:
    metadata = _manifest_policy_metadata(manifest)
    if manifest.permission in _REJECTED_PERMISSION_CLASSES:
        raise Gate2ShadowToolPolicyError(
            f"{manifest.permission} permission is not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata=metadata,
        )
    if manifest.dangerous:
        raise Gate2ShadowToolPolicyError(
            "dangerous tools are not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata=metadata,
        )
    if manifest.mutates_workspace:
        raise Gate2ShadowToolPolicyError(
            "mutatesWorkspace tools are not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata=metadata,
        )
    if manifest.side_effect_class != "none":
        raise Gate2ShadowToolPolicyError(
            f"sideEffectClass={manifest.side_effect_class} is not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata=metadata,
        )
    approval_tags = _approval_required_manifest_tags(manifest)
    if approval_tags:
        raise Gate2ShadowToolPolicyError(
            "approval-required tools are not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata={**metadata, "approvalTags": sorted(approval_tags)},
        )
    if manifest.kind in _REJECTED_TOOL_KINDS or manifest.source.kind in _REJECTED_SOURCE_KINDS:
        raise Gate2ShadowToolPolicyError(
            "custom/external tool source/kind is not allowed in Gate 2 shadow tools",
            tool_name=manifest.name,
            metadata=metadata,
        )


def _approval_required_manifest_tags(manifest: ToolManifest) -> frozenset[str]:
    tags = {
        _normalize_diagnostic_metadata_key(tag)
        for tag in (*manifest.tags, *manifest.capability_tags)
    }
    return frozenset(tags & _APPROVAL_REQUIRED_TAGS)


def _sanitize_synthetic_local_context(context: ToolContext) -> ToolContext:
    return ToolContext(
        bot_id=context.bot_id,
        user_id=context.user_id,
        session_id=context.session_id,
        session_key=None,
        turn_id=context.turn_id,
        workspace_root=context.workspace_root,
        memory_mode=context.memory_mode,
        channel=context.channel,
        locale=context.locale,
        current_user_message=context.current_user_message,
        trace_id=context.trace_id,
        tool_use_id=context.tool_use_id,
        deadline_ms=context.deadline_ms,
        files_read=context.files_read,
        source_ledger=context.source_ledger,
        spawn_depth=context.spawn_depth,
        spawn_workspace=None,
        plugin_id=context.plugin_id,
        secret_scope=None,
        emit_progress=None,
        emit_agent_event=None,
        emit_control_event=None,
        ask_user=None,
        commit_handle=None,
        secret_broker=None,
        adk_tool_context=None,
        adk_context=None,
    )


def _reject_non_temp_workspace_root(workspace_root: str | None, *, tool_name: str) -> None:
    if workspace_root is None:
        return

    stripped = workspace_root.strip()
    if not stripped:
        raise Gate2ShadowToolPolicyError(
            "workspace_root must be absent or confined to a local temp directory",
            tool_name=tool_name,
        )

    lowered = stripped.lower()
    if _looks_production_or_workspace_backed(lowered):
        raise Gate2ShadowToolPolicyError(
            "workspace_root looks production-like or workspace-backed",
            tool_name=tool_name,
            metadata={"workspaceRoot": workspace_root},
        )

    candidate = Path(stripped).expanduser()
    if not candidate.is_absolute():
        raise Gate2ShadowToolPolicyError(
            "workspace_root must be absent or an absolute local temp path",
            tool_name=tool_name,
            metadata={"workspaceRoot": workspace_root},
        )

    candidate_real = _realpath(candidate)
    if _looks_production_or_workspace_backed(str(candidate_real).lower()):
        raise Gate2ShadowToolPolicyError(
            "workspace_root looks production-like or workspace-backed",
            tool_name=tool_name,
            metadata={"workspaceRoot": workspace_root},
        )

    if _has_git_repo_parent(candidate_real):
        raise Gate2ShadowToolPolicyError(
            "workspace_root looks production-like or workspace-backed",
            tool_name=tool_name,
            metadata={"workspaceRoot": workspace_root},
        )

    if not any(_is_path_relative_to(candidate_real, root) for root in _local_temp_roots()):
        raise Gate2ShadowToolPolicyError(
            "workspace_root must be absent or confined to a local temp directory",
            tool_name=tool_name,
            metadata={"workspaceRoot": workspace_root},
        )


def _local_temp_roots() -> tuple[Path, ...]:
    raw_roots = set(_KNOWN_OS_TEMP_ROOTS)
    raw_roots.add(tempfile.gettempdir())
    raw_roots.update(os.environ.get(name) or "" for name in _TEMP_ENV_VARS)
    return tuple(
        sorted(
            {
                _realpath(Path(root).expanduser())
                for root in raw_roots
                if _is_allowed_local_temp_root(root)
            },
            key=lambda path: str(path),
        )
    )


def _is_allowed_local_temp_root(root: str) -> bool:
    if not root:
        return False
    path = Path(root).expanduser()
    if not path.is_absolute():
        return False
    real = _realpath(path)
    if _looks_production_or_workspace_backed(str(real).lower()):
        return False

    known_roots = tuple(_realpath(Path(item)) for item in _KNOWN_OS_TEMP_ROOTS)
    if any(_is_path_relative_to(real, known_root) for known_root in known_roots):
        return True

    macos_roots = tuple(_realpath(Path(item)) for item in _MACOS_TEMP_PARENT_ROOTS)
    if not any(_is_path_relative_to(real, macos_root) for macos_root in macos_roots):
        return False

    return _has_temp_like_component(real)


def _has_temp_like_component(path: Path) -> bool:
    return any(part.lower() in {"t", "tmp", "temp"} for part in path.parts)


def _looks_production_or_workspace_backed(lowered_path: str) -> bool:
    return any(marker in lowered_path for marker in _PRODUCTION_WORKSPACE_MARKERS)


def _has_git_repo_parent(path: Path) -> bool:
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return True
    return False


def _realpath(path: Path) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def _is_path_relative_to(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(candidate), os.fspath(root)))
    except ValueError:
        return False
    return common == os.fspath(root)


def _raw_report_field(report: Gate2ShadowToolReport, field_name: str) -> object:
    raw_state = object.__getattribute__(report, "__dict__")
    if field_name in raw_state:
        return raw_state[field_name]
    if field_name == "posture":
        return "diagnostic_non_authoritative"
    raise ValueError(f"shadow report envelope missing field {field_name!r}")


def _stored_report_envelope(
    report: Gate2ShadowToolReport,
) -> Mapping[str, object] | None:
    snapshot_key = id(report)
    stored = _REPORT_ENVELOPE_SNAPSHOTS.get(snapshot_key)
    if stored is None:
        return None

    report_ref, envelope = stored
    if report_ref() is report:
        return envelope

    _REPORT_ENVELOPE_SNAPSHOTS.pop(snapshot_key, None)
    return None


def _validate_stored_report_envelope(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("shadow report envelope must be a mapping")
    return _validate_report_envelope(value)


def _store_report_envelope(
    report: Gate2ShadowToolReport,
    envelope: Mapping[str, object],
) -> None:
    canonical_envelope = _validate_stored_report_envelope(envelope)
    snapshot_key = id(report)

    def remove_snapshot(
        dead_ref: weakref.ReferenceType[object],
        key: int = snapshot_key,
    ) -> None:
        stored = _REPORT_ENVELOPE_SNAPSHOTS.get(key)
        if stored is not None and stored[0] is dead_ref:
            _REPORT_ENVELOPE_SNAPSHOTS.pop(key, None)

    _REPORT_ENVELOPE_SNAPSHOTS[snapshot_key] = (
        weakref.ref(report, remove_snapshot),
        canonical_envelope,
    )

    try:
        private_state = object.__getattribute__(report, "__pydantic_private__")
    except AttributeError:
        object.__setattr__(report, "_canonical_envelope", canonical_envelope)
        return
    if isinstance(private_state, dict):
        private_state["_canonical_envelope"] = canonical_envelope
    else:
        object.__setattr__(report, "_canonical_envelope", canonical_envelope)


def _canonical_report_envelope(
    report: Gate2ShadowToolReport,
) -> Mapping[str, object]:
    stored = _stored_report_envelope(report)
    if stored is not None:
        return stored
    return _validate_report_envelope(
        {
            field_name: _raw_report_field(report, field_name)
            for field_name in _REPORT_ENVELOPE_FIELD_NAMES
        }
    )


def _canonical_report_envelope_value(
    report: Gate2ShadowToolReport,
    field_name: str,
) -> object:
    return _canonical_report_envelope(report)[field_name]


def _validate_report_envelope(
    value: Mapping[str, object],
) -> Mapping[str, object]:
    tool_name = value.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("shadow report envelope tool_name must be a non-empty string")

    return _FrozenJsonMapping(
        {
            "posture": _validate_report_literal(
                value.get("posture"),
                field_name="posture",
                allowed=frozenset({"diagnostic_non_authoritative"}),
            ),
            "shadow_mode": _validate_report_literal(
                value.get("shadow_mode"),
                field_name="shadow_mode",
                allowed=_ALLOWED_SHADOW_TOOL_MODES,
            ),
            "tool_name": tool_name,
            "tool_kind": _validate_report_literal(
                value.get("tool_kind"),
                field_name="tool_kind",
                allowed=_ALLOWED_SHADOW_TOOL_KINDS,
            ),
            "source_kind": _validate_report_literal(
                value.get("source_kind"),
                field_name="source_kind",
                allowed=_ALLOWED_SHADOW_SOURCE_KINDS,
            ),
            "permission_class": _validate_report_literal(
                value.get("permission_class"),
                field_name="permission_class",
                allowed=_ALLOWED_SHADOW_PERMISSION_CLASSES,
            ),
            "side_effect_class": _validate_report_literal(
                value.get("side_effect_class"),
                field_name="side_effect_class",
                allowed=_ALLOWED_SHADOW_SIDE_EFFECT_CLASSES,
            ),
            "mode": _validate_report_literal(
                value.get("mode"),
                field_name="mode",
                allowed=_ALLOWED_RUNTIME_MODES,
            ),
        }
    )


def _validate_report_literal(
    value: object,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    if not isinstance(value, str) or value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(
            f"shadow report envelope {field_name} must be one of: {allowed_values}"
        )
    return value


def _shadow_report_to_json_like(
    envelope: Mapping[str, object],
    *,
    tool_result: ToolResult,
    diagnostic_metadata: Mapping[str, object],
    by_alias: bool,
) -> dict[str, object]:
    return {
        "posture": envelope["posture"],
        ("shadowMode" if by_alias else "shadow_mode"): envelope["shadow_mode"],
        ("toolName" if by_alias else "tool_name"): envelope["tool_name"],
        ("toolKind" if by_alias else "tool_kind"): envelope["tool_kind"],
        ("sourceKind" if by_alias else "source_kind"): envelope["source_kind"],
        (
            "permissionClass" if by_alias else "permission_class"
        ): envelope["permission_class"],
        (
            "sideEffectClass" if by_alias else "side_effect_class"
        ): envelope["side_effect_class"],
        "mode": envelope["mode"],
        ("outputFlags" if by_alias else "output_flags"): Gate2ShadowToolOutputFlags().model_dump(
            by_alias=by_alias,
            mode="json",
            warnings=False,
        ),
        ("toolResult" if by_alias else "tool_result"): _tool_result_to_json_like(
            tool_result,
            by_alias=by_alias,
        ),
        (
            "diagnosticMetadata" if by_alias else "diagnostic_metadata"
        ): _thaw_json(diagnostic_metadata),
    }


def _shadow_report_to_public_python_mapping(
    report: Gate2ShadowToolReport,
) -> dict[str, object]:
    envelope = _canonical_report_envelope(report)
    raw_tool_result = _raw_report_field(report, "tool_result")
    if not isinstance(raw_tool_result, ToolResult):
        raise Gate2ShadowToolPolicyError(
            "tool result is not safe for Gate 2 shadow report: expected ToolResult",
            tool_name=str(envelope["tool_name"]),
        )
    safe_tool_result = _validate_and_freeze_shadow_tool_result(
        raw_tool_result,
        tool_name=str(envelope["tool_name"]),
    )
    diagnostic_metadata = _validate_and_freeze_diagnostic_metadata(
        _raw_report_field(report, "diagnostic_metadata"),
    )
    return {
        "posture": envelope["posture"],
        "shadow_mode": envelope["shadow_mode"],
        "tool_name": envelope["tool_name"],
        "tool_kind": envelope["tool_kind"],
        "source_kind": envelope["source_kind"],
        "permission_class": envelope["permission_class"],
        "side_effect_class": envelope["side_effect_class"],
        "mode": envelope["mode"],
        "output_flags": Gate2ShadowToolOutputFlags(),
        "tool_result": safe_tool_result,
        "diagnostic_metadata": diagnostic_metadata,
    }


def _build_report(
    manifest: ToolManifest,
    *,
    shadow_mode: Gate2ShadowToolMode,
    tool_result: ToolResult,
    diagnostic_metadata: Mapping[str, object],
    mode: RuntimeMode,
) -> Gate2ShadowToolReport:
    safe_tool_result = _validate_and_freeze_shadow_tool_result(
        tool_result,
        tool_name=manifest.name,
    )
    return Gate2ShadowToolReport(
        shadow_mode=shadow_mode,
        tool_name=manifest.name,
        tool_kind=manifest.kind,
        source_kind=manifest.source.kind,
        permission_class=manifest.permission,
        side_effect_class=manifest.side_effect_class,
        mode=mode,
        tool_result=safe_tool_result,
        diagnostic_metadata=dict(diagnostic_metadata),
    )


def _base_diagnostic_metadata(
    manifest: ToolManifest,
    *,
    shadow_mode: Gate2ShadowToolMode,
) -> dict[str, object]:
    metadata = {
        "dangerous": manifest.dangerous,
        "mutatesWorkspace": manifest.mutates_workspace,
    }
    metadata.update(
        {
            "outputScope": "local_shadow_report_only",
            "productionAuthority": False,
            "adkRunnerAttached": False,
            "productionRouteAttached": False,
            "trafficAttached": False,
            "canaryAttached": False,
            "productionAttached": False,
        }
    )
    return metadata


def _manifest_policy_metadata(manifest: ToolManifest) -> dict[str, object]:
    return {
        "toolName": manifest.name,
        "toolKind": manifest.kind,
        "sourceKind": manifest.source.kind,
        "permissionClass": manifest.permission,
        "dangerous": manifest.dangerous,
        "mutatesWorkspace": manifest.mutates_workspace,
        "sideEffectClass": manifest.side_effect_class,
    }


def _validate_and_freeze_diagnostic_metadata(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("diagnostic metadata must be a mapping")
    sanitized = _sanitize_shadow_public_value(value)
    _validate_json_like(sanitized, label="diagnostic metadata")
    _validate_reserved_shadow_public_payload_claims(sanitized, label="diagnostic metadata")
    _validate_reserved_diagnostic_metadata(sanitized)
    frozen = _freeze_json(sanitized)
    if not isinstance(frozen, Mapping):
        raise ValueError("diagnostic metadata must be a mapping")
    return frozen


def _validate_reserved_diagnostic_metadata(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized_key = _normalize_diagnostic_metadata_key(key)
                canonical_value = _CANONICAL_DIAGNOSTIC_METADATA.get(normalized_key)
            else:
                canonical_value = None
            if canonical_value is not None:
                if not _matches_canonical_metadata_value(item, canonical_value):
                    raise ValueError(
                        f"diagnostic metadata reserved key {key!r} must be "
                        f"{canonical_value!r}"
                    )
            _validate_reserved_diagnostic_metadata(item)
        return
    if isinstance(value, (list, _FrozenJsonList)):
        for item in value:
            _validate_reserved_diagnostic_metadata(item)


def _normalize_diagnostic_metadata_key(key: str) -> str:
    key = key.strip()
    key = _FIRST_CAP_RE.sub(r"\1_\2", key)
    key = _CAMEL_RE.sub(r"\1_\2", key)
    key = _NON_KEY_WORD_RE.sub("_", key)
    return key.strip("_").lower()


def _matches_canonical_metadata_value(value: object, canonical_value: object) -> bool:
    if isinstance(canonical_value, bool):
        return value is canonical_value
    return value == canonical_value


def _validate_json_like(value: object, *, label: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not (value == value and value not in (float("inf"), float("-inf"))):
            raise ValueError(f"{label} must contain only JSON-compatible values")
        return
    if isinstance(value, (list, _FrozenJsonList)):
        for item in value:
            _validate_json_like(item, label=label)
        return
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ValueError(f"{label} must contain only JSON-compatible keys")
            _validate_json_like(nested_value, label=label)
        return
    if isinstance(value, tuple):
        raise ValueError(f"{label} must contain only JSON-compatible values")
    raise ValueError(f"{label} must contain only JSON-compatible values")


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenJsonMapping(
            {
                key: _freeze_json(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return _FrozenJsonList(_freeze_json(item) for item in value)
    if isinstance(value, _FrozenJsonList):
        return value
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _validate_and_freeze_shadow_public_value(value: object, *, label: str) -> object:
    sanitized = _sanitize_shadow_public_value(value)
    _validate_json_like(sanitized, label=label)
    _validate_reserved_diagnostic_metadata(sanitized)
    _validate_reserved_shadow_public_payload_claims(sanitized, label=label)
    return _freeze_json(sanitized)


def _sanitize_shadow_public_value(value: object, *, key: object | None = None) -> object:
    if isinstance(key, str) and _is_secret_like_public_key(key):
        return "[REDACTED]"

    if isinstance(value, Mapping):
        return {
            _sanitize_shadow_public_key(item_key): _sanitize_shadow_public_value(
                item,
                key=item_key,
            )
            for item_key, item in value.items()
        }
    if isinstance(value, (list, _FrozenJsonList)):
        return [_sanitize_shadow_public_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_shadow_public_string(value)
    return value


def _sanitize_shadow_public_key(key: object) -> object:
    if not isinstance(key, str):
        return key
    return _sanitize_shadow_public_string(key)


def _is_secret_like_public_key(key: str) -> bool:
    normalized_key = _normalize_diagnostic_metadata_key(key)
    return any(marker in normalized_key for marker in _SECRET_KEY_MARKERS)


def _sanitize_shadow_public_string(value: str) -> str:
    if _looks_public_path_secret(value):
        return "[REDACTED]"

    sanitized = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", value)
    sanitized = _GITHUB_TOKEN_RE.sub("[REDACTED]", sanitized)
    sanitized = _OPENAI_TOKEN_RE.sub("[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_RE.sub(
        _sanitize_secret_assignment_match,
        sanitized,
    )
    if len(sanitized) > _MAX_PUBLIC_STRING_LENGTH:
        return f"{sanitized[:_MAX_PUBLIC_STRING_LENGTH]}...[TRUNCATED]"
    return sanitized


def _sanitize_secret_assignment_match(match: re.Match[str]) -> str:
    key = match.group(1)
    if not _is_secret_like_public_key(key):
        return match.group(0)
    return f"{key}{match.group(2)}[REDACTED]"


def _looks_public_path_secret(value: str) -> bool:
    lowered = value.lower()
    return "/" in lowered and _looks_production_or_workspace_backed(lowered)


def _validate_reserved_shadow_public_payload_claims(
    value: object,
    *,
    label: str,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized_key = _normalize_diagnostic_metadata_key(key)
                if normalized_key in _PUBLIC_PAYLOAD_REPORT_ENVELOPE_CLAIM_KEYS:
                    raise ValueError(
                        f"{label} reserved shadow report envelope key "
                        f"{key!r} is not allowed"
                    )
            _validate_reserved_shadow_public_payload_claims(item, label=label)
        return
    if isinstance(value, (list, _FrozenJsonList)):
        for item in value:
            _validate_reserved_shadow_public_payload_claims(item, label=label)


def _validate_and_freeze_shadow_tool_result(
    result: ToolResult,
    *,
    tool_name: str,
) -> ToolResult:
    if not isinstance(result, ToolResult):
        raise Gate2ShadowToolPolicyError(
            "tool result is not safe for Gate 2 shadow report: expected ToolResult",
            tool_name=tool_name,
        )

    try:
        status = _validate_shadow_tool_result_status(result.status)
        output = _validate_and_freeze_shadow_public_value(
            result.output,
            label="tool result output",
        )
        llm_output = _validate_and_freeze_shadow_public_value(
            result.llm_output,
            label="tool result llmOutput",
        )
        transcript_output = _validate_and_freeze_shadow_public_value(
            result.transcript_output,
            label="tool result transcriptOutput",
        )
        if not isinstance(result.metadata, Mapping):
            raise ValueError("tool result metadata must be a mapping")
        metadata = _validate_and_freeze_shadow_public_value(
            result.metadata,
            label="tool result metadata",
        )
        if not isinstance(metadata, Mapping):
            raise ValueError("tool result metadata must be a mapping")
        error_code = _validate_optional_shadow_tool_result_string(
            result.error_code,
            label="tool result errorCode",
        )
        error_message = _validate_optional_shadow_tool_result_string(
            result.error_message,
            label="tool result errorMessage",
        )
        duration_ms = _validate_shadow_tool_duration_ms(result.duration_ms)
        artifact_refs = _validate_shadow_tool_refs(
            result.artifact_refs,
            label="tool result artifactRefs",
        )
        file_refs = _validate_shadow_tool_refs(
            result.file_refs,
            label="tool result fileRefs",
        )
        delivery_receipts = _validate_shadow_tool_refs(
            result.delivery_receipts,
            label="tool result deliveryReceipts",
        )
        retryable = _validate_shadow_tool_retryable(result.retryable)
    except ValueError as exc:
        raise Gate2ShadowToolPolicyError(
            f"tool result is not safe for Gate 2 shadow report: {exc}",
            tool_name=tool_name,
        ) from exc

    return ToolResult.model_construct(
        status=status,
        output=output,
        llm_output=llm_output,
        transcript_output=transcript_output,
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms,
        artifact_refs=artifact_refs,
        file_refs=file_refs,
        delivery_receipts=delivery_receipts,
        retryable=retryable,
        metadata=metadata,
    )


def _strip_synthetic_dispatcher_policy_metadata(
    result: ToolResult,
    *,
    manifest: ToolManifest,
    mode: RuntimeMode,
) -> ToolResult:
    if not isinstance(result, ToolResult):
        return result
    if result.status != "blocked" or not isinstance(result.metadata, Mapping):
        return result

    expected_reason: str | None = None
    if not manifest.enabled_by_default:
        expected_reason = "tool disabled"
    elif mode not in manifest.available_in_modes:
        expected_reason = f"tool unavailable in {mode} mode"
    if result.metadata.get("reason") != expected_reason:
        return result

    metadata = {
        key: item
        for key, item in result.metadata.items()
        if not (
            isinstance(key, str)
            and _normalize_diagnostic_metadata_key(key)
            in _PUBLIC_PAYLOAD_REPORT_ENVELOPE_CLAIM_KEYS
        )
    }
    return ToolResult.model_construct(
        status=result.status,
        output=result.output,
        llm_output=result.llm_output,
        transcript_output=result.transcript_output,
        error_code=result.error_code,
        error_message=result.error_message,
        duration_ms=result.duration_ms,
        artifact_refs=result.artifact_refs,
        file_refs=result.file_refs,
        delivery_receipts=result.delivery_receipts,
        retryable=result.retryable,
        metadata=metadata,
    )


def _validate_shadow_tool_result_status(value: object) -> str:
    if not isinstance(value, str):
        _validate_and_freeze_shadow_public_value(value, label="tool result status")
        raise ValueError("tool result status must be a string")
    if value not in _ALLOWED_TOOL_RESULT_STATUSES:
        allowed_values = ", ".join(sorted(_ALLOWED_TOOL_RESULT_STATUSES))
        raise ValueError(f"tool result status must be one of: {allowed_values}")
    return value


def _validate_optional_shadow_tool_result_string(
    value: object,
    *,
    label: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _validate_and_freeze_shadow_public_value(value, label=label)
        raise ValueError(f"{label} must be a string or null")
    sanitized = _validate_and_freeze_shadow_public_value(value, label=label)
    if not isinstance(sanitized, str):
        raise ValueError(f"{label} must be a string or null")
    return sanitized


def _validate_shadow_tool_duration_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _validate_and_freeze_shadow_public_value(
            value,
            label="tool result durationMs",
        )
        raise ValueError("tool result durationMs must be a non-negative integer or null")
    if value < 0:
        raise ValueError("tool result durationMs must be a non-negative integer or null")
    return value


def _validate_shadow_tool_refs(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _validate_and_freeze_shadow_public_value(value, label=label)
        raise ValueError(f"{label} must be a list or tuple of strings")

    refs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _validate_and_freeze_shadow_public_value(item, label=label)
            raise ValueError(f"{label} must contain only strings")
        sanitized = _validate_and_freeze_shadow_public_value(item, label=label)
        if not isinstance(sanitized, str):
            raise ValueError(f"{label} must contain only strings")
        refs.append(sanitized)
    return tuple(refs)


def _validate_shadow_tool_retryable(value: object) -> bool:
    if not isinstance(value, bool):
        _validate_and_freeze_shadow_public_value(
            value,
            label="tool result retryable",
        )
        raise ValueError("tool result retryable must be a boolean")
    return value


def _tool_result_to_json_like(
    result: ToolResult,
    *,
    by_alias: bool,
) -> dict[str, object]:
    llm_output_key = "llmOutput" if by_alias else "llm_output"
    transcript_output_key = "transcriptOutput" if by_alias else "transcript_output"
    error_code_key = "errorCode" if by_alias else "error_code"
    error_message_key = "errorMessage" if by_alias else "error_message"
    duration_ms_key = "durationMs" if by_alias else "duration_ms"
    artifact_refs_key = "artifactRefs" if by_alias else "artifact_refs"
    file_refs_key = "fileRefs" if by_alias else "file_refs"
    delivery_receipts_key = "deliveryReceipts" if by_alias else "delivery_receipts"
    return {
        "status": result.status,
        "output": _thaw_json(result.output),
        llm_output_key: _thaw_json(result.llm_output),
        transcript_output_key: _thaw_json(result.transcript_output),
        error_code_key: result.error_code,
        error_message_key: result.error_message,
        duration_ms_key: result.duration_ms,
        artifact_refs_key: list(result.artifact_refs),
        file_refs_key: list(result.file_refs),
        delivery_receipts_key: list(result.delivery_receipts),
        "retryable": result.retryable,
        "metadata": _thaw_json(result.metadata),
    }
