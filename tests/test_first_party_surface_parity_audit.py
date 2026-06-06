from __future__ import annotations

import json
from pathlib import Path

from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native.skills import _bundled_skill_candidates
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.recipes.compiler import PackRegistry
from magi_agent.tools import ToolRegistry, core_tool_manifests


FIXTURE = Path(__file__).parent / "fixtures" / "parity" / "first_party_surface_audit_matrix.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _native_tool_names() -> set[str]:
    return {
        tool.name
        for manifest in native_plugin_manifests()
        for tool in manifest.tools
    }


def _native_tool_capability_names() -> set[str]:
    return {
        capability.name
        for manifest in native_plugin_manifests()
        for capability in manifest.capabilities
        if capability.type == "tool"
    }


def test_legacy_ts_tool_surface_matrix_matches_current_catalogs() -> None:
    matrix = _fixture()
    core_tool_names = {manifest.name for manifest in core_tool_manifests()}
    native_tool_names = _native_tool_names()
    metadata_only_capabilities = _native_tool_capability_names() - native_tool_names

    assert matrix["schemaVersion"] == "first-party-surface-audit.v1"

    for row in matrix["legacyTsTools"]:  # type: ignore[index]
        name = row["name"]
        status = row["ossStatus"]

        if status == "core-tool":
            assert name in core_tool_names
        elif status == "native-plugin-tool":
            assert name in native_tool_names
        elif status == "metadata-only-capability":
            assert name in metadata_only_capabilities
        elif status == "implementation-only":
            assert name == "ToolRegistry"
            assert ToolRegistry.__name__ == "ToolRegistry"
        else:  # pragma: no cover - fixture schema guard
            raise AssertionError(f"unknown OSS status for {name}: {status}")


def test_first_party_packs_and_plugins_remain_default_inert() -> None:
    matrix = _fixture()
    registry = PackRegistry.with_first_party_packs()
    plugin_state = resolve_plugin_state(native_plugin_manifests())

    expected_pack_ids = tuple(matrix["firstPartyRecipePacks"])  # type: ignore[index]
    assert registry.pack_ids == expected_pack_ids

    for pack_id in expected_pack_ids:
        pack = registry.get(pack_id)
        flags = pack.attachment_flags.model_dump(by_alias=True)
        assert set(flags.values()) == {False}, pack_id
        assert pack.live_tool_refs == ()
        assert pack.live_callback_refs == ()
        assert pack.runner_route_refs == ()

    assert plugin_state.traffic_attached is False
    assert plugin_state.execution_attached is False
    assert all(status.traffic_attached is False for status in plugin_state.plugins)
    assert all(status.execution_attached is False for status in plugin_state.plugins)
    assert set(matrix["metadataOnlyToolCapabilities"]).issubset(  # type: ignore[arg-type]
        _native_tool_capability_names() - _native_tool_names()
    )


def test_bundled_first_party_skills_are_workflow_only_and_gap_is_documented() -> None:
    matrix = _fixture()
    bundled_skills = set(_bundled_skill_candidates())

    assert set(matrix["bundledWorkflowSkills"]).issubset(bundled_skills)  # type: ignore[arg-type]

    hosted_skill_gap = matrix["documentedGaps"]["hostedTemplateSkills"]  # type: ignore[index]
    assert hosted_skill_gap["status"] == "plan-only"
    assert hosted_skill_gap["defaultAuthority"] == "none"
