from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from magi_agent.credentials_admin.approval_resolver import (
    CredentialApprovalResolver,
    default_credential_approval_resolver,
    extract_egress_host,
)
from magi_agent.customize.tool_perm import matched_decision
from magi_agent.runtime.control import ControlRequest

from .context import ToolContext
from .manifest import RuntimeMode, ToolManifest
from .safety import RuntimePermissionArbiter


PermissionAction = Literal["allow", "deny", "ask"]
APPROVAL_PERMISSION_CLASSES = {"write", "execute", "net", "computer"}
APPROVAL_TAGS = {"requires-approval", "approval-required"}
# Side-effect classes that, for a ``net`` tool, indicate external mutation rather
# than a pure remote read. A net tool tagged with one of these is NOT treated as
# read-only even if it (mis)declares ``parallel_safety="readonly"``.
_EXTERNALLY_MUTATING_SIDE_EFFECTS = {"local_and_external"}

# A tool that carries its OWN enforcement gate (e.g. MemoryWrite: real
# persistence requires ``MAGI_MEMORY_WRITE_ENABLED=1`` AND an injected provider,
# and the write boundary rejects non-declarative facts and is bounded to its own
# MEMORY.md/USER.md). Such a tool may declare this manifest tag so that, under
# the fail-closed default, it auto-allows instead of prompting on every call.
SELF_GATED_TAG = "self-gated"


def is_self_gated_write_tool(manifest: ToolManifest) -> bool:
    """Whether a tool is a narrow, self-gated WRITE eligible to skip approval.

    HARD predicate (the exemption can NEVER be abused by a mis-tagged tool):
    the tool must carry the :data:`SELF_GATED_TAG` AND be a *narrow* write —
    ``permission == "write"`` exactly (so NOT ``execute`` / ``net`` / ``meta``)
    AND ``dangerous is False``. An ``execute`` / ``net`` / ``dangerous`` tool that
    declares the tag still fails this predicate and stays on the approval path.
    The tag only expresses *intent*; this code enforces the bounded blast radius.
    """
    if SELF_GATED_TAG not in manifest.tags:
        return False
    if manifest.dangerous:
        return False
    if manifest.permission != "write":
        return False
    return manifest.permission not in {"execute", "net"}


def is_readonly_net_tool(manifest: ToolManifest) -> bool:
    """Whether a ``net``-permission tool only *reads* remote data.

    Predicate: ``parallel_safety == "readonly"`` (e.g. WebSearch / WebFetch GET).
    The manifest validator already forbids ``readonly`` tools from being
    ``dangerous`` or ``mutates_workspace``, so a readonly classification can never
    coexist with a local mutation/danger flag. As belt-and-suspenders we also
    reject the explicitly external-mutating ``side_effect_class`` value
    (``local_and_external``) so a net WRITE/POST tool that mis-declares itself
    readonly is still sent to approval. A net tool that merely fetches remote
    data (side_effect_class ``none`` or ``external``-read) is read-only; anything
    that causes external side effects is not.
    """
    if manifest.permission != "net":
        return False
    if manifest.parallel_safety != "readonly":
        return False
    if manifest.side_effect_class in _EXTERNALLY_MUTATING_SIDE_EFFECTS:
        return False
    return True


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
    def __init__(
        self,
        runtime_arbiter: RuntimePermissionArbiter | None = None,
        *,
        credential_resolver: CredentialApprovalResolver | None = None,
    ) -> None:
        self.runtime_arbiter = runtime_arbiter or RuntimePermissionArbiter()
        # Resolves whether a tool call's egress host is guarded by a
        # require-approval Agent Vault credential. Inert (null) unless a local
        # vault is enabled, so default deployments are byte-identical.
        self._credential_resolver = (
            credential_resolver
            if credential_resolver is not None
            else default_credential_approval_resolver()
        )

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

        # Agent Vault credential-use approval. If this call would egress to a host
        # guarded by a require-approval credential that has no current grant, ask
        # the user inline (in chat) BEFORE the handler runs. Runs ahead of the
        # bypass/preapproval short-circuits below so an explicit per-credential
        # "require approval" is honored even under bypass/YOLO; inert (returns
        # None) when no local vault / no matching credential. The egress proxy
        # remains the hard backstop.
        credential_decision = self._credential_approval_decision(
            manifest, arguments, context, mode=mode
        )
        if credential_decision is not None:
            return credential_decision

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

    def _credential_approval_decision(
        self,
        manifest: ToolManifest,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: RuntimeMode,
    ) -> ToolPermissionDecision | None:
        """Ask for approval when the call egresses to a guarded credential host.

        Returns an ``ask`` decision (carrying a control request + a non-secret
        ``credentialApproval`` marker the kernel reads to record the grant on
        allow) or None when no approval is needed.
        """
        try:
            host = extract_egress_host(manifest.name, arguments)
            if not host:
                return None
            need = self._credential_resolver.needs_approval(host)
            if need is None:
                return None
            if self._credential_resolver.is_granted(need.credential_id):
                return None
        except Exception:  # noqa: BLE001 - resolver issues must not break the turn
            return None

        reason = (
            f"Use the '{need.label}' credential for {need.service} "
            f"({need.host})? The vault injects the secret; the agent never sees it."
        )
        metadata = base_tool_metadata(manifest, mode=mode, reason=reason)
        metadata["controlRequest"] = make_control_request(
            manifest, arguments, context, reason=reason
        ).model_dump(by_alias=True)
        # Non-secret marker the kernel reads at resume-accept to write the grant.
        metadata["credentialApproval"] = {
            "credentialId": need.credential_id,
            "service": need.service,
            "label": need.label,
            "host": need.host,
        }
        return ToolPermissionDecision(action="ask", reason=reason, metadata=metadata)

    def apply_credential_grant(
        self, metadata: object, *, persistent: bool = False
    ) -> None:
        """Record a credential grant after the user approved its use in chat.

        Reads the ``credentialApproval`` marker placed by
        :meth:`_credential_approval_decision`; no-op when absent. Called by the
        kernel at the approval-resume accept point, before the handler egresses,
        so the egress proxy injects the secret. Never raises.
        """
        if not isinstance(metadata, dict):
            return
        marker = metadata.get("credentialApproval")
        if not isinstance(marker, dict):
            return
        credential_id = marker.get("credentialId")
        if not isinstance(credential_id, str) or not credential_id:
            return
        try:
            self._credential_resolver.grant(credential_id, persistent=persistent)
        except Exception:  # noqa: BLE001 - a grant-write failure must not crash the turn
            return


def approval_required_reason(manifest: ToolManifest) -> str | None:
    if manifest.dangerous:
        return "dangerous tool requires approval"
    # Narrow self-gated WRITE tools (e.g. MemoryWrite) carry their own enforcement
    # gate and a bounded blast radius, so under the fail-closed default they
    # auto-allow instead of prompting every call. The predicate hard-requires
    # ``permission == "write"`` and ``dangerous is False`` — an execute / net /
    # dangerous tool that mis-declares the tag falls through to the checks below
    # and STILL requires approval.
    if is_self_gated_write_tool(manifest):
        return None
    if manifest.mutates_workspace:
        return "workspace mutation requires approval"
    # Read-only ``net`` tools (WebSearch / WebFetch GET) only fetch remote data;
    # under the fail-closed default they auto-allow instead of prompting every
    # call. Net WRITE / side-effecting net tools fall through to the class check
    # below and still require approval.
    if is_readonly_net_tool(manifest):
        return None
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
