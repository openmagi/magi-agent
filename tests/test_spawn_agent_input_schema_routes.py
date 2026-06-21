"""SpawnAgent's input_schema must advertise REGISTRY routes — not a stale
hardcoded example.

Root cause for "spawn three SOTA subagents" returning
``child_model_route_unknown`` for every explicit model: SpawnAgent's
``model`` parameter description was a static literal:

    "Model override for the child (e.g. 'claude-opus-4-5')."

The parent LLM, following the most authoritative signal it sees for a tool
call (the parameter description), copies the example name — but the
``ModelTierRegistry`` only knows ``claude-opus-4-8``, never ``claude-opus-4-5``,
so the route is rejected at validation. ``available_child_model_routes`` is
the single source of truth used by ``resolve_child_route``; this test pins
that the same enumeration is what the LLM reads on the tool-call side.

Hermetic: explicit ``env=`` to the dynamic builder so the test does not depend
on the process's actual provider keys / config file.
"""
from __future__ import annotations

import pytest

from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.plugins.tool_projection import (
    _build_spawn_agent_input_schema,
    project_native_plugin_tool_manifests,
)


@pytest.fixture(scope="module")
def spawn_agent_manifest():
    state = resolve_plugin_state(native_plugin_manifests())
    projected = project_native_plugin_tool_manifests(state)
    by_name = {m.name: m for m in projected}
    return by_name["SpawnAgent"]


def test_dynamic_builder_lists_registry_routes_in_model_description() -> None:
    # Empty env → key-aware filter fails open → all registry providers visible.
    schema = _build_spawn_agent_input_schema(env={})
    model_desc = schema["properties"]["model"]["description"]
    # Each canonical registry SOTA / Flash route must appear by name.
    assert "claude-sonnet-4-6" in model_desc, model_desc
    assert "gpt-5.5" in model_desc, model_desc
    assert "gemini-3.5-flash" in model_desc, model_desc


def test_dynamic_builder_drops_the_stale_opus_4_5_example() -> None:
    # The stale literal that misled GPT-5.5 into emitting
    # ``provider=anthropic, model=claude-opus-4-5`` (rejected by the registry).
    schema = _build_spawn_agent_input_schema(env={})
    model_desc = schema["properties"]["model"]["description"]
    provider_desc = schema["properties"]["provider"]["description"]
    assert "claude-opus-4-5" not in model_desc, model_desc
    assert "claude-opus-4-5" not in provider_desc, provider_desc


def test_dynamic_builder_lists_registry_providers_in_provider_description() -> None:
    schema = _build_spawn_agent_input_schema(env={})
    provider_desc = schema["properties"]["provider"]["description"]
    for provider in ("anthropic", "openai", "gemini"):
        assert provider in provider_desc, (provider, provider_desc)


def test_dynamic_builder_mentions_route_unknown_failure_mode() -> None:
    # The description must teach the LLM what happens on a mismatch, so the
    # model does not silently keep guessing.
    schema = _build_spawn_agent_input_schema(env={})
    model_desc = schema["properties"]["model"]["description"]
    assert "child_model_route_unknown" in model_desc, model_desc


def test_projected_spawn_agent_manifest_uses_dynamic_schema(spawn_agent_manifest) -> None:
    # End-to-end: the manifest the runtime ships to the LLM must carry the
    # dynamic schema (not the stale static one). Asserting on a canonical
    # registry name pins the wiring without coupling to env-specific filtering.
    model_desc = spawn_agent_manifest.input_schema["properties"]["model"]["description"]
    assert "claude-sonnet-4-6" in model_desc, model_desc
    assert "claude-opus-4-5" not in model_desc, model_desc
