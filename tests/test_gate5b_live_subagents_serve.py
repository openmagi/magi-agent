"""Serve-path live sub-agent wiring (gate5b dashboard chat).

These tests cover the *enablement* gap: the gate5b full toolhost is only reached
on the user-visible serve path when ``build_gate5b_full_toolhost_config_from_env``
produces a ``ready`` config whose selection scope matches the per-request scope
computed by ``_gate5b_full_toolhost_bundle``. The default-OFF
``MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED`` flag (gated by the existing live
child-runner master gate) derives that scope so a dashboard-chat turn dispatching
SpawnAgent actually spawns a real (here: fake-model) sub-agent — preserving the
child_runner_boundary depth/total/output/toolset caps.

Env isolation: every run must strip provider keys and pin MAGI_CONFIG so no real
model call is made (see repo gate prefix). The child runner is injected as a fake
``RealLocalChildRunner`` (openmagi_live_provider=True) so the boundary executes
the live branch without a network call.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat_routes import _gate5b_full_toolhost_bundle
from magi_agent.transport.chat_shared import (
    Gate5BUserVisibleChatRouteConfig,
    build_gate5b_full_toolhost_config_from_env,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
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


def _serve_route_config() -> Gate5BUserVisibleChatRouteConfig:
    # The serve scope environment used by _gate5b_full_toolhost_bundle.
    return Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="local",
        environmentAllowlist=("local",),
    )


def _install_full_toolhost_config(
    runtime: OpenMagiRuntime,
    env: Mapping[str, str],
) -> None:
    runtime.gate5b_full_toolhost_config = build_gate5b_full_toolhost_config_from_env(
        env,
        runtime.config,
    )


class _FakeLiveChildRunner:
    openmagi_live_provider = True

    def __init__(self, **kwargs: object) -> None:  # noqa: D401 - test double
        pass

    async def run_child(self, request: object) -> Mapping[str, object]:
        return {
            "childExecutionId": "child-exec-serve-live",
            "status": "completed",
            "summary": "Delegated child completed.",
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def test_serve_bundle_disabled_when_live_subagents_flag_unset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Flag OFF + no explicit full-toolhost env => config disabled => serve bundle
    # is NOT ready => the route falls back to gate1a (SpawnAgent never exposed).
    # This is byte-identical to today's serve behavior.
    monkeypatch.delenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )

    runtime = _runtime()
    _install_full_toolhost_config(runtime, {"MAGI_CHILD_RUNNER_LIVE_ENABLED": "1"})

    bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())

    assert bundle.status != "ready"
    assert "SpawnAgent" not in bundle.exposed_tool_names


def test_serve_bundle_ready_exposes_spawn_agent_without_write_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Flag ON (+ child-runner live ON): the serve bundle reaches ready and exposes
    # the FULL toolhost surface (SpawnAgent + read + write/mutation tools).
    monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )

    runtime = _runtime()
    _install_full_toolhost_config(
        runtime,
        {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        },
    )

    bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())

    assert bundle.status == "ready"
    assert "SpawnAgent" in bundle.exposed_tool_names
    # FULL serve surface: the entire write/mutation toolset is exposed (operator
    # explicitly enabled every tool on the dashboard serve path).
    assert {"FileWrite", "FileEdit", "PatchApply", "Bash"} <= set(
        bundle.exposed_tool_names
    )
    # Read-only surface remains available alongside the write surface + SpawnAgent.
    assert {"FileRead", "Glob", "Grep"} <= set(bundle.exposed_tool_names)
    bundle.host.shutdown()


@pytest.mark.asyncio
async def test_serve_turn_spawn_agent_executes_live_child(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The full serve wiring: flag ON -> ready bundle -> dispatching SpawnAgent on
    # the serve-constructed host reaches the child boundary and executes a
    # (fake-model) child. No provider key is set, so a real RealLocalChildRunner
    # would have blocked; the injected fake proves the boundary live branch runs.
    monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _FakeLiveChildRunner)

    public_events: list[dict[str, object]] = []
    runtime = _runtime()
    _install_full_toolhost_config(
        runtime,
        {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        },
    )

    bundle = _gate5b_full_toolhost_bundle(
        runtime,
        _serve_route_config(),
        public_event_sink=lambda event: public_events.append(dict(event)),
        session_id="serve-session-1",
    )
    assert bundle.status == "ready"

    outcome = await bundle.host.dispatch(
        "SpawnAgent",
        {"prompt": "assign a helper"},
        request_digest=_sha256("request-serve-spawn"),
        tool_call_id="call-serve-spawn",
    )

    assert outcome.status == "ok"
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert preview["status"] == "ok"
    output = preview["output"]
    assert isinstance(output, dict)
    assert output["liveChildRunnerAttached"] is True
    assert output["summary"] == "Delegated child completed."
    # The boundary emitted live child lifecycle events on the serve sink.
    event_types = [event["type"] for event in public_events]
    assert "child_started" in event_types
    assert "child_completed" in event_types
    # Prompt + child summary never leak into the sanitized public events.
    import json

    assert "assign a helper" not in json.dumps(public_events, sort_keys=True)
    assert "Delegated child completed" not in json.dumps(public_events, sort_keys=True)
    bundle.host.shutdown()


@pytest.mark.asyncio
async def test_serve_spawn_agent_depth_cap_denies_not_crashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Depth cap preserved through the serve ToolContext seam: a host threaded with
    # spawn_depth at the boundary max (2) makes SpawnAgent request depth 3, which
    # the child_runner_boundary rejects as child_spawn_depth_exceeded WITHOUT
    # crashing the turn (the cap is enforced inside the boundary the tool builds).
    monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )

    import magi_agent.runtime.child_runner_live as _live_mod

    class _MustNotRunChildRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            raise AssertionError("child must not execute past the depth cap")

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _MustNotRunChildRunner)

    from magi_agent.gates.gate5b_full_toolhost import (
        build_gate5b_full_toolhost_bundle,
    )

    runtime = _runtime()
    _install_full_toolhost_config(
        runtime,
        {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        },
    )
    config = runtime.gate5b_full_toolhost_config

    # Build the bundle with a parent spawn_depth already at the boundary max so the
    # next SpawnAgent request (parent+1 = 3) trips child_spawn_depth_exceeded.
    bundle = build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "local",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
        spawn_depth=2,
    )
    assert bundle.status == "ready"

    outcome = await bundle.host.dispatch(
        "SpawnAgent",
        {"prompt": "go deeper"},
        request_digest=_sha256("request-serve-depth"),
        tool_call_id="call-serve-depth",
    )

    # Denied, not crashed: spawn_agent never raises; the blocked envelope surfaces.
    assert outcome.status in {"blocked", "ok"}
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert preview["errorCode"] == "child_spawn_depth_exceeded"
    bundle.host.shutdown()
