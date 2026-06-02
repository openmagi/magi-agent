import asyncio
from copy import deepcopy

import pytest

from openmagi_core_agent.tools import (
    ToolDispatcher,
    ToolRegistry,
    ToolResult,
    ToolSource,
)
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.manifest import ToolManifest


def make_manifest(
    name: str,
    *,
    kind: str = "custom",
    source_kind: str = "custom-plugin",
    permission: str = "read",
    input_schema: dict[str, object] | None = None,
    modes: tuple[str, ...] = ("plan", "act"),
    dangerous: bool = False,
    mutates_workspace: bool = False,
    enabled_by_default: bool | None = None,
    tags: tuple[str, ...] = (),
    **metadata: object,
) -> ToolManifest:
    kwargs: dict[str, object] = {}
    if enabled_by_default is not None:
        kwargs["enabled_by_default"] = enabled_by_default
    schema = deepcopy(input_schema) if input_schema is not None else {"type": "object"}
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind=kind,
        source=ToolSource(kind=source_kind, package="tests.tools"),
        permission=permission,
        input_schema=schema,
        timeout_ms=1_000,
        available_in_modes=modes,
        dangerous=dangerous,
        mutates_workspace=mutates_workspace,
        tags=tags,
        **metadata,
        **kwargs,
    )


def make_context() -> ToolContext:
    return ToolContext(bot_id="bot-1", turn_id="turn-1", workspace_root="/tmp/workspace")


def test_registry_lists_only_deliberately_enabled_tools_by_mode() -> None:
    calls: list[str] = []

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        calls.append(f"{context.bot_id}:{arguments['value']}")
        return ToolResult(status="ok", output={"echo": arguments["value"]})

    registry = ToolRegistry()
    registry.register(make_manifest("SafeRead"), handler=handler)
    registry.register(
        make_manifest("ExplicitRead", enabled_by_default=True),
        handler=handler,
    )
    registry.register(
        make_manifest("ActOnly", permission="meta", modes=("act",)),
        handler=handler,
    )

    assert [tool.name for tool in registry.list_available(mode="plan")] == ["ExplicitRead"]
    assert [tool.name for tool in registry.list_available(mode="act")] == ["ExplicitRead"]
    assert [tool.name for tool in registry.list_all()] == ["ActOnly", "ExplicitRead", "SafeRead"]

    disabled_result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "SafeRead",
            {"value": "blocked"},
            make_context(),
            mode="act",
        )
    )

    assert disabled_result.status == "blocked"
    assert disabled_result.metadata["toolName"] == "SafeRead"
    assert disabled_result.metadata["reason"] == "tool disabled"
    assert calls == []

    registry.enable("SafeRead")
    enabled_result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "SafeRead",
            {"value": "enabled"},
            make_context(),
            mode="act",
        )
    )

    assert enabled_result.status == "ok"
    assert enabled_result.output == {"echo": "enabled"}
    assert calls == ["bot-1:enabled"]


def test_registry_rejects_duplicates_replaces_and_guards_core_builtin_unregister() -> None:
    registry = ToolRegistry()
    registry.register(make_manifest("FileRead", kind="core", source_kind="builtin"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_manifest("FileRead", kind="core", source_kind="builtin"))

    registry.replace(make_manifest("FileRead", kind="core", source_kind="builtin"))
    assert registry.resolve("FileRead").permission == "read"

    with pytest.raises(ValueError, match="core/builtin"):
        registry.unregister("FileRead")

    registry.register(make_manifest("CustomSearch", kind="external", source_kind="external"))
    removed = registry.unregister("CustomSearch")
    assert removed.name == "CustomSearch"
    assert registry.resolve("CustomSearch") is None


@pytest.mark.parametrize(
    "replacement",
    [
        make_manifest(
            "Bash",
            kind="custom",
            source_kind="external",
            permission="execute",
            dangerous=True,
            mutates_workspace=True,
            tags=("requires-approval",),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="external",
            permission="execute",
            dangerous=True,
            mutates_workspace=True,
            tags=("requires-approval",),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="builtin",
            permission="read",
            dangerous=True,
            mutates_workspace=True,
            tags=("requires-approval",),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="builtin",
            permission="execute",
            dangerous=False,
            mutates_workspace=True,
            tags=("requires-approval",),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="builtin",
            permission="execute",
            dangerous=True,
            mutates_workspace=False,
            tags=("requires-approval",),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="builtin",
            permission="execute",
            dangerous=True,
            mutates_workspace=True,
            tags=(),
        ),
        make_manifest(
            "Bash",
            kind="core",
            source_kind="builtin",
            permission="execute",
            dangerous=True,
            mutates_workspace=True,
            tags=("requires-approval",),
            modes=("plan", "act"),
        ),
    ],
)
def test_registry_rejects_protected_replacement_metadata_downgrade(
    replacement: ToolManifest,
) -> None:
    registry = ToolRegistry()
    original = make_manifest(
        "Bash",
        kind="core",
        source_kind="builtin",
        permission="execute",
        dangerous=True,
        mutates_workspace=True,
        tags=("requires-approval",),
        modes=("act",),
    )
    registry.register(original)

    with pytest.raises(ValueError, match="cannot downgrade protected tool metadata"):
        registry.replace(replacement)

    with pytest.raises(ValueError, match="core/builtin"):
        registry.unregister("Bash")

    stored = registry.resolve("Bash")
    assert stored == original


def test_registry_rejects_protected_preserved_handler_downgrade_bypass() -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"ran": True})

    registry = ToolRegistry()
    registry.register(
        make_manifest(
            "FileWrite",
            kind="core",
            source_kind="builtin",
            permission="write",
            mutates_workspace=True,
            modes=("act",),
        ),
        handler=handler,
    )
    registry.enable("FileWrite")

    with pytest.raises(ValueError, match="cannot downgrade protected tool metadata"):
        registry.replace(
            make_manifest(
                "FileWrite",
                kind="custom",
                source_kind="external",
                permission="read",
                mutates_workspace=False,
                modes=("plan", "act"),
            )
        )

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileWrite",
            {"path": "README.md", "content": "changed"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "needs_approval"
    assert result.metadata["permissionClass"] == "write"
    assert result.metadata["mutatesWorkspace"] is True
    assert called is False


def test_registry_rejects_protected_replacement_dropping_deterministic_evidence_metadata() -> None:
    registry = ToolRegistry()
    original = make_manifest(
        "Clock",
        kind="core",
        source_kind="builtin",
        emitsEvidenceTypes=("Clock",),
        deterministicRequirementTypes=("clock",),
        canSatisfyDeterministicRequirement=True,
        parallelSafety="readonly",
        isConcurrencySafe=True,
    )
    registry.register(original)

    with pytest.raises(ValueError, match="cannot downgrade protected tool metadata"):
        registry.replace(
            make_manifest(
                "Clock",
                kind="core",
                source_kind="builtin",
                parallelSafety="readonly",
                isConcurrencySafe=True,
            )
        )

    stored = registry.resolve("Clock")
    assert stored == original


@pytest.mark.parametrize(
    ("field_name", "replacement_metadata"),
    (
        ("emitsEvidenceTypes", {"emitsEvidenceTypes": ("Clock",)}),
        (
            "deterministicRequirementTypes",
            {
                "emitsEvidenceTypes": ("Clock",),
                "deterministicRequirementTypes": ("clock",),
                "canSatisfyDeterministicRequirement": True,
            },
        ),
        (
            "canSatisfyDeterministicRequirement",
            {
                "emitsEvidenceTypes": ("Clock",),
                "deterministicRequirementTypes": ("clock",),
                "canSatisfyDeterministicRequirement": True,
            },
        ),
        ("capabilityTags", {"capabilityTags": ("time-source",)}),
    ),
)
def test_registry_rejects_protected_replacement_deterministic_evidence_overclaims(
    field_name: str,
    replacement_metadata: dict[str, object],
) -> None:
    registry = ToolRegistry()
    original = make_manifest(
        "Clock",
        kind="core",
        source_kind="builtin",
        emitsEvidenceTypes=(),
        deterministicRequirementTypes=(),
        canSatisfyDeterministicRequirement=False,
        capabilityTags=(),
    )
    registry.register(original)

    with pytest.raises(ValueError, match=field_name):
        registry.replace(
            make_manifest(
                "Clock",
                kind="core",
                source_kind="builtin",
                **replacement_metadata,
            )
        )

    stored = registry.resolve("Clock")
    assert stored == original


def test_registry_rejects_protected_replacement_weakening_parallel_safety() -> None:
    registry = ToolRegistry()
    original = make_manifest(
        "FileRead",
        kind="core",
        source_kind="builtin",
        parallelSafety="readonly",
        isConcurrencySafe=True,
    )
    registry.register(original)

    with pytest.raises(ValueError, match="cannot downgrade protected tool metadata"):
        registry.replace(
            make_manifest(
                "FileRead",
                kind="core",
                source_kind="builtin",
                parallelSafety="unsafe",
            )
        )

    stored = registry.resolve("FileRead")
    assert stored == original


@pytest.mark.parametrize("parallel_safety", ("readonly", "concurrency_safe"))
def test_registry_rejects_protected_replacement_overclaiming_parallel_safety(
    parallel_safety: str,
) -> None:
    registry = ToolRegistry()
    original = make_manifest(
        "ProcessInspect",
        kind="core",
        source_kind="builtin",
        parallelSafety="unsafe",
        isConcurrencySafe=False,
    )
    registry.register(original)

    with pytest.raises(ValueError, match="cannot downgrade protected tool metadata"):
        registry.replace(
            make_manifest(
                "ProcessInspect",
                kind="core",
                source_kind="builtin",
                parallelSafety=parallel_safety,
                isConcurrencySafe=False,
            )
        )

    stored = registry.resolve("ProcessInspect")
    assert stored == original


def test_registry_preserves_protected_handler_when_metadata_replaced_identically() -> None:
    def original_handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"handler": "original", "path": arguments["path"]})

    def replacement_handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"handler": "replacement", "path": arguments["path"]})

    registry = ToolRegistry()
    manifest = make_manifest(
        "FileRead",
        kind="core",
        source_kind="builtin",
        permission="read",
        modes=("act",),
    )
    registry.register(manifest, handler=original_handler)
    registry.enable("FileRead")

    registry.replace(manifest.model_copy(deep=True), handler=replacement_handler)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileRead",
            {"path": "README.md"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert result.output == {"handler": "original", "path": "README.md"}


def test_registry_keeps_non_protected_replacement_behavior_intact() -> None:
    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"value": arguments["value"]})

    registry = ToolRegistry()
    registry.register(
        make_manifest(
            "CustomWrite",
            permission="write",
            mutates_workspace=True,
            modes=("act",),
        ),
        handler=handler,
    )
    registry.enable("CustomWrite")

    registry.replace(
        make_manifest(
            "CustomWrite",
            kind="external",
            source_kind="external",
            permission="read",
            mutates_workspace=False,
            modes=("plan", "act"),
        )
    )

    stored = registry.resolve("CustomWrite")
    assert stored is not None
    assert stored.kind == "external"
    assert stored.source.kind == "external"
    assert stored.permission == "read"
    assert stored.mutates_workspace is False

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "CustomWrite",
            {"value": "preserved"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert result.output == {"value": "preserved"}


def test_register_stores_defensive_manifest_copy() -> None:
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    expected_schema = deepcopy(input_schema)
    manifest = make_manifest("CallerOwnedRegister", input_schema=input_schema)
    registry = ToolRegistry()

    registry.register(manifest)
    manifest.input_schema["callerMutation"] = {"nested": True}
    manifest.input_schema["properties"]["path"]["type"] = "integer"  # type: ignore[index]

    stored = registry.resolve("CallerOwnedRegister")
    assert stored is not None
    assert stored.input_schema == expected_schema


def test_replace_stores_defensive_manifest_copy() -> None:
    replacement_schema: dict[str, object] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    expected_schema = deepcopy(replacement_schema)
    replacement = make_manifest("CallerOwnedReplace", input_schema=replacement_schema)
    registry = ToolRegistry()
    registry.register(make_manifest("CallerOwnedReplace"))

    registry.replace(replacement)
    replacement.input_schema["callerMutation"] = {"nested": True}
    replacement.input_schema["properties"]["query"]["type"] = "integer"  # type: ignore[index]

    stored = registry.resolve("CallerOwnedReplace")
    assert stored is not None
    assert stored.input_schema == expected_schema


def test_resolve_registration_returns_defensive_manifest_copy() -> None:
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    expected_schema = deepcopy(input_schema)
    registry = ToolRegistry()
    registry.register(make_manifest("RegistrationCopy", input_schema=input_schema))

    registration = registry.resolve_registration("RegistrationCopy")
    assert registration is not None
    registration.manifest.input_schema["callerMutation"] = {"nested": True}
    registration.manifest.input_schema["properties"]["path"]["type"] = "integer"  # type: ignore[index]

    stored = registry.resolve("RegistrationCopy")
    assert stored is not None
    assert stored.input_schema == expected_schema


@pytest.mark.parametrize(
    "reader_name",
    ("resolve", "resolve_enabled", "list_all", "list_available"),
)
def test_registry_public_read_methods_return_defensive_manifest_copies(reader_name: str) -> None:
    original_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    expected_schema = deepcopy(original_schema)
    registry = ToolRegistry()
    registry.register(
        make_manifest(
            "MutableSchemaTool",
            input_schema=original_schema,
            enabled_by_default=True,
            modes=("plan", "act"),
        )
    )
    registration = registry.resolve_registration("MutableSchemaTool")
    assert registration is not None

    if reader_name == "resolve":
        returned = registry.resolve("MutableSchemaTool")
    elif reader_name == "resolve_enabled":
        returned = registry.resolve_enabled("MutableSchemaTool")
    elif reader_name == "list_all":
        returned = next(tool for tool in registry.list_all() if tool.name == "MutableSchemaTool")
    else:
        returned = next(
            tool for tool in registry.list_available(mode="act") if tool.name == "MutableSchemaTool"
        )

    assert returned is not None
    assert returned is not registration.manifest
    assert returned.input_schema is not registration.manifest.input_schema

    returned.input_schema["callerMutation"] = {"nested": True}
    returned.input_schema["properties"]["path"]["type"] = "integer"  # type: ignore[index]

    assert registration.manifest.input_schema == expected_schema
    fresh = registry.resolve("MutableSchemaTool")
    assert fresh is not None
    assert fresh.input_schema == expected_schema


def test_plan_mode_blocks_act_only_tools_before_execution() -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok")

    registry = ToolRegistry()
    registry.register(make_manifest("ActOnly", modes=("act",)), handler=handler)
    registry.enable("ActOnly")

    result = asyncio.run(
        ToolDispatcher(registry).dispatch("ActOnly", {}, make_context(), mode="plan")
    )

    assert result.status == "blocked"
    assert result.metadata == {
        "toolName": "ActOnly",
        "permissionClass": "read",
        "mode": "plan",
        "dangerous": False,
        "mutatesWorkspace": False,
        "reason": "tool unavailable in plan mode",
    }
    assert called is False


def test_dispatcher_blocks_invalid_schema_before_permission_or_handler() -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        _ = arguments, context
        called = True
        return ToolResult(status="ok")

    registry = ToolRegistry()
    manifest = make_manifest(
        "StrictWrite",
        permission="write",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    registry.register(manifest, handler=handler)
    registry.enable(manifest.name)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            manifest.name,
            {"path": "README.md", "unexpected": "blocked"},
            make_context(),
            mode="act",
        )
    )

    dumped = str(result.model_dump(by_alias=True))
    assert result.status == "blocked"
    assert result.error_code == "tool_input_schema_invalid"
    assert "schema_additional_property_blocked" in dumped
    assert "unexpected" not in dumped
    assert "controlRequest" not in dumped
    assert called is False


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        (make_manifest("WriteTool", permission="write"), "write permission requires approval"),
        (make_manifest("ExecTool", permission="execute"), "execute permission requires approval"),
        (make_manifest("NetTool", permission="net"), "net permission requires approval"),
        (make_manifest("DangerTool", dangerous=True), "dangerous tool requires approval"),
        (make_manifest("MutatingTool", mutates_workspace=True), "workspace mutation requires approval"),
        (make_manifest("TaggedApprovalTool", tags=("requires-approval",)), "tool explicitly requires approval"),
    ],
)
def test_approval_required_tools_return_control_request_metadata_without_execution(
    manifest: ToolManifest,
    reason: str,
) -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok")

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    registry.enable(manifest.name)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            manifest.name,
            {"path": "README.md"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "needs_approval"
    assert result.metadata["toolName"] == manifest.name
    assert result.metadata["permissionClass"] == manifest.permission
    assert result.metadata["mode"] == "act"
    assert result.metadata["dangerous"] == manifest.dangerous
    assert result.metadata["mutatesWorkspace"] == manifest.mutates_workspace
    assert result.metadata["reason"] == reason
    control_request = result.metadata["controlRequest"]
    assert control_request == {
        "requestId": control_request["requestId"],
        "turnId": "turn-1",
        "toolName": manifest.name,
        "arguments": {"path": "README.md"},
        "reason": reason,
    }
    assert control_request["requestId"].startswith(f"tool-permission:turn-1:{manifest.name}:")
    assert "README.md" not in control_request["requestId"]
    assert called is False


def test_repeated_same_tool_approval_requests_in_one_turn_get_distinct_ids() -> None:
    registry = ToolRegistry()
    manifest = make_manifest("WriteTool", permission="write")
    registry.register(manifest, handler=lambda _arguments, _context: ToolResult(status="ok"))
    registry.enable(manifest.name)
    dispatcher = ToolDispatcher(registry)
    context = make_context()

    first = asyncio.run(dispatcher.dispatch("WriteTool", {"path": "a.txt"}, context, mode="act"))
    second = asyncio.run(dispatcher.dispatch("WriteTool", {"path": "b.txt"}, context, mode="act"))

    assert first.status == "needs_approval"
    assert second.status == "needs_approval"
    first_id = first.metadata["controlRequest"]["requestId"]
    second_id = second.metadata["controlRequest"]["requestId"]
    assert first_id != second_id
    assert first_id.startswith("tool-permission:turn-1:WriteTool:")
    assert second_id.startswith("tool-permission:turn-1:WriteTool:")
    assert "a.txt" not in first_id
    assert "b.txt" not in second_id


def test_unknown_or_missing_handler_returns_structured_error_without_execution() -> None:
    registry = ToolRegistry()
    dispatcher = ToolDispatcher(registry)

    unknown = asyncio.run(dispatcher.dispatch("MissingTool", {}, make_context(), mode="act"))
    assert unknown.status == "error"
    assert unknown.error == "tool not found"
    assert unknown.error_code == "tool_not_found"
    assert unknown.error_message == "tool not found"
    assert unknown.model_dump(by_alias=True)["errorCode"] == "tool_not_found"
    assert unknown.model_dump(by_alias=True)["errorMessage"] == "tool not found"
    assert unknown.metadata["toolName"] == "MissingTool"
    assert unknown.metadata["reason"] == "tool not found"

    registry.register(make_manifest("DeclaredOnly", enabled_by_default=True))
    missing_handler = asyncio.run(
        dispatcher.dispatch("DeclaredOnly", {}, make_context(), mode="act")
    )
    assert missing_handler.status == "error"
    assert missing_handler.error == "tool handler missing"
    assert missing_handler.error_code == "tool_handler_missing"
    assert missing_handler.error_message == "tool handler missing"
    assert missing_handler.model_dump(by_alias=True)["errorCode"] == "tool_handler_missing"
    assert missing_handler.model_dump(by_alias=True)["errorMessage"] == "tool handler missing"
    assert missing_handler.metadata["toolName"] == "DeclaredOnly"
    assert missing_handler.metadata["reason"] == "tool handler missing"
