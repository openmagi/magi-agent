"""
Tests that SpawnAgent's CLI projection (via _SPECIAL_TOOL_METADATA) advertises
allowedTools and recipeRefs as typed properties in its input_schema and includes
a description that informs the CLI model of those params.

TDD: RED first (before the _SPECIAL_TOOL_METADATA entry is added), then GREEN.
"""
from __future__ import annotations

import pytest

from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.plugins.tool_projection import project_native_plugin_tool_manifests


@pytest.fixture(scope="module")
def spawn_agent_manifest():
    state = resolve_plugin_state(native_plugin_manifests())
    projected = project_native_plugin_tool_manifests(state)
    by_name = {m.name: m for m in projected}
    return by_name["SpawnAgent"]


def test_spawn_agent_input_schema_has_properties(spawn_agent_manifest) -> None:
    """input_schema must have a 'properties' dict (not the generic bare-object fallback)."""
    schema = spawn_agent_manifest.input_schema
    assert "properties" in schema, (
        "SpawnAgent input_schema lacks 'properties'; still using generic fallback"
    )


def test_spawn_agent_input_schema_has_allowed_tools_property(spawn_agent_manifest) -> None:
    """allowedTools must appear as a property (array of strings)."""
    props = spawn_agent_manifest.input_schema["properties"]
    assert "allowedTools" in props, "allowedTools missing from SpawnAgent input_schema properties"
    allowed_tools = props["allowedTools"]
    assert allowed_tools["type"] == "array"
    assert allowed_tools["items"]["type"] == "string"


def test_spawn_agent_input_schema_has_recipe_refs_property(spawn_agent_manifest) -> None:
    """recipeRefs must appear as a property (array of strings)."""
    props = spawn_agent_manifest.input_schema["properties"]
    assert "recipeRefs" in props, "recipeRefs missing from SpawnAgent input_schema properties"
    recipe_refs = props["recipeRefs"]
    assert recipe_refs["type"] == "array"
    assert recipe_refs["items"]["type"] == "string"


def test_spawn_agent_input_schema_allowed_tools_and_recipe_refs_not_required(
    spawn_agent_manifest,
) -> None:
    """allowedTools and recipeRefs must NOT be in the required list."""
    schema = spawn_agent_manifest.input_schema
    required = schema.get("required", ())
    assert "allowedTools" not in required, "allowedTools should not be required"
    assert "recipeRefs" not in required, "recipeRefs should not be required"


def test_spawn_agent_input_schema_has_core_params(spawn_agent_manifest) -> None:
    """Core params (prompt, persona, provider, model, budgetMs) must be in properties."""
    props = spawn_agent_manifest.input_schema["properties"]
    for param in ("prompt", "persona", "provider", "model", "budgetMs"):
        assert param in props, f"{param!r} missing from SpawnAgent input_schema properties"


def test_spawn_agent_description_mentions_allowed_tools(spawn_agent_manifest) -> None:
    """Description must mention allowedTools so the CLI model is informed."""
    assert "allowedTools" in spawn_agent_manifest.description, (
        "SpawnAgent description must mention allowedTools"
    )


def test_spawn_agent_description_mentions_recipe_refs(spawn_agent_manifest) -> None:
    """Description must mention recipeRefs so the CLI model is informed."""
    assert "recipeRefs" in spawn_agent_manifest.description, (
        "SpawnAgent description must mention recipeRefs"
    )


def test_spawn_agent_description_is_not_generic_fallback(spawn_agent_manifest) -> None:
    """Description must NOT be the generic 'Metadata-only native plugin tool projection' string."""
    assert "Metadata-only native plugin tool projection" not in spawn_agent_manifest.description, (
        "SpawnAgent still using generic fallback description — _SPECIAL_TOOL_METADATA entry missing"
    )


def test_spawn_agent_schema_allows_additional_properties(spawn_agent_manifest) -> None:
    """additionalProperties must be True for forward-compat."""
    schema = spawn_agent_manifest.input_schema
    assert schema.get("additionalProperties") is True


def test_spawn_agent_plugin_id_is_subagents(spawn_agent_manifest) -> None:
    """Sanity check: SpawnAgent belongs to openmagi.subagents."""
    assert spawn_agent_manifest.plugin_id == "openmagi.subagents"
