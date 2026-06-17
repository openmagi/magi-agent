from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from magi_agent.customize.tool_perm import matched_decision
from magi_agent.runtime.control import ControlRequest

from .context import ToolContext
from .manifest import RuntimeMode, ToolManifest
from .safety import RuntimePermissionArbiter


PermissionAction = Literal["allow", "deny", "ask"]
APPROVAL_PERMISSION_CLASSES = {"write", "execute", "net"}
APPROVAL_TAGS = {"requires-approval", "approval-required"}


@dataclass(frozen=True)
class ToolPermissionDecision:
    action: PermissionAction
    reason: str
    metadata: dict[str, object]


def base_tool_metadata(
    manifest: ToolManifest,
    *,
    mode: RuntimeMode,
    reason: str,
) -> dict[str, object]:
    return {
        "toolName": manifest.name,
        "permissionClass": manifest.permission,
        "mode": mode,
        "dangerous": manifest.dangerous,
        "mutatesWorkspace": manifest.mutates_workspace,
        "reason": reason,
    }


class ToolPermissionPolicy:
    def __init__(self, runtime_arbiter: RuntimePermissionArbiter | None = None) -> None:
        self.runtime_arbiter = runtime_arbiter or RuntimePermissionArbiter()

    def decide(
        self,
        manifest: ToolManifest,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: RuntimeMode,
    ) -> ToolPermissionDecision:
        if mode not in manifest.available_in_modes:
            return ToolPermissionDecision(
                action="deny",
                reason=f"tool unavailable in {mode} mode",
                metadata=base_tool_metadata(
                    manifest,
                    mode=mode,
                    reason=f"tool unavailable in {mode} mode",
                ),
            )

        safety_decision = self.runtime_arbiter.decide(manifest, arguments, context, mode=mode)
        if safety_decision.action == "deny":
            return ToolPermissionDecision(
                action="deny",
                reason=safety_decision.reason,
                metadata=safety_decision.metadata,
            )
        if safety_decision.action == "ask":
            metadata = dict(safety_decision.metadata)
            if "controlRequest" not in metadata:
                metadata["controlRequest"] = make_control_request(
                    manifest,
                    arguments,
                    context,
                    reason=safety_decision.reason,
                ).model_dump(by_alias=True)
            return ToolPermissionDecision(
                action="ask",
                reason=safety_decision.reason,
                metadata=metadata,
            )

        # Custom tool_perm rules (P2) layer on top of immutable safety: they can
        # deny/ask a call safety would allow, but never loosen a safety deny/ask
        # (handled above). No-op (returns None) unless both customize flags are on.
        custom = matched_decision(tool_name=manifest.name, arguments=arguments)
        if custom is not None:
            custom_action, rule_id = custom
            reason = f"custom rule {rule_id}"
            metadata = base_tool_metadata(manifest, mode=mode, reason=reason)
            metadata["customRuleId"] = rule_id
            if custom_action == "deny":
                return ToolPermissionDecision(action="deny", reason=reason, metadata=metadata)
            metadata["controlRequest"] = make_control_request(
                manifest, arguments, context, reason=reason
            ).model_dump(by_alias=True)
            return ToolPermissionDecision(action="ask", reason=reason, metadata=metadata)

        if safety_decision.metadata.get("policyHandled") is True:
            return ToolPermissionDecision(
                action="allow",
                reason=safety_decision.reason,
                metadata=safety_decision.metadata,
            )

        if bypass_preapproved(context, safety_decision.metadata, mode=mode):
            metadata = dict(safety_decision.metadata)
            metadata["bypassPermissionsPreapproved"] = True
            metadata["reason"] = "bypass permissions preapproved"
            return ToolPermissionDecision(
                action="allow",
                reason="bypass permissions preapproved",
                metadata=metadata,
            )

        if selected_full_toolhost_preapproved(manifest, context, safety_decision.metadata):
            metadata = dict(safety_decision.metadata)
            metadata["selectedFullToolhostPreapproved"] = True
            metadata["reason"] = "selected full toolhost preapproved"
            return ToolPermissionDecision(
                action="allow",
                reason="selected full toolhost preapproved",
                metadata=metadata,
            )

        approval_reason = approval_required_reason(manifest)
        if approval_reason is not None:
            metadata = base_tool_metadata(manifest, mode=mode, reason=approval_reason)
            metadata["controlRequest"] = make_control_request(
                manifest,
                arguments,
                context,
                reason=approval_reason,
            ).model_dump(by_alias=True)
            return ToolPermissionDecision(
                action="ask",
                reason=approval_reason,
                metadata=metadata,
            )

        return ToolPermissionDecision(
            action="allow",
            reason="allowed",
            metadata=base_tool_metadata(manifest, mode=mode, reason="allowed"),
        )


def approval_required_reason(manifest: ToolManifest) -> str | None:
    if manifest.dangerous:
        return "dangerous tool requires approval"
    if manifest.mutates_workspace:
        return "workspace mutation requires approval"
    if manifest.permission in APPROVAL_PERMISSION_CLASSES:
        return f"{manifest.permission} permission requires approval"
    if APPROVAL_TAGS.intersection(manifest.tags):
        return "tool explicitly requires approval"
    return None


def selected_full_toolhost_preapproved(
    manifest: ToolManifest,
    context: ToolContext,
    safety_metadata: dict[str, object],
) -> bool:
    raw_scope = context.permission_scope
    if not isinstance(raw_scope, dict):
        return False
    mode = _scope_token(raw_scope.get("mode") or raw_scope.get("permissionMode"))
    source = _scope_token(raw_scope.get("source"))
    if mode != "selected_full_toolhost" or source != "selected_full_toolhost":
        return False
    if safety_metadata.get("securityPrecheck") != "passed":
        return False
    if APPROVAL_TAGS.intersection(manifest.tags):
        return False
    if manifest.source.kind not in {"builtin", "native-plugin", "runtime", "skill"}:
        return False
    return (
        manifest.permission in APPROVAL_PERMISSION_CLASSES
        or manifest.dangerous
        or manifest.mutates_workspace
    )


def bypass_preapproved(
    context: ToolContext,
    safety_metadata: dict[str, object],
    *,
    mode: RuntimeMode,
) -> bool:
    if mode != "act":
        return False
    raw_scope = context.permission_scope
    if not isinstance(raw_scope, dict):
        return False
    scope_mode = _scope_token(raw_scope.get("mode") or raw_scope.get("permissionMode"))
    if scope_mode != "bypass":
        return False
    return safety_metadata.get("securityPrecheck") == "passed"


def _scope_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_")


def make_control_request(
    manifest: ToolManifest,
    arguments: dict[str, object],
    context: ToolContext,
    *,
    reason: str,
) -> ControlRequest:
    turn_id = context.turn_id or "unknown-turn"
    return ControlRequest(
        request_id=f"tool-permission:{turn_id}:{manifest.name}:{uuid4().hex}",
        turn_id=turn_id,
        tool_name=manifest.name,
        arguments=arguments,
        reason=reason,
    )
