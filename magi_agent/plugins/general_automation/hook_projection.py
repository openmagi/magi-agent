from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


PluginLifecycleStage = Literal[
    "install",
    "enable",
    "disable",
    "tool_execution",
    "runtime_hook",
    "session_start",
    "session_end",
]
PluginHookProjectionStatus = Literal["projected_metadata", "blocked"]
PluginCallbackName = Literal[
    "before_agent_callback",
    "after_agent_callback",
    "before_model_callback",
    "after_model_callback",
    "before_tool_callback",
    "after_tool_callback",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")


class PluginLifecycleAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    callback_attached: Literal[False] = Field(default=False, alias="callbackAttached")
    plugin_loaded: Literal[False] = Field(default=False, alias="pluginLoaded")
    external_code_executed: Literal[False] = Field(
        default=False,
        alias="externalCodeExecuted",
    )
    mcp_server_attached: Literal[False] = Field(default=False, alias="mcpServerAttached")
    credential_used: Literal[False] = Field(default=False, alias="credentialUsed")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class PluginLifecycleHookProjectionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    plugin_id: str = Field(alias="pluginId")
    lifecycle_stage: PluginLifecycleStage = Field(alias="lifecycleStage")
    callback_names: tuple[PluginCallbackName, ...] = Field(alias="callbackNames")
    policy_ref: str = Field(alias="policyRef")
    protected_runtime_hook: bool = Field(default=False, alias="protectedRuntimeHook")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("plugin_id", "policy_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("callback_names")
    @classmethod
    def _dedupe_callback_names(
        cls,
        value: tuple[PluginCallbackName, ...],
    ) -> tuple[PluginCallbackName, ...]:
        if not value:
            raise ValueError("callbackNames are required")
        return tuple(dict.fromkeys(value))


class PluginLifecycleHookProjection(BaseModel):
    model_config = _MODEL_CONFIG

    status: PluginHookProjectionStatus
    plugin_ref: str = Field(alias="pluginRef")
    lifecycle_stage: PluginLifecycleStage = Field(alias="lifecycleStage")
    callback_names: tuple[PluginCallbackName, ...] = Field(alias="callbackNames")
    hook_refs: tuple[str, ...] = Field(default=(), alias="hookRefs")
    policy_ref: str = Field(alias="policyRef")
    metadata_digest: str = Field(alias="metadataDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: PluginLifecycleAuthorityFlags = Field(
        default_factory=PluginLifecycleAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("plugin_ref", "policy_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("hook_refs")
    @classmethod
    def _validate_hook_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("metadata_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"^sha256:[a-f0-9]{64}$", value):
            raise ValueError("metadataDigest must be sha256")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "pluginRef": self.plugin_ref,
            "lifecycleStage": self.lifecycle_stage,
            "callbackNames": self.callback_names,
            "hookRefs": self.hook_refs,
            "policyRef": self.policy_ref,
            "metadataDigest": self.metadata_digest,
            "reasonCodes": self.reason_codes,
            "adkBoundary": {
                "pluginLifecycle": "plugin lifecycle",
                "callbackVocabulary": "ADK callback",
                "callbackAttached": False,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def project_plugin_lifecycle_hooks(
    request: PluginLifecycleHookProjectionRequest,
) -> PluginLifecycleHookProjection:
    plugin_ref = _plugin_ref(request.plugin_id)
    metadata_digest = _digest(request.metadata)
    if request.protected_runtime_hook:
        return PluginLifecycleHookProjection(
            status="blocked",
            pluginRef=plugin_ref,
            lifecycleStage=request.lifecycle_stage,
            callbackNames=request.callback_names,
            policyRef=request.policy_ref,
            metadataDigest=metadata_digest,
            reasonCodes=("protected_runtime_hook_blocked",),
        )
    return PluginLifecycleHookProjection(
        status="projected_metadata",
        pluginRef=plugin_ref,
        lifecycleStage=request.lifecycle_stage,
        callbackNames=request.callback_names,
        hookRefs=tuple(_hook_ref(request, callback) for callback in request.callback_names),
        policyRef=request.policy_ref,
        metadataDigest=metadata_digest,
        reasonCodes=("plugin_lifecycle_callbacks_metadata_only",),
    )


#: Plugin id under which the Track 19 PR6 constraint-reinjection callback is
#: declared. The callback re-injects the active GA contract's still-unmet
#: required-evidence checklist + open approval_required controls each turn
#: (see ``harness/general_automation/constraint_reinjection``). Declared here as
#: metadata-only — ``callbackAttached`` stays ``False`` (the live flag gate is
#: the activation authority; this projection attaches nothing).
_CONSTRAINT_REINJECTION_PLUGIN_ID = "general-automation-constraint-reinjection"
_CONSTRAINT_REINJECTION_POLICY_REF = (
    "policy:general-automation:constraint-reinjection"
)


def project_constraint_reinjection_callback() -> PluginLifecycleHookProjection:
    """Project the PR6 constraint-reinjection callback as plugin metadata.

    Extends the existing lifecycle-hook projection (it reuses
    :func:`project_plugin_lifecycle_hooks`) to declare the general-automation
    constraint-reinjection callback — the ``before_model_callback`` runtime hook
    that, each turn, re-injects the contract's still-unmet required evidence and
    open ``approval_required`` controls. The projection is metadata-only:
    ``callbackAttached`` remains ``False`` (no authority flag is flipped). The
    live runtime attaches the actual callback only behind the
    ``MAGI_GA_LIVE_ENABLED`` flag gate.
    """
    request = PluginLifecycleHookProjectionRequest(
        pluginId=_CONSTRAINT_REINJECTION_PLUGIN_ID,
        lifecycleStage="runtime_hook",
        callbackNames=("before_model_callback",),
        policyRef=_CONSTRAINT_REINJECTION_POLICY_REF,
        metadata={"reinjection": "required_evidence_and_open_approvals"},
    )
    return project_plugin_lifecycle_hooks(request)


def _plugin_ref(plugin_id: str) -> str:
    return "plugin:general-automation:" + _digest(plugin_id)


def _hook_ref(
    request: PluginLifecycleHookProjectionRequest,
    callback_name: PluginCallbackName,
) -> str:
    return "hook:general-automation:" + _digest(
        {
            "pluginId": request.plugin_id,
            "lifecycleStage": request.lifecycle_stage,
            "callbackName": callback_name,
            "policyRef": request.policy_ref,
        }
    )


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "PluginLifecycleHookProjection",
    "PluginLifecycleHookProjectionRequest",
    "PluginLifecycleAuthorityFlags",
    "project_plugin_lifecycle_hooks",
    "project_constraint_reinjection_callback",
]
