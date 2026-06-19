"""Tests for recipeRefs per-spawn arg (TY1).

TDD: tests written before implementation (RED first, then GREEN).

Coverage:
- T1: recipeRefs=["openmagi.research","openmagi.dev-coding"] → metadata["recipeRefs"]==("openmagi.research","openmagi.dev-coding")
- T2: No recipeRefs → metadata has NO "recipeRefs" key (byte-identical).
- T3: recipeRefs with non-string / blank entries filtered out.
- T4: SpawnAgent manifest declares optional recipeRefs array in parameters.properties.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(**overrides: object) -> ToolContext:
    defaults: dict[str, object] = {
        "botId": "test-bot",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "spawnDepth": 0,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


def _capturing_runner_class(captured: list[object]) -> type:
    """Return a fake RealLocalChildRunner that records the ChildTaskRequest."""

    class _CapturingRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            captured.append(request)
            return {
                "childExecutionId": "child-exec-recipe-refs",
                "status": "completed",
                "summary": "captured",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    return _CapturingRunner


# ---------------------------------------------------------------------------
# T1: recipeRefs flows into metadata["recipeRefs"]
# ---------------------------------------------------------------------------


def test_recipe_refs_arg_put_in_request_metadata(monkeypatch) -> None:
    """spawn_agent(recipeRefs=["openmagi.research","openmagi.dev-coding"]) → metadata["recipeRefs"]."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured: list[object] = []
    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _capturing_runner_class(captured))

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0)
    asyncio.run(
        spawn_agent(
            {"prompt": "do research", "recipeRefs": ["openmagi.research", "openmagi.dev-coding"]},
            ctx,
        )
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.metadata["recipeRefs"] == ("openmagi.research", "openmagi.dev-coding")


# ---------------------------------------------------------------------------
# T2: absent recipeRefs → metadata has NO "recipeRefs" key
# ---------------------------------------------------------------------------


def test_absent_recipe_refs_leaves_metadata_unchanged(monkeypatch) -> None:
    """No recipeRefs in arguments → metadata has NO 'recipeRefs' key."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured: list[object] = []
    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _capturing_runner_class(captured))

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0)
    asyncio.run(spawn_agent({"prompt": "no recipe refs"}, ctx))

    assert len(captured) == 1
    req = captured[0]
    assert "recipeRefs" not in req.metadata


# ---------------------------------------------------------------------------
# T3: non-string / blank entries are filtered out
# ---------------------------------------------------------------------------


def test_recipe_refs_filters_non_string_and_blank(monkeypatch) -> None:
    """recipeRefs=["openmagi.research","",5] → metadata["recipeRefs"]==("openmagi.research",)."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured: list[object] = []
    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _capturing_runner_class(captured))

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0)
    asyncio.run(
        spawn_agent(
            {"prompt": "filter test", "recipeRefs": ["openmagi.research", "", 5]},
            ctx,
        )
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.metadata["recipeRefs"] == ("openmagi.research",)


# ---------------------------------------------------------------------------
# T4: SpawnAgent manifest declares recipeRefs in parameters.properties
# ---------------------------------------------------------------------------


def test_spawn_agent_manifest_declares_recipe_refs_property(tmp_path) -> None:
    """SpawnAgent ADK declaration advertises optional recipeRefs array param."""
    import hashlib

    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.gates.gate5b_full_toolhost import (
        GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        Gate5BFullToolHostConfig,
        build_gate5b_full_toolhost_bundle,
    )
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    def _sha256(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="test", build_sha="sha-test"),
        )
    )
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    spawn = next(tool for tool in bundle.tools if tool.name == "SpawnAgent")
    declaration = spawn._get_declaration()
    assert declaration is not None
    payload = declaration.model_dump(by_alias=True, exclude_none=True, mode="json")
    properties = payload["parameters"]["properties"]

    # recipeRefs must be declared
    assert "recipeRefs" in properties, f"recipeRefs not in properties: {list(properties)}"
    schema = properties["recipeRefs"]
    # ADK serialises list types as ARRAY (uppercase); accept both forms.
    raw_type = str(schema.get("type", "")).upper()
    any_of_types = [str(e.get("type", "")).upper() for e in schema.get("anyOf", [])]
    assert raw_type == "ARRAY" or "ARRAY" in any_of_types, f"expected array type in schema: {schema}"
    # Must NOT be required
    required = payload["parameters"].get("required", [])
    assert "recipeRefs" not in required
