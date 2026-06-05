import asyncio

import pytest
from pydantic import ValidationError

from magi_agent.tools import (
    ToolDispatcher,
    ToolRegistry,
    core_tool_manifests,
    register_core_tool_manifests,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest


EXPECTED_CORE_TOOL_NAMES = (
    "ToolSearch",
    "TodoWrite",
    "FileRead",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Glob",
    "Grep",
    "Bash",
    "TestRun",
    "GitDiff",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "ArtifactCreate",
    "ArtifactRead",
    "ArtifactList",
    "Clock",
    "Calculation",
    "HealthStatus",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "CronList",
)


def make_context() -> ToolContext:
    return ToolContext(bot_id="bot-1", turn_id="turn-1", workspace_root="/tmp/workspace")


def manifest_by_name() -> dict[str, ToolManifest]:
    return {manifest.name: manifest for manifest in core_tool_manifests()}


def test_core_tool_catalog_seed_set_is_immutable_builtin_core_metadata() -> None:
    manifests = core_tool_manifests()

    assert isinstance(manifests, tuple)
    assert tuple(manifest.name for manifest in manifests) == EXPECTED_CORE_TOOL_NAMES

    first = manifests[0]
    with pytest.raises(ValidationError):
        first.name = "Changed"

    for manifest in manifests:
        assert manifest.kind == "core"
        assert manifest.source.kind == "builtin"
        assert manifest.source.package == "openmagi.core"
        assert manifest.enabled_by_default is True
        assert manifest.opt_out is True
        if manifest.name == "TodoWrite":
            # TodoWrite carries a structured payload schema, not the loose
            # additionalProperties default shared by the other core tools.
            assert manifest.input_schema["type"] == "object"
            assert "todos" in manifest.input_schema["properties"]  # type: ignore[index]
        else:
            assert manifest.input_schema == {"type": "object", "additionalProperties": True}


def test_core_tool_manifests_returns_defensive_manifest_and_schema_copies() -> None:
    manifests = core_tool_manifests()
    file_read = manifests[0]

    file_read.input_schema["additionalProperties"] = False
    file_read.input_schema["callerMutation"] = {"nested": True}
    file_read.input_schema["callerMutation"]["nested"] = False  # type: ignore[index]

    fresh_file_read = core_tool_manifests()[0]

    assert fresh_file_read is not file_read
    assert fresh_file_read.input_schema == {"type": "object", "additionalProperties": True}
    assert fresh_file_read.input_schema is not file_read.input_schema


def test_register_core_tool_manifests_uses_defensive_schema_copies() -> None:
    mutated_file_read = core_tool_manifests()[0]
    mutated_file_read.input_schema["additionalProperties"] = False
    mutated_file_read.input_schema["callerMutation"] = True

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    registered_file_read = registry.resolve("FileRead")
    assert registered_file_read is not None
    assert registered_file_read is not mutated_file_read
    assert registered_file_read.input_schema == {"type": "object", "additionalProperties": True}
    assert registered_file_read.input_schema is not mutated_file_read.input_schema


def test_register_core_tool_manifests_returned_manifests_do_not_mutate_registry_schema() -> None:
    registry = ToolRegistry()
    manifests = register_core_tool_manifests(registry)
    returned_file_read = manifests[0]

    returned_file_read.input_schema["additionalProperties"] = False
    returned_file_read.input_schema["callerMutation"] = {"nested": True}
    returned_file_read.input_schema["callerMutation"]["nested"] = False  # type: ignore[index]

    registered_file_read = registry.resolve("FileRead")

    assert registered_file_read is not None
    assert registered_file_read is not returned_file_read
    assert registered_file_read.input_schema == {"type": "object", "additionalProperties": True}
    assert registered_file_read.input_schema is not returned_file_read.input_schema


def test_core_tool_catalog_uses_conservative_permission_and_mode_metadata() -> None:
    manifests = manifest_by_name()

    assert manifests["FileRead"].permission == "read"
    assert manifests["FileRead"].available_in_modes == ("plan", "act")
    assert manifests["FileRead"].mutates_workspace is False
    assert manifests["FileRead"].dangerous is False

    assert manifests["FileWrite"].permission == "write"
    assert manifests["FileWrite"].available_in_modes == ("act",)
    assert manifests["FileWrite"].mutates_workspace is True

    assert manifests["PatchApply"].permission == "write"
    assert manifests["PatchApply"].available_in_modes == ("act",)
    assert manifests["PatchApply"].mutates_workspace is True
    assert manifests["PatchApply"].dangerous is False

    assert manifests["Bash"].permission == "execute"
    assert manifests["Bash"].available_in_modes == ("act",)
    assert manifests["Bash"].mutates_workspace is True
    assert manifests["Bash"].dangerous is True

    assert manifests["GitDiff"].permission == "read"
    assert manifests["GitDiff"].available_in_modes == ("plan", "act")
    assert manifests["GitDiff"].mutates_workspace is False
    assert manifests["GitDiff"].dangerous is False

    assert manifests["AskUserQuestion"].permission == "meta"
    assert manifests["AskUserQuestion"].available_in_modes == ("plan", "act")

    assert manifests["EnterPlanMode"].permission == "meta"
    assert manifests["EnterPlanMode"].available_in_modes == ("plan", "act")

    assert manifests["ExitPlanMode"].permission == "meta"
    assert manifests["ExitPlanMode"].available_in_modes == ("act",)
    assert manifests["ExitPlanMode"].mutates_workspace is False
    assert manifests["ExitPlanMode"].dangerous is False

    assert manifests["ArtifactCreate"].permission == "write"
    assert manifests["ArtifactCreate"].available_in_modes == ("act",)

    assert manifests["Clock"].permission == "meta"
    assert manifests["Clock"].available_in_modes == ("plan", "act")

    assert manifests["Calculation"].permission == "meta"
    assert manifests["Calculation"].available_in_modes == ("plan", "act")

    for name in ("HealthStatus", "TaskList", "TaskGet", "TaskOutput", "CronList"):
        assert manifests[name].permission == "meta"
        assert manifests[name].available_in_modes == ("plan", "act")
        assert manifests[name].mutates_workspace is False
        assert manifests[name].dangerous is False
        assert "gate1a" not in manifests[name].tags


def test_register_core_tool_manifests_keeps_catalog_enabled_and_protected() -> None:
    registry = ToolRegistry()
    manifests = register_core_tool_manifests(registry)

    assert tuple(manifest.name for manifest in manifests) == EXPECTED_CORE_TOOL_NAMES
    assert [tool.name for tool in registry.list_all()] == sorted(EXPECTED_CORE_TOOL_NAMES)
    assert {tool.name for tool in registry.list_available(mode="plan")} == {
        name
        for name in EXPECTED_CORE_TOOL_NAMES
        if name
        not in {
            "FileWrite",
            "FileEdit",
            "PatchApply",
            "Bash",
            "TestRun",
            "ExitPlanMode",
            "ArtifactCreate",
        }
    }
    assert {tool.name for tool in registry.list_available(mode="act")} == set(EXPECTED_CORE_TOOL_NAMES)
    assert all(registry.is_enabled(name) is True for name in EXPECTED_CORE_TOOL_NAMES)

    with pytest.raises(ValueError, match="core/builtin"):
        registry.unregister("FileRead")


def test_default_enabled_shell_catalog_tool_without_handler_returns_missing_handler() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "Bash",
            {"command": "echo should-not-run"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_handler_missing"
    assert result.metadata["toolName"] == "Bash"
    assert result.metadata["reason"] == "tool handler missing"


def test_enabled_catalog_tool_without_handler_returns_structured_missing_handler_error() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileRead",
            {"path": "README.md"},
            make_context(),
            mode="plan",
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_handler_missing"
    assert result.error_message == "tool handler missing"
    assert result.metadata == {
        "toolName": "FileRead",
        "permissionClass": "read",
        "mode": "plan",
        "dangerous": False,
        "mutatesWorkspace": False,
        "reason": "tool handler missing",
    }


def test_enabled_shell_catalog_tool_without_handler_returns_missing_handler() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    registry.enable("Bash")

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "Bash",
            {"command": "echo should-not-run"},
            make_context(),
            mode="act",
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_handler_missing"
    assert result.metadata["toolName"] == "Bash"
    assert result.metadata["reason"] == "tool handler missing"
