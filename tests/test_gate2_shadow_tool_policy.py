from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import pytest
from pydantic_core import PydanticSerializationError

from magi_agent.shadow.tool_policy import (
    Gate2ShadowToolOutputFlags,
    Gate2ShadowToolPolicyError,
    Gate2ShadowToolReport,
    run_gate2_recorded_tool_output,
    run_gate2_synthetic_local_tool,
)
from magi_agent.tools import ToolDispatcher, ToolRegistry, ToolResult, ToolSource
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest


class SpyDispatcher(ToolDispatcher):
    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__(registry)
        self.dispatch_called = False

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: Literal["plan", "act"],
    ) -> ToolResult:
        self.dispatch_called = True
        return await super().dispatch(name, arguments, context, mode=mode)


def make_manifest(
    name: str = "SafeRead",
    *,
    kind: str = "core",
    source_kind: str = "builtin",
    permission: str = "read",
    modes: tuple[str, ...] = ("plan", "act"),
    dangerous: bool = False,
    mutates_workspace: bool = False,
    side_effect_class: str | None = None,
    enabled_by_default: bool = True,
    tags: tuple[str, ...] = (),
    capability_tags: tuple[str, ...] = (),
) -> ToolManifest:
    metadata: dict[str, object] = {}
    if side_effect_class is not None:
        metadata["sideEffectClass"] = side_effect_class
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind=kind,
        source=ToolSource(kind=source_kind, package="tests.tools"),
        permission=permission,
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=1_000,
        available_in_modes=modes,
        dangerous=dangerous,
        mutates_workspace=mutates_workspace,
        enabled_by_default=enabled_by_default,
        tags=tags,
        capability_tags=capability_tags,
        **metadata,
    )


def make_context(workspace_root: str | None) -> ToolContext:
    return ToolContext(
        bot_id="gate2-shadow-bot",
        turn_id="gate2-shadow-turn",
        workspace_root=workspace_root,
    )


def assert_output_flags_false(report: object) -> None:
    output_flags = getattr(report, "output_flags")
    dumped = output_flags.model_dump(by_alias=True)
    assert dumped
    assert set(dumped.values()) == {False}


def make_direct_report(
    *,
    output_flags: Gate2ShadowToolOutputFlags | None = None,
    diagnostic_metadata: Mapping[str, object] | None = None,
) -> Gate2ShadowToolReport:
    return Gate2ShadowToolReport(
        shadow_mode="recorded_output",
        tool_name="DirectReportSafeRead",
        tool_kind="core",
        source_kind="builtin",
        permission_class="read",
        side_effect_class="none",
        mode="act",
        output_flags=output_flags or Gate2ShadowToolOutputFlags(),
        tool_result=ToolResult(status="ok", output={"fixture": "local"}),
        diagnostic_metadata=diagnostic_metadata or {},
    )


def make_constructed_report(
    *,
    output_flags: object | None = None,
    diagnostic_metadata: object | None = None,
    tool_result: ToolResult | None = None,
) -> Gate2ShadowToolReport:
    return Gate2ShadowToolReport.model_construct(
        posture="diagnostic_non_authoritative",
        shadow_mode="recorded_output",
        tool_name="ConstructedReportSafeRead",
        tool_kind="core",
        source_kind="builtin",
        permission_class="read",
        side_effect_class="none",
        mode="act",
        output_flags=output_flags or Gate2ShadowToolOutputFlags(),
        tool_result=tool_result or ToolResult(status="ok", output={"fixture": "local"}),
        diagnostic_metadata=diagnostic_metadata or {},
    )


def make_constructed_tool_result(**updates: object) -> ToolResult:
    data: dict[str, object] = {
        "status": "ok",
        "output": {"fixture": "local"},
        "llm_output": None,
        "transcript_output": None,
        "error_code": None,
        "error_message": None,
        "duration_ms": None,
        "artifact_refs": (),
        "file_refs": (),
        "delivery_receipts": (),
        "retryable": False,
        "metadata": {},
    }
    data.update(updates)
    return ToolResult.model_construct(**data)


def unsafe_private_report_envelope(**updates: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "posture": "diagnostic_non_authoritative",
        "shadow_mode": "recorded_output",
        "tool_name": "ForgedPrivateEnvelopeSafeRead",
        "tool_kind": "core",
        "source_kind": "builtin",
        "permission_class": "read",
        "side_effect_class": "none",
        "mode": "act",
    }
    envelope.update(updates)
    return envelope


def assert_model_dump_rejects_forged_report(report: Gate2ShadowToolReport) -> None:
    with pytest.raises(
        (ValueError, TypeError, PydanticSerializationError),
        match="diagnostic metadata|JSON|shadow report envelope|tool result",
    ):
        report.model_dump(by_alias=True, mode="json")


def test_recorded_output_mode_replays_local_tool_result_without_calling_handler() -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": arguments})

    manifest = make_manifest("RecordedSafeRead")
    recorded_result = ToolResult(
        status="ok",
        output={"fixture": "local"},
        metadata={"source": "unit-fixture"},
    )

    report = run_gate2_recorded_tool_output(
        manifest,
        recorded_result=recorded_result,
        arguments={"query": "diagnostic only"},
        mode="act",
    )

    assert report.shadow_mode == "recorded_output"
    assert report.tool_result == recorded_result
    assert report.diagnostic_metadata["resultScope"] == "diagnostic_metadata_only"
    assert report.diagnostic_metadata["handlerCalled"] is False
    assert "posture" not in report.diagnostic_metadata
    assert "shadowMode" not in report.diagnostic_metadata
    assert "toolName" not in report.diagnostic_metadata
    assert "toolKind" not in report.diagnostic_metadata
    assert_output_flags_false(report)
    assert called is False


@pytest.mark.parametrize(
    "manifest",
    (
        make_manifest("RecordedWriteTool", permission="write"),
        make_manifest("RecordedExecuteTool", permission="execute"),
        make_manifest("RecordedNetTool", permission="net"),
        make_manifest("RecordedDangerousTool", dangerous=True),
        make_manifest(
            "RecordedMutatingTool",
            mutates_workspace=True,
            side_effect_class="local_workspace",
        ),
        make_manifest("RecordedLocalProcessTool", side_effect_class="local_process"),
        make_manifest("RecordedExternalSourceTool", source_kind="external"),
        make_manifest("RecordedExternalKindTool", kind="external"),
    ),
)
def test_recorded_output_rejects_unsafe_manifests(manifest: ToolManifest) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError):
        run_gate2_recorded_tool_output(
            manifest,
            recorded_result=ToolResult(status="ok", output={"unsafe": True}),
            mode="act",
        )


@pytest.mark.parametrize(
    "manifest",
    (
        make_manifest("RecordedCustomKindRead", kind="custom", source_kind="builtin"),
        make_manifest("RecordedCustomPluginRead", kind="core", source_kind="custom-plugin"),
    ),
)
def test_recorded_output_rejects_custom_manifests(manifest: ToolManifest) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="custom"):
        run_gate2_recorded_tool_output(
            manifest,
            recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
            mode="act",
        )


def test_synthetic_local_mode_dispatches_safe_tool_and_preserves_temp_workspace(
    tmp_path: Path,
) -> None:
    calls: list[tuple[dict[str, object], str | None]] = []

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        calls.append((arguments, context.workspace_root))
        return ToolResult(
            status="ok",
            output={
                "echo": arguments["value"],
                "workspaceRoot": context.workspace_root,
            },
        )

    registry = ToolRegistry()
    registry.register(make_manifest("SyntheticSafeRead"), handler=handler)
    dispatcher = SpyDispatcher(registry)
    workspace_root = str(tmp_path / "shadow-local")

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            "SyntheticSafeRead",
            {"value": "hello"},
            make_context(workspace_root),
            mode="act",
        )
    )

    assert dispatcher.dispatch_called is True
    assert report.shadow_mode == "synthetic_local"
    assert report.tool_result.status == "ok"
    assert report.tool_result.output == {
        "echo": "hello",
        "workspaceRoot": workspace_root,
    }
    assert report.diagnostic_metadata["toolHostMediated"] is True
    assert report.diagnostic_metadata["dispatcherOnly"] is True
    assert calls == [({"value": "hello"}, workspace_root)]
    assert_output_flags_false(report)


@pytest.mark.parametrize("permission", ("write", "execute", "net"))
def test_shadow_policy_rejects_write_execute_net_before_dispatch(
    permission: str,
    tmp_path: Path,
) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(
        make_manifest(f"{permission.title()}Tool", permission=permission),
        handler=handler,
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match=permission):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                f"{permission.title()}Tool",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False
    assert called is False


@pytest.mark.parametrize(
    "manifest",
    (
        make_manifest("SyntheticCustomKindRead", kind="custom", source_kind="builtin"),
        make_manifest("SyntheticCustomPluginRead", kind="core", source_kind="custom-plugin"),
    ),
)
def test_synthetic_local_rejects_custom_manifests_before_dispatch(
    manifest: ToolManifest,
    tmp_path: Path,
) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="custom"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                manifest.name,
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False
    assert called is False


@pytest.mark.parametrize(
    "manifest",
    (
        make_manifest("DangerousTool", dangerous=True),
        make_manifest(
            "MutatingTool",
            mutates_workspace=True,
            side_effect_class="local_workspace",
        ),
        make_manifest("LocalProcessTool", side_effect_class="local_process"),
        make_manifest("ExternalEffectTool", side_effect_class="external"),
        make_manifest(
            "LocalAndExternalTool",
            mutates_workspace=True,
            side_effect_class="local_and_external",
        ),
    ),
)
def test_shadow_policy_rejects_dangerous_mutating_and_side_effect_tools_before_dispatch(
    manifest: ToolManifest,
    tmp_path: Path,
) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                manifest.name,
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False
    assert called is False


def test_shadow_policy_rejects_external_kind_before_dispatch(tmp_path: Path) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(
        make_manifest("ExternalKindRead", kind="external"),
        handler=handler,
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="external"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "ExternalKindRead",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False
    assert called is False


def test_shadow_policy_rejects_external_source_before_dispatch(tmp_path: Path) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(
        make_manifest("ExternalSourceRead", source_kind="external"),
        handler=handler,
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="external"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "ExternalSourceRead",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False
    assert called is False


@pytest.mark.parametrize(
    "workspace_root",
    (
        "/Users/kevin/Desktop/openmagi",
        "/data/bots/bot-123/workspace",
        "/workspace/bot-123",
        "/tmp/pvc-bot-123/workspace",
    ),
)
def test_synthetic_local_rejects_non_temp_or_production_workspace_roots(
    workspace_root: str,
) -> None:
    registry = ToolRegistry()
    registry.register(
        make_manifest("WorkspaceSafeRead"),
        handler=lambda _arguments, _context: ToolResult(status="ok"),
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="workspace_root"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "WorkspaceSafeRead",
                {},
                make_context(workspace_root),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False


def test_synthetic_local_rejects_repo_workspace_even_when_tmpdir_points_to_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    poisoned_workspace = repo_root / "shadow-local-temp-poison"
    monkeypatch.setenv("TMPDIR", str(repo_root))

    registry = ToolRegistry()
    registry.register(
        make_manifest("TmpdirPoisonSafeRead"),
        handler=lambda _arguments, _context: ToolResult(status="ok"),
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="workspace_root"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "TmpdirPoisonSafeRead",
                {},
                make_context(str(poisoned_workspace)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False


def test_synthetic_local_sanitizes_live_tool_context_before_dispatch(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        seen.update(
            {
                "sameContext": context is live_context,
                "emitProgress": context.emit_progress,
                "emitAgentEvent": context.emit_agent_event,
                "emitControlEvent": context.emit_control_event,
                "askUser": context.ask_user,
                "commitHandle": context.commit_handle,
                "secretBroker": context.secret_broker,
                "adkContext": context.adk_context,
                "adkToolContext": context.adk_tool_context,
                "sessionKey": context.session_key,
            }
        )
        return ToolResult(status="ok", output={"contextSanitized": True})

    registry = ToolRegistry()
    registry.register(make_manifest("ContextSanitizedSafeRead"), handler=handler)
    dispatcher = SpyDispatcher(registry)
    live_context = ToolContext(
        bot_id="gate2-shadow-bot",
        turn_id="gate2-shadow-turn",
        session_key="live-session-secret",
        workspace_root=str(tmp_path),
        emit_progress=lambda *_args, **_kwargs: None,
        emit_agent_event=lambda *_args, **_kwargs: None,
        emit_control_event=lambda *_args, **_kwargs: None,
        ask_user=lambda *_args, **_kwargs: None,
        commit_handle=object(),
        secret_broker=object(),
        adk_context=object(),
        adk_tool_context=object(),
    )

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            "ContextSanitizedSafeRead",
            {},
            live_context,
            mode="act",
        )
    )

    assert report.tool_result.output == {"contextSanitized": True}
    assert seen == {
        "sameContext": False,
        "emitProgress": None,
        "emitAgentEvent": None,
        "emitControlEvent": None,
        "askUser": None,
        "commitHandle": None,
        "secretBroker": None,
        "adkContext": None,
        "adkToolContext": None,
        "sessionKey": None,
    }


def test_synthetic_local_strips_spawn_workspace_before_dispatch(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(_arguments: dict[str, object], context: ToolContext) -> ToolResult:
        seen["workspaceRoot"] = context.workspace_root
        seen["spawnWorkspace"] = context.spawn_workspace
        return ToolResult(status="ok", output={"spawnWorkspace": context.spawn_workspace})

    registry = ToolRegistry()
    registry.register(make_manifest("SpawnWorkspaceStrippedSafeRead"), handler=handler)
    dispatcher = SpyDispatcher(registry)
    live_context = ToolContext(
        bot_id="gate2-shadow-bot",
        turn_id="gate2-shadow-turn",
        workspace_root=str(tmp_path),
        spawn_workspace="/data/bots/bot-prod/workspace",
    )

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            "SpawnWorkspaceStrippedSafeRead",
            {},
            live_context,
            mode="act",
        )
    )

    assert seen == {"workspaceRoot": str(tmp_path), "spawnWorkspace": None}
    assert report.tool_result.output == {"spawnWorkspace": None}


def test_synthetic_local_preserves_parent_tool_names_through_sanitizer(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def handler(_arguments: dict[str, object], context: ToolContext) -> ToolResult:
        seen["parentToolNames"] = context.parent_tool_names
        return ToolResult(status="ok", output={"parentToolNames": list(context.parent_tool_names)})

    registry = ToolRegistry()
    registry.register(make_manifest("ParentToolNamesSafeRead"), handler=handler)
    dispatcher = SpyDispatcher(registry)
    live_context = ToolContext(
        bot_id="gate2-shadow-bot",
        turn_id="gate2-shadow-turn",
        workspace_root=str(tmp_path),
        parent_tool_names=("FileRead", "Bash"),
    )

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            "ParentToolNamesSafeRead",
            {},
            live_context,
            mode="act",
        )
    )

    assert seen["parentToolNames"] == ("FileRead", "Bash")
    assert report.tool_result.output == {"parentToolNames": ["FileRead", "Bash"]}


@pytest.mark.parametrize(
    "manifest",
    (
        make_manifest("ApprovalRequiredTagRead", tags=("approval-required",)),
        make_manifest("RequiresApprovalTagRead", tags=("requires-approval",)),
        make_manifest("ApprovalRequiredCapabilityMeta", permission="meta", capability_tags=("approval-required",)),
    ),
)
def test_synthetic_local_rejects_approval_required_read_meta_before_dispatch(
    manifest: ToolManifest,
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        manifest,
        handler=lambda _arguments, _context: ToolResult(status="ok", output={"unexpected": True}),
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="approval"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                manifest.name,
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False


def test_synthetic_local_rejects_symlink_to_temp_pvc_workspace_after_realpath(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pvc-bot-123" / "workspace"
    target.mkdir(parents=True)
    link = tmp_path / "shadow-local"
    link.symlink_to(target, target_is_directory=True)

    registry = ToolRegistry()
    registry.register(
        make_manifest("SymlinkWorkspaceSafeRead"),
        handler=lambda _arguments, _context: ToolResult(status="ok"),
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="workspace_root"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "SymlinkWorkspaceSafeRead",
                {},
                make_context(str(link)),
                mode="act",
            )
        )

    assert dispatcher.dispatch_called is False


@pytest.mark.parametrize(
    ("manifest", "dispatch_mode", "expected_status", "expected_reason"),
    (
        (
            make_manifest("DisabledSafeRead", enabled_by_default=False),
            "act",
            "blocked",
            "tool disabled",
        ),
        (
            make_manifest("PlanOnlySafeRead", modes=("plan",)),
            "act",
            "blocked",
            "tool unavailable in act mode",
        ),
    ),
)
def test_disabled_or_unavailable_safe_tools_return_dispatcher_result_after_policy_permits(
    manifest: ToolManifest,
    dispatch_mode: Literal["plan", "act"],
    expected_status: str,
    expected_reason: str,
    tmp_path: Path,
) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": True})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = SpyDispatcher(registry)

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            manifest.name,
            {},
            make_context(str(tmp_path)),
            mode=dispatch_mode,
        )
    )

    assert dispatcher.dispatch_called is True
    assert report.tool_result.status == expected_status
    assert report.tool_result.metadata["reason"] == expected_reason
    assert "toolName" not in report.tool_result.metadata
    assert "toolKind" not in report.tool_result.metadata
    assert "sourceKind" not in report.tool_result.metadata
    assert "permissionClass" not in report.tool_result.metadata
    assert "mode" not in report.tool_result.metadata
    assert called is False
    assert_output_flags_false(report)


def test_direct_report_normalizes_model_constructed_output_flags() -> None:
    forged_flags = Gate2ShadowToolOutputFlags.model_construct(
        user_visible=True,
        traffic_attached=True,
    )

    report = make_direct_report(output_flags=forged_flags)

    assert report.output_flags.user_visible is False
    assert report.output_flags.traffic_attached is False
    assert_output_flags_false(report)


def test_direct_report_normalizes_object_setattr_mutated_output_flags() -> None:
    forged_flags = Gate2ShadowToolOutputFlags()
    object.__setattr__(forged_flags, "user_visible", True)
    object.__setattr__(forged_flags, "production_attached", True)

    report = make_direct_report(output_flags=forged_flags)

    assert report.output_flags.user_visible is False
    assert report.output_flags.production_attached is False
    assert_output_flags_false(report)


def test_model_constructed_report_does_not_expose_forged_output_flags() -> None:
    forged_flags = Gate2ShadowToolOutputFlags.model_construct(
        user_visible=True,
        traffic_attached=True,
        production_attached=True,
    )

    report = make_constructed_report(output_flags=forged_flags)

    assert report.output_flags.user_visible is False
    assert report.output_flags.traffic_attached is False
    assert report.output_flags.production_attached is False
    assert report.model_dump(by_alias=True, mode="json")["outputFlags"] == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }


def test_report_output_flags_remain_false_after_post_return_raw_state_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedOutputFlagsSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )
    forged_flags = Gate2ShadowToolOutputFlags.model_construct(
        user_visible=True,
        network_sse=True,
        traffic_attached=True,
    )

    object.__setattr__(report, "output_flags", forged_flags)

    assert report.output_flags.user_visible is False
    assert report.output_flags.network_sse is False
    assert report.output_flags.traffic_attached is False
    assert report.model_dump(by_alias=True, mode="json")["outputFlags"] == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("posture", "production_authoritative"),
        ("shadow_mode", "live_capture"),
        ("tool_kind", "external"),
        ("source_kind", "external"),
        ("permission_class", "write"),
        ("side_effect_class", "external"),
        ("mode", "live_capture"),
    ),
)
def test_model_constructed_report_rejects_forged_public_envelope_fields(
    field_name: str,
    forged_value: object,
) -> None:
    data: dict[str, object] = {
        "posture": "diagnostic_non_authoritative",
        "shadow_mode": "recorded_output",
        "tool_name": "ConstructedEnvelopeSafeRead",
        "tool_kind": "core",
        "source_kind": "builtin",
        "permission_class": "read",
        "side_effect_class": "none",
        "mode": "act",
        "output_flags": Gate2ShadowToolOutputFlags(),
        "tool_result": ToolResult(status="ok", output={"fixture": "local"}),
        "diagnostic_metadata": {},
    }
    data[field_name] = forged_value
    report = Gate2ShadowToolReport.model_construct(**data)

    with pytest.raises(ValueError, match="shadow report envelope"):
        _ = getattr(report, field_name)
    assert_model_dump_rejects_forged_report(report)


def test_report_serializes_canonical_envelope_after_post_return_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedEnvelopeSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(report, "posture", "production_authoritative")
    object.__setattr__(report, "shadow_mode", "live_capture")
    object.__setattr__(report, "tool_kind", "external")
    object.__setattr__(report, "source_kind", "external")
    object.__setattr__(report, "permission_class", "write")
    object.__setattr__(report, "side_effect_class", "external")
    object.__setattr__(report, "mode", "live_capture")

    dumped = report.model_dump(by_alias=True, mode="json")

    assert report.posture == "diagnostic_non_authoritative"
    assert report.shadow_mode == "recorded_output"
    assert report.tool_kind == "core"
    assert report.source_kind == "builtin"
    assert report.permission_class == "read"
    assert report.side_effect_class == "none"
    assert report.mode == "act"
    assert dumped["posture"] == "diagnostic_non_authoritative"
    assert dumped["shadowMode"] == "recorded_output"
    assert dumped["toolKind"] == "core"
    assert dumped["sourceKind"] == "builtin"
    assert dumped["permissionClass"] == "read"
    assert dumped["sideEffectClass"] == "none"
    assert dumped["mode"] == "act"


def test_model_constructed_report_ignores_private_envelope_when_public_fields_are_safe() -> None:
    report = make_constructed_report()
    report = report.model_construct(
        **report.__dict__,
        _canonical_envelope=unsafe_private_report_envelope(
            tool_kind="external",
        ),
    )

    assert report.tool_kind == "core"
    assert report.permission_class == "read"
    assert report.model_dump(by_alias=True, mode="json")["toolKind"] == "core"


def test_model_constructed_report_ignores_safe_private_envelope_and_rejects_unsafe_public_fields() -> None:
    report = Gate2ShadowToolReport.model_construct(
        posture="diagnostic_non_authoritative",
        shadow_mode="recorded_output",
        tool_name="UnsafePublicEnvelopeSafeRead",
        tool_kind="external",
        source_kind="external",
        permission_class="write",
        side_effect_class="external",
        mode="act",
        output_flags=Gate2ShadowToolOutputFlags(),
        tool_result=ToolResult(status="ok", output={"fixture": "local"}),
        diagnostic_metadata={},
        _canonical_envelope=unsafe_private_report_envelope(
            tool_name="ForgedSafeRead",
            tool_kind="core",
            source_kind="builtin",
            permission_class="read",
        ),
    )

    with pytest.raises(ValueError, match="shadow report envelope"):
        _ = report.tool_kind
    assert_model_dump_rejects_forged_report(report)


def test_report_ignores_raw_private_canonical_envelope_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawPrivateEnvelopeSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(
        report,
        "__pydantic_private__",
        {
            "_canonical_envelope": unsafe_private_report_envelope(
                tool_name="ForgedSafeRead",
                tool_kind="core",
                source_kind="builtin",
                permission_class="read",
            )
        },
    )

    dumped = report.model_dump(by_alias=True, mode="json")

    assert report.tool_name == "RawPrivateEnvelopeSafeRead"
    assert report.tool_kind == "core"
    assert report.source_kind == "builtin"
    assert report.permission_class == "read"
    assert dumped["toolName"] == "RawPrivateEnvelopeSafeRead"
    assert dumped["toolKind"] == "core"
    assert dumped["sourceKind"] == "builtin"
    assert dumped["permissionClass"] == "read"


def test_report_diagnostic_metadata_is_deeply_frozen() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("FrozenDiagnosticRead"),
        recorded_result=ToolResult(
            status="ok",
            output={"nested": {"safe": True}},
            metadata={"nested": {"safe": True}},
        ),
        mode="act",
    )

    with pytest.raises(TypeError):
        report.diagnostic_metadata["productionAuthority"] = True  # type: ignore[index]

    recorded_result = report.diagnostic_metadata["recordedToolResult"]
    assert isinstance(recorded_result, Mapping)
    with pytest.raises(TypeError):
        recorded_result["metadata"] = {"productionAuthority": True}  # type: ignore[index]

    recorded_metadata = recorded_result["metadata"]
    assert isinstance(recorded_metadata, Mapping)
    with pytest.raises(TypeError):
        recorded_metadata["productionAuthority"] = True  # type: ignore[index]

    assert report.diagnostic_metadata["productionAuthority"] is False


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"productionAuthority": True},
        {"outputScope": "production_transcript"},
        {"trafficAttached": True},
        {"productionAttached": True},
        {"userVisible": True},
        {"productionTranscriptAppend": True},
        {"nested": {"productionAuthority": True}},
    ),
)
def test_direct_report_rejects_forged_reserved_diagnostic_metadata(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"telegramAttached": True},
        {"apiRouteAttached": True},
        {"dashboardRouteAttached": True},
        {"runtimeSelector": "production"},
        {"tsRuntimeAuthoritative": True},
        {"nested": {"Telegram-Attached": True}},
        {"items": [{"runtime_selector": "typescript"}]},
    ),
)
def test_direct_report_rejects_runtime_route_telegram_authority_claims_in_diagnostic_metadata(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"telegram": True},
        {"api": True},
        {"apiRoute": True},
        {"dashboard": True},
        {"dashboardRoute": True},
        {"route": True},
        {"Route": True},
        {"tsRuntime": "attached"},
        {"ts_runtime": "attached"},
        {"TSRuntime": "attached"},
        {"typescriptRuntime": "attached"},
        {"productionRoute": True},
        {"canary": True},
        {"traffic": True},
        {"production": True},
        {"nested": {"TypeScript-Runtime": "attached"}},
        {"items": [{"production_route": True}]},
    ),
)
def test_direct_report_rejects_bare_runtime_route_authority_claims_in_diagnostic_metadata(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"shadowMode": "recorded_output"},
        {"toolKind": "core"},
        {"sourceKind": "builtin"},
        {"permissionClass": "read"},
        {"sideEffectClass": "none"},
        {"mode": "act"},
        {"nested": {"tool-kind": "core"}},
        {"metadata": [{"source_kind": "builtin"}]},
    ),
)
def test_direct_report_rejects_report_envelope_claims_in_diagnostic_metadata(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


def test_model_constructed_report_rejects_forged_diagnostic_metadata_on_access_and_dump() -> None:
    report = make_constructed_report(
        diagnostic_metadata={"ProductionAuthority": True},
    )

    with pytest.raises(ValueError, match="diagnostic metadata"):
        _ = report.diagnostic_metadata
    assert_model_dump_rejects_forged_report(report)


def test_report_rejects_post_return_diagnostic_metadata_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedMetadataSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(report, "diagnostic_metadata", {"trafficAttached": True})

    with pytest.raises(ValueError, match="diagnostic metadata"):
        _ = report.diagnostic_metadata
    assert_model_dump_rejects_forged_report(report)


def test_report_rejects_post_return_diagnostic_metadata_envelope_key_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedEnvelopeMetadataSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(report, "diagnostic_metadata", {"toolKind": "core"})

    with pytest.raises(ValueError, match="diagnostic metadata"):
        _ = report.diagnostic_metadata
    assert_model_dump_rejects_forged_report(report)


@pytest.mark.parametrize(
    "arguments",
    (
        {"toolKind": "core"},
        {"metadata": {"sourceKind": "builtin"}},
        {"nested": [{"permission_class": "read"}]},
    ),
)
def test_recorded_output_rejects_report_envelope_claims_in_recorded_arguments(
    arguments: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedArgumentEnvelopeClaimSafeRead"),
            recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
            arguments=arguments,
            mode="act",
        )


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"outputFlags": {}},
        {"nested": {"output_flags": {}}},
        {"items": [{"outputFlags": {}}]},
    ),
)
def test_direct_report_rejects_output_flags_claims_in_diagnostic_metadata(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


@pytest.mark.parametrize(
    "arguments",
    (
        {"outputFlags": {}},
        {"nested": {"output_flags": {}}},
        {"items": [{"outputFlags": {}}]},
    ),
)
def test_recorded_output_rejects_output_flags_claims_in_recorded_arguments(
    arguments: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedArgumentOutputFlagsClaimSafeRead"),
            recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
            arguments=arguments,
            mode="act",
        )


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"ProductionAuthority": True},
        {"traffic-attached": True},
        {"OutputScope": "production_transcript"},
        {"user-visible": True},
        {"production_transcript_append": True},
        {"nested": {"adk-runner-attached": True}},
    ),
)
def test_direct_report_rejects_normalized_reserved_diagnostic_metadata_variants(
    diagnostic_metadata: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="diagnostic metadata"):
        make_direct_report(diagnostic_metadata=diagnostic_metadata)


@pytest.mark.parametrize(
    "diagnostic_metadata",
    (
        {"raw": {1, 2}},
        {"raw": b"bytes"},
        {"raw": object()},
        {1: "bad-key"},
        {"nested": [{"raw": object()}]},
        {"number": float("nan")},
    ),
)
def test_direct_report_rejects_non_json_like_diagnostic_metadata(
    diagnostic_metadata: Mapping[object, object],
) -> None:
    with pytest.raises(ValueError, match="JSON|diagnostic metadata|valid string"):
        Gate2ShadowToolReport(
            shadow_mode="recorded_output",
            tool_name="NonJsonMetadataSafeRead",
            tool_kind="core",
            source_kind="builtin",
            permission_class="read",
            side_effect_class="none",
            mode="act",
            tool_result=ToolResult(status="ok", output={"fixture": "local"}),
            diagnostic_metadata=diagnostic_metadata,
        )


def test_report_rejects_post_return_non_json_like_diagnostic_metadata_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedNonJsonMetadataSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(report, "diagnostic_metadata", {"raw": {1, 2}})

    with pytest.raises(ValueError, match="JSON|diagnostic metadata"):
        _ = report.diagnostic_metadata
    assert_model_dump_rejects_forged_report(report)


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", metadata={"trafficAttached": True}),
        ToolResult(status="ok", output={"ProductionAuthority": True}),
        ToolResult(status="ok", transcript_output={"user-visible": True}),
        ToolResult(status="ok", llm_output={"OutputScope": "production_transcript"}),
    ),
)
def test_recorded_output_rejects_reserved_claims_in_recorded_tool_result(
    tool_result: ToolResult,
) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedReservedClaimSafeRead"),
            recorded_result=tool_result,
            mode="act",
        )


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", metadata={"trafficAttached": True}),
        ToolResult(status="ok", output={"ProductionAuthority": True}),
        ToolResult(status="ok", transcript_output={"user-visible": True}),
        ToolResult(status="ok", llm_output={"OutputScope": "production_transcript"}),
    ),
)
def test_synthetic_local_rejects_reserved_claims_in_tool_result(
    tool_result: ToolResult,
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        make_manifest("SyntheticReservedClaimSafeRead"),
        handler=lambda _arguments, _context: tool_result,
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "SyntheticReservedClaimSafeRead",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", output={"permissionClass": "write"}),
        ToolResult(status="ok", output={"nested": {"tool-kind": "external"}}),
        ToolResult(status="ok", metadata={"sourceKind": "external"}),
        ToolResult(status="ok", transcript_output={"shadow_mode": "live_capture"}),
        ToolResult(status="ok", llm_output={"Mode": "act"}),
    ),
)
def test_recorded_output_rejects_report_envelope_claims_in_tool_result(
    tool_result: ToolResult,
) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedEnvelopeClaimSafeRead"),
            recorded_result=tool_result,
            mode="act",
        )


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", output={"outputFlags": {}}),
        ToolResult(status="ok", metadata={"nested": {"output_flags": {}}}),
        ToolResult(status="ok", llm_output={"items": [{"outputFlags": {}}]}),
    ),
)
def test_recorded_output_rejects_output_flags_claims_in_tool_result(
    tool_result: ToolResult,
) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedOutputFlagsClaimSafeRead"),
            recorded_result=tool_result,
            mode="act",
        )


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", output={"permissionClass": "write"}),
        ToolResult(status="ok", output={"nested": {"tool-kind": "external"}}),
        ToolResult(status="ok", metadata={"sourceKind": "external"}),
        ToolResult(status="ok", transcript_output={"shadow_mode": "live_capture"}),
        ToolResult(status="ok", llm_output={"Mode": "act"}),
    ),
)
def test_synthetic_local_rejects_report_envelope_claims_in_tool_result(
    tool_result: ToolResult,
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        make_manifest("SyntheticEnvelopeClaimSafeRead"),
        handler=lambda _arguments, _context: tool_result,
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "SyntheticEnvelopeClaimSafeRead",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )


@pytest.mark.parametrize(
    "tool_result",
    (
        ToolResult(status="ok", output={"telegramAttached": True}),
        ToolResult(status="ok", metadata={"apiRouteAttached": True}),
        ToolResult(status="ok", output={"apiRoute": True}),
        ToolResult(status="ok", metadata={"dashboardRoute": True}),
        ToolResult(status="ok", output={"route": "production"}),
        ToolResult(status="ok", transcript_output={"dashboard-route-attached": True}),
        ToolResult(status="ok", output={"tsRuntime": "production"}),
        ToolResult(status="ok", llm_output={"nested": {"runtimeSelector": "prod"}}),
        ToolResult(status="ok", output={"items": [{"ts_runtime_authoritative": True}]}),
    ),
)
def test_recorded_output_rejects_runtime_route_telegram_authority_claims_in_tool_result(
    tool_result: ToolResult,
) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedRuntimeAuthorityClaimSafeRead"),
            recorded_result=tool_result,
            mode="act",
        )


def test_report_serialization_redacts_public_payload_secrets_and_production_paths() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RedactedPayloadSafeRead"),
        recorded_result=ToolResult(
            status="ok",
            output={
                "authorization": "Bearer live-secret-token-value",
                "github": "ghp_1234567890abcdef1234567890abcdef1234",
                "openai": "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
                "workspace": "/tmp/pvc-bot-123/workspace/raw.txt",
            },
            metadata={
                "cookie": "sessionid=super-secret-cookie",
                "SUPABASE_SERVICE_ROLE_KEY": "service-role-secret-value",
            },
        ),
        arguments={
            "OPENAI_API_KEY": "sk-abcdefghijklmnopqrstuvwxyz1234567890",
            "authHeader": "Bearer caller-token",
            "botPath": "/data/bots/bot-abc/workspace",
        },
        mode="act",
    )

    dumped = report.model_dump(by_alias=True, mode="json")
    rendered = json.dumps(dumped, sort_keys=True)

    assert "live-secret-token-value" not in rendered
    assert "ghp_1234567890abcdef1234567890abcdef1234" not in rendered
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890" not in rendered
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in rendered
    assert "super-secret-cookie" not in rendered
    assert "service-role-secret-value" not in rendered
    assert "/tmp/pvc-bot-123/workspace/raw.txt" not in rendered
    assert "/data/bots/bot-abc/workspace" not in rendered
    assert "[REDACTED]" in rendered

    recorded_arguments = report.diagnostic_metadata["recordedArguments"]
    assert isinstance(recorded_arguments, Mapping)
    with pytest.raises(TypeError):
        recorded_arguments["OPENAI_API_KEY"] = "sk-new"  # type: ignore[index]


def test_report_serialization_redacts_secret_and_production_path_mapping_keys() -> None:
    openai_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    github_key = "github_pat_1234567890abcdef1234567890abcdef1234567890"
    workspace_key = "/data/bots/bot-abc/workspace/secret.json"
    report = run_gate2_recorded_tool_output(
        make_manifest("RedactedPayloadKeysSafeRead"),
        recorded_result=ToolResult(
            status="ok",
            output={openai_key: "output-key"},
            metadata={github_key: "metadata-key"},
        ),
        arguments={workspace_key: "argument-key"},
        mode="act",
    )

    dumped = report.model_dump(by_alias=True, mode="json")
    rendered = json.dumps(dumped, sort_keys=True)

    assert openai_key not in rendered
    assert github_key not in rendered
    assert workspace_key not in rendered
    assert "[REDACTED]" in rendered


def test_report_serialization_sanitizes_tool_result_string_and_ref_fields() -> None:
    github_token = "ghp_1234567890abcdef1234567890abcdef1234"
    openai_token = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    long_error = f"failed with Bearer live-error-token {openai_token} " + ("x" * 700)
    report = run_gate2_recorded_tool_output(
        make_manifest("RedactedToolResultRefsSafeRead"),
        recorded_result=ToolResult(
            status="error",
            error_message=long_error,
            artifact_refs=(
                f"artifact://capture?token={github_token}",
                "/tmp/pvc-bot-123/workspace/artifact.log",
            ),
            file_refs=(
                "/data/bots/bot-abc/workspace/raw.txt",
                f"file://local?authorization=Bearer live-file-token",
            ),
            delivery_receipts=(
                f"delivered with {openai_token}",
                "/workspace/bot-abc/receipt.json",
            ),
        ),
        mode="act",
    )

    dumped = report.model_dump(by_alias=True, mode="json")
    tool_result = dumped["toolResult"]
    rendered = json.dumps(tool_result, sort_keys=True)

    assert "live-error-token" not in rendered
    assert github_token not in rendered
    assert openai_token not in rendered
    assert "/tmp/pvc-bot-123/workspace/artifact.log" not in rendered
    assert "/data/bots/bot-abc/workspace/raw.txt" not in rendered
    assert "/workspace/bot-abc/receipt.json" not in rendered
    assert "live-file-token" not in rendered
    assert tool_result["errorMessage"].endswith("...[TRUNCATED]")
    assert "Bearer [REDACTED]" in tool_result["errorMessage"]
    assert "[REDACTED]" in rendered


def test_report_serialization_redacts_secret_like_key_value_strings_in_errors_and_refs() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RedactedKeyValueStringsSafeRead"),
        recorded_result=ToolResult(
            status="error",
            error_message=(
                "failed token=plain-token sessionKey=session-secret "
                "password=pw-secret api_key=api-secret "
                "SUPABASE_SERVICE_ROLE_KEY=service-secret "
                "GITHUB_TOKEN=plain-github COOKIE=session-cookie "
                "SERVICE_ROLE_KEY=role-secret service_role=role-lower "
                "serviceRoleKey=role-camel SECRET=plain-secret "
                "OPENAI_API_KEY=plain-openai"
            ),
            artifact_refs=(
                "artifact://capture?GITHUB_TOKEN=artifact-github&COOKIE=artifact-cookie",
            ),
            file_refs=(
                "file://local?SERVICE_ROLE_KEY=file-role&serviceRoleKey=file-camel",
            ),
            delivery_receipts=(
                "receipt SECRET=receipt-secret OPENAI_API_KEY=receipt-openai",
            ),
        ),
        mode="act",
    )

    dumped = report.model_dump(by_alias=True, mode="json")
    rendered = json.dumps(dumped["toolResult"], sort_keys=True)

    for raw_secret in (
        "plain-token",
        "session-secret",
        "pw-secret",
        "api-secret",
        "service-secret",
        "plain-github",
        "session-cookie",
        "role-secret",
        "role-lower",
        "role-camel",
        "plain-secret",
        "plain-openai",
        "artifact-github",
        "artifact-cookie",
        "file-role",
        "file-camel",
        "receipt-secret",
        "receipt-openai",
    ):
        assert raw_secret not in rendered
    assert "token=[REDACTED]" in rendered
    assert "sessionKey=[REDACTED]" in rendered
    assert "password=[REDACTED]" in rendered
    assert "api_key=[REDACTED]" in rendered
    assert "SUPABASE_SERVICE_ROLE_KEY=[REDACTED]" in rendered
    assert "GITHUB_TOKEN=[REDACTED]" in rendered
    assert "COOKIE=[REDACTED]" in rendered
    assert "SERVICE_ROLE_KEY=[REDACTED]" in rendered
    assert "service_role=[REDACTED]" in rendered
    assert "serviceRoleKey=[REDACTED]" in rendered
    assert "SECRET=[REDACTED]" in rendered
    assert "OPENAI_API_KEY=[REDACTED]" in rendered


def test_model_constructed_report_mapping_surfaces_sanitize_raw_state() -> None:
    report = make_constructed_report(
        output_flags=Gate2ShadowToolOutputFlags.model_construct(
            user_visible=True,
            traffic_attached=True,
            production_attached=True,
        ),
        diagnostic_metadata={
            "authorization": "Bearer raw-metadata-token",
            "note": "SECRET=metadata-secret",
        },
        tool_result=make_constructed_tool_result(
            status="error",
            error_message=(
                "failed Bearer raw-error-token sk-abcdefghijklmnopqrstuvwxyz1234567890 "
                "OPENAI_API_KEY=plain-openai"
            ),
            file_refs=("file://local?authorization=Bearer raw-ref-token",),
            metadata={"cookie": "raw-cookie-secret"},
        ),
    )

    public_dict = report.__dict__
    iter_dict = dict(report)
    rendered = json.dumps({"dict": public_dict, "iter": iter_dict}, default=str, sort_keys=True)

    for raw in (
        "raw-metadata-token",
        "metadata-secret",
        "raw-error-token",
        "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "plain-openai",
        "raw-ref-token",
        "raw-cookie-secret",
    ):
        assert raw not in rendered
    assert public_dict["output_flags"].model_dump(by_alias=True) == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }
    assert iter_dict["output_flags"].model_dump(by_alias=True)["trafficAttached"] is False


def test_report_mapping_surfaces_sanitize_post_return_raw_state_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawMappingSurfaceSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(
        report,
        "output_flags",
        Gate2ShadowToolOutputFlags.model_construct(
            user_visible=True,
            traffic_attached=True,
            production_attached=True,
        ),
    )
    object.__setattr__(
        report,
        "diagnostic_metadata",
        {"authorization": "Bearer replaced-metadata-token"},
    )
    object.__setattr__(
        report,
        "tool_result",
        make_constructed_tool_result(
            status="error",
            error_message="failed SECRET=replaced-error-secret",
            artifact_refs=("artifact://capture?token=replaced-ref-secret",),
        ),
    )

    public_dict = report.__dict__
    iter_dict = dict(report)
    rendered = json.dumps({"dict": public_dict, "iter": iter_dict}, default=str, sort_keys=True)

    for raw in (
        "replaced-metadata-token",
        "replaced-error-secret",
        "replaced-ref-secret",
    ):
        assert raw not in rendered
    assert public_dict["output_flags"].model_dump(by_alias=True)["userVisible"] is False
    assert iter_dict["output_flags"].model_dump(by_alias=True)["productionAttached"] is False


def test_constructed_report_serialization_sanitizes_tool_result_string_and_ref_fields() -> None:
    github_token = "github_pat_1234567890abcdef1234567890abcdef1234567890"
    openai_token = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    report = make_constructed_report(
        tool_result=make_constructed_tool_result(
            status="error",
            error_message=f"blocked Bearer live-constructed-token {openai_token}",
            artifact_refs=(f"artifact://{github_token}",),
            file_refs=("/var/lib/kubelet/pods/bot-abc/secret.txt",),
            delivery_receipts=(f"receipt {openai_token}",),
        ),
    )

    dumped = report.model_dump(by_alias=True, mode="json")
    tool_result = dumped["toolResult"]
    rendered = json.dumps(tool_result, sort_keys=True)

    assert "live-constructed-token" not in rendered
    assert github_token not in rendered
    assert openai_token not in rendered
    assert "/var/lib/kubelet/pods/bot-abc/secret.txt" not in rendered
    assert tool_result["errorMessage"] == "blocked Bearer [REDACTED] [REDACTED]"
    assert tool_result["artifactRefs"] == ["artifact://[REDACTED]"]
    assert tool_result["fileRefs"] == ["[REDACTED]"]
    assert tool_result["deliveryReceipts"] == ["receipt [REDACTED]"]


def test_synthetic_local_freezes_safe_tool_result_public_payload(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        make_manifest("SyntheticFrozenPayloadSafeRead"),
        handler=lambda _arguments, _context: ToolResult(
            status="ok",
            output={"nested": {"items": [1, 2]}},
            metadata={"trafficAttached": False},
        ),
    )
    dispatcher = SpyDispatcher(registry)

    report = asyncio.run(
        run_gate2_synthetic_local_tool(
            dispatcher,
            "SyntheticFrozenPayloadSafeRead",
            {},
            make_context(str(tmp_path)),
            mode="act",
        )
    )

    with pytest.raises(TypeError):
        report.tool_result.metadata["trafficAttached"] = True
    output = report.tool_result.output
    assert isinstance(output, Mapping)
    nested = output["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["trafficAttached"] = True
    assert report.model_dump(by_alias=True, mode="json")["toolResult"]["output"] == {
        "nested": {"items": [1, 2]},
    }


def test_constructed_report_tool_result_direct_access_returns_frozen_safe_result() -> None:
    report = make_constructed_report(
        tool_result=ToolResult(status="ok", output={"nested": {"items": [1, 2]}}),
    )

    safe_result = report.tool_result
    output = safe_result.output
    assert isinstance(output, Mapping)
    nested = output["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["permissionClass"] = "write"


def test_constructed_report_tool_result_direct_access_rejects_unsafe_result() -> None:
    report = make_constructed_report(
        tool_result=ToolResult(status="ok", output={"permissionClass": "write"}),
    )

    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        _ = report.tool_result
    assert_model_dump_rejects_forged_report(report)


def test_report_tool_result_direct_access_rejects_post_return_replacement() -> None:
    report = run_gate2_recorded_tool_output(
        make_manifest("RawReplacedToolResultSafeRead"),
        recorded_result=ToolResult(status="ok", output={"fixture": "local"}),
        mode="act",
    )

    object.__setattr__(
        report,
        "tool_result",
        ToolResult(status="ok", metadata={"sourceKind": "external"}),
    )

    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        _ = report.tool_result
    assert_model_dump_rejects_forged_report(report)


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("status", "production_authoritative"),
        ("error_code", {"ProductionAuthority": True}),
        ("error_message", {"trafficAttached": True}),
        ("duration_ms", -1),
        ("duration_ms", True),
        ("artifact_refs", ({"trafficAttached": True},)),
        ("file_refs", (123,)),
        ("delivery_receipts", ({"ProductionAuthority": True},)),
        ("retryable", "yes"),
    ),
)
def test_recorded_output_rejects_unsafe_public_tool_result_fields(
    field_name: str,
    forged_value: object,
) -> None:
    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        run_gate2_recorded_tool_output(
            make_manifest("RecordedUnsafeToolResultFieldSafeRead"),
            recorded_result=make_constructed_tool_result(**{field_name: forged_value}),
            mode="act",
        )


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("status", "production_authoritative"),
        ("error_code", {"ProductionAuthority": True}),
        ("error_message", {"trafficAttached": True}),
        ("duration_ms", -1),
        ("duration_ms", True),
        ("artifact_refs", ({"trafficAttached": True},)),
        ("file_refs", (123,)),
        ("delivery_receipts", ({"ProductionAuthority": True},)),
        ("retryable", "yes"),
    ),
)
def test_synthetic_local_rejects_unsafe_public_tool_result_fields(
    field_name: str,
    forged_value: object,
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        make_manifest("SyntheticUnsafeToolResultFieldSafeRead"),
        handler=lambda _arguments, _context: make_constructed_tool_result(
            **{field_name: forged_value}
        ),
    )
    dispatcher = SpyDispatcher(registry)

    with pytest.raises(Gate2ShadowToolPolicyError, match="tool result"):
        asyncio.run(
            run_gate2_synthetic_local_tool(
                dispatcher,
                "SyntheticUnsafeToolResultFieldSafeRead",
                {},
                make_context(str(tmp_path)),
                mode="act",
            )
        )


def test_shadow_tool_policy_import_stays_production_runtime_free() -> None:
    script = """
import importlib
import sys

module = importlib.import_module("magi_agent.shadow.tool_policy")
assert hasattr(module, "run_gate2_synthetic_local_tool")

forbidden_exact = (
    "magi_agent.app",
    "magi_agent.main",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.transport.health",
    "magi_agent.transport.plugins",
    "magi_agent.transport.tools",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
)
forbidden_prefixes = (
    "magi_agent.api",
    "magi_agent.proxy",
    "magi_agent.dashboard",
    "magi_agent.database",
    "magi_agent.db",
    "magi_agent.supabase",
    "magi_agent.billing",
    "magi_agent.auth",
    "magi_agent.model_routing",
    "magi_agent.transport.api",
    "magi_agent.transport.proxy",
)
forbidden_terms = (
    "api_proxy",
    "chat_proxy",
    "dashboard",
    "database",
    "supabase",
    "billing",
    "auth",
    "model_routing",
    "model-routing",
    "telegram",
    "k8s",
    "kubernetes",
    "provisioning",
    "deploy",
    "runtime_selector",
    "typescript_runtime",
)
loaded_exact = [name for name in forbidden_exact if name in sys.modules]
loaded_prefixes = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
loaded_terms = [
    name
    for name in sys.modules
    if name.startswith("magi_agent.")
    and any(term in name.lower() for term in forbidden_terms)
]
if loaded_exact or loaded_prefixes or loaded_terms:
    raise AssertionError(
        f"shadow tool policy imported forbidden modules: "
        f"exact={loaded_exact}, prefixes={loaded_prefixes}, terms={loaded_terms}"
    )
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
