from __future__ import annotations

import subprocess
import sys

import pytest

from magi_agent.plugins.manager import PluginOptOutRecord, resolve_plugin_state
from magi_agent.plugins.manifest import parse_plugin_manifest
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.plugins.tool_projection import project_native_plugin_tool_manifests
from magi_agent.channels.contract import channel_adapter_manifests


def _opt_out(plugin_id: str) -> PluginOptOutRecord:
    return PluginOptOutRecord(
        pluginId=plugin_id,
        scope="bot",
        actor="user:test",
        reason="projection contract test",
        ts="2026-05-15T00:00:00Z",
    )


def _manifest(
    plugin_id: str,
    *,
    tool_name: str = "SharedTool",
    permissions: tuple[str, ...] = ("read",),
):
    return parse_plugin_manifest(
        {
            "id": plugin_id,
            "name": plugin_id,
            "kind": "native",
            "version": "0.1.0-adk-scaffold",
            "defaultInstalled": True,
            "defaultEnabled": True,
            "optOutAllowed": True,
            "securityCritical": False,
            "permissions": permissions,
            "tools": (
                {
                    "name": tool_name,
                    "entrypoint": "plugins.native:tool",
                },
            ),
        }
    )


def test_native_state_projection_returns_expected_native_tool_names_in_resolved_order() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    projected = project_native_plugin_tool_manifests(state)

    assert tuple(manifest.name for manifest in projected) == (
        "AgentMemorySearch",
        "AgentMemoryRemember",
        "ArtifactUpdate",
        "ArtifactDelete",
        "Browser",
        "SocialBrowser",
        "CodeDiagnostics",
        "CodeIntelligence",
        "CodeSymbolSearch",
        "CodeWorkspace",
        "CodingBenchmark",
        "CommitCheckpoint",
        "PackageDependencyResolve",
        "ProjectVerificationPlanner",
        "RepoMap",
        "RepositoryMap",
        "RepoTaskState",
        "SafeCommand",
        "DocumentWrite",
        "SpreadsheetWrite",
        "FileDeliver",
        "FileSend",
        "KnowledgeSearch",
        "knowledge-search",
        "KnowledgeWrite",
        "knowledge-write",
        "MissionLedger",
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "SkillLoader",
        "SkillRuntimeHooks",
        "ExternalToolLoader",
        "BatchRead",
        "DateRange",
        "ExternalSourceCache",
        "ExternalSourceRead",
        "SpawnAgent",
        "SpawnWorktreeApply",
        "TaskBoard",
        "MemoryRedact",
        "NotifyUser",
        "SwitchToActMode",
        "WebSearch",
        "web-search",
        "web_search",
        "WebFetch",
    )
    assert tuple(manifest.name for manifest in projected if manifest.plugin_id == "openmagi.knowledge") == (
        "KnowledgeSearch",
        "knowledge-search",
        "KnowledgeWrite",
        "knowledge-write",
    )
    assert tuple(manifest.name for manifest in projected if manifest.plugin_id == "openmagi.web") == (
        "WebSearch",
        "web-search",
        "web_search",
        "WebFetch",
    )
    assert tuple(manifest.name for manifest in projected if manifest.plugin_id == "openmagi.browser") == (
        "Browser",
        "SocialBrowser",
    )
    assert tuple(
        manifest.name
        for manifest in projected
        if manifest.plugin_id == "openmagi.web-acquisition"
    ) == ()
    assert tuple(manifest.name for manifest in projected if manifest.plugin_id == "openmagi.missions") == (
        "MissionLedger",
    )


def test_default_enabled_web_browser_and_web_acquisition_emit_projected_tools() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    projected = project_native_plugin_tool_manifests(state)

    names = {manifest.name for manifest in projected}
    assert "WebSearch" in state.active_tools
    assert "web-search" in state.active_tools
    assert "web_search" in state.active_tools
    assert "WebFetch" in state.active_tools
    assert "Browser" in state.active_tools
    assert "SocialBrowser" in state.active_tools
    assert "web_acquisition_provider_boundary" in state.active_harness_rules
    assert "WebSearch" in names
    assert "web-search" in names
    assert "web_search" in names
    assert "WebFetch" in names
    assert "Browser" in names
    assert "SocialBrowser" in names
    assert any(manifest.plugin_id == "openmagi.web" for manifest in projected)
    assert any(manifest.plugin_id == "openmagi.browser" for manifest in projected)


def test_default_enabled_scheduled_work_emits_projected_toolhost_entries() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    projected = project_native_plugin_tool_manifests(state)

    names = {manifest.name for manifest in projected}
    assert "CronCreate" in state.active_tools
    assert "CronList" in state.active_tools
    assert "CronUpdate" in state.active_tools
    assert "CronDelete" in state.active_tools
    assert "TaskWait" in state.active_tools
    assert "scheduled_work_recipe_policy" in state.active_harness_rules
    assert "CronCreate" in names
    assert "CronList" in names
    assert "CronUpdate" in names
    assert "CronDelete" in names
    assert "TaskWait" in names
    assert any(manifest.plugin_id == "openmagi.scheduled-work" for manifest in projected)


def test_projected_tool_manifests_are_metadata_only_and_execution_free() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    projected = project_native_plugin_tool_manifests(state)
    by_name = {manifest.name: manifest for manifest in projected}

    knowledge_search = by_name["KnowledgeSearch"]
    assert knowledge_search.kind == "native"
    assert knowledge_search.source.kind == "native-plugin"
    assert knowledge_search.source.package == "openmagi.knowledge"
    assert knowledge_search.plugin_id == "openmagi.knowledge"
    assert knowledge_search.permission == "net"
    assert knowledge_search.enabled_by_default is True
    assert knowledge_search.opt_out is True
    assert knowledge_search.input_schema == {"type": "object", "additionalProperties": True}
    assert knowledge_search.timeout_ms == 0
    assert "openmagi.knowledge" in knowledge_search.description
    assert knowledge_search.tags == ("native-plugin", "openmagi.knowledge", "metadata-only")
    assert by_name["AgentMemoryRemember"].permission == "write"
    assert by_name["ArtifactUpdate"].permission == "write"
    assert by_name["ArtifactDelete"].permission == "write"
    assert by_name["CommitCheckpoint"].permission == "write"
    assert by_name["KnowledgeWrite"].permission == "write"
    assert by_name["knowledge-write"].permission == "write"
    assert by_name["DocumentWrite"].permission == "write"
    assert by_name["SpreadsheetWrite"].permission == "write"
    assert by_name["ExternalSourceCache"].permission == "write"
    assert by_name["TaskBoard"].permission == "write"

    assert "MissionLedger" in by_name

    dumped = "\n".join(
        repr(manifest.model_dump(by_alias=True))
        for manifest in projected
    )
    assert "entrypoint" not in dumped
    assert "configSchema" not in dumped
    assert "GATEWAY_TOKEN" not in dumped
    assert "FIRECRAWL_API_KEY" not in dumped
    assert "magi_agent.plugins.native" not in dumped


def test_file_delivery_projection_is_default_enabled_metadata_only_and_channel_traffic_free() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    projected = project_native_plugin_tool_manifests(state)
    by_name = {manifest.name: manifest for manifest in projected}

    file_deliver = by_name["FileDeliver"]
    assert file_deliver.plugin_id == "openmagi.documents"
    assert file_deliver.permission == "net"
    assert file_deliver.enabled_by_default is True
    assert file_deliver.opt_out is True
    assert file_deliver.should_defer is True
    assert file_deliver.side_effect_class == "external"
    assert file_deliver.latency_class == "background"
    assert file_deliver.adk_tool_type == "LongRunningFunctionTool"
    assert file_deliver.input_schema["required"] == ("target",)
    assert file_deliver.input_schema["properties"]["target"]["enum"] == ("chat", "kb", "both")  # type: ignore[index]
    assert "future-approval-required" in file_deliver.preconditions
    assert "adk-artifact-service-required" in file_deliver.preconditions
    assert "channel-traffic-disabled" in file_deliver.preconditions

    file_send = by_name["FileSend"]
    assert file_send.plugin_id == "openmagi.documents"
    assert file_send.permission == "net"
    assert file_send.enabled_by_default is True
    assert file_send.should_defer is True
    assert file_send.side_effect_class == "external"
    assert file_send.latency_class == "background"
    assert file_send.adk_tool_type == "LongRunningFunctionTool"
    assert file_send.input_schema["required"] == ("path",)
    assert file_send.input_schema["properties"]["mode"]["enum"] == ("document", "photo")  # type: ignore[index]
    assert "future-approval-required" in file_send.preconditions
    assert "adk-artifact-service-required" in file_send.preconditions
    assert "channel-traffic-disabled" in file_send.preconditions

    telegram = next(
        manifest for manifest in channel_adapter_manifests() if manifest.channel_type == "telegram"
    )
    assert telegram.supports_file_delivery is True
    assert telegram.default_enabled is False
    assert telegram.traffic_attached is False
    assert telegram.execution_attached is False

    dumped = "\n".join(
        repr(manifest.model_dump(by_alias=True))
        for manifest in (file_deliver, file_send)
    )
    assert "entrypoint" not in dumped
    assert "handler" not in dumped.lower()
    assert "sendFile" not in dumped
    assert "file-send.sh" not in dumped
    assert "ArtifactService" not in dumped


def test_file_delivery_projection_returns_defensive_nested_schema_copies() -> None:
    state = resolve_plugin_state(native_plugin_manifests())

    first = project_native_plugin_tool_manifests(state)
    first_by_name = {manifest.name: manifest for manifest in first}
    first_file_deliver = first_by_name["FileDeliver"]
    first_file_send = first_by_name["FileSend"]

    first_file_deliver.input_schema["properties"]["target"]["enum"] = ("mutated",)  # type: ignore[index]
    first_file_send.input_schema["properties"]["mode"]["enum"] = ("mutated",)  # type: ignore[index]

    second = project_native_plugin_tool_manifests(state)
    second_by_name = {manifest.name: manifest for manifest in second}

    assert second_by_name["FileDeliver"].input_schema["properties"]["target"]["enum"] == (  # type: ignore[index]
        "chat",
        "kb",
        "both",
    )
    assert second_by_name["FileSend"].input_schema["properties"]["mode"]["enum"] == (  # type: ignore[index]
        "document",
        "photo",
    )


def test_duplicate_projected_tool_names_across_enabled_plugins_raise_value_error() -> None:
    state = resolve_plugin_state(
        (
            _manifest("openmagi.alpha", tool_name="SharedTool"),
            _manifest("openmagi.zeta", tool_name="SharedTool"),
        )
    )

    with pytest.raises(ValueError, match="duplicate native plugin tool name: SharedTool"):
        project_native_plugin_tool_manifests(state)


def test_tool_projection_import_boundary_stays_metadata_only() -> None:
    script = """
import importlib
import sys

importlib.import_module("magi_agent.plugins.tool_projection")
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.transport",
    "magi_agent.plugins.native.",
)
loaded = [
    name
    for name in sys.modules
    if name == forbidden_prefixes[0] or name.startswith(forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"tool projection import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
