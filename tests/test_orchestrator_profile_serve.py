"""Seam 1b (serve) — orchestrator main-agent profile on gate5b path.

TDD: written RED first, then GREEN via gate5b_full_toolhost.py.

When ``MAGI_MAIN_AGENT_PROFILE=orchestrator`` the serve main-agent assembly must:
1. Give the main agent ONLY the restricted toolset
   (intersection of full gate5b set with orchestrator_tool_names()).
2. Set ToolContext.spawn_cap = the full bundle ceiling (all exposed names).
3. The existing hardcoded permissionScope payload must be unchanged.

When the flag is unset, behaviour MUST be byte-identical to today.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.main_agent_profile import (
    apply_orchestrator_filter,
    orchestrator_tool_names,
)
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
            bot_id="bot-orch-test",
            user_id="user-orch-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="test", build_sha="sha-test"),
        )
    )


def _serve_route_config() -> Gate5BUserVisibleChatRouteConfig:
    return Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-orch-test"),
        selectedOwnerUserIdDigest=_sha256("user-orch-test"),
        environment="local",
        environmentAllowlist=("local",),
    )


def _install_full_toolhost_config(
    runtime: OpenMagiRuntime,
    env: dict[str, str],
) -> None:
    runtime.gate5b_full_toolhost_config = build_gate5b_full_toolhost_config_from_env(
        env,
        runtime.config,
    )


# ---------------------------------------------------------------------------
# Tests for the factored helper _apply_orchestrator_profile_serve
# ---------------------------------------------------------------------------


class TestApplyOrchestratorProfileServeHelper:
    """Unit-tests for gate5b_full_toolhost._apply_orchestrator_profile_serve."""

    def _call(
        self,
        full_names: tuple[str, ...],
        env: dict[str, str] | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
        from magi_agent.gates.gate5b_full_toolhost import (  # noqa: PLC0415
            _apply_orchestrator_profile_serve,
        )

        return _apply_orchestrator_profile_serve(full_names, env=env)

    def test_flag_unset_returns_full_names_unchanged(self) -> None:
        """Flag unset → returns the same tuple and None spawn_cap."""
        names = ("FileRead", "Bash", "SpawnAgent", "Glob")
        restricted, spawn_cap = self._call(names, env={})
        assert restricted is names, "flag unset must return the exact same tuple"
        assert spawn_cap is None

    def test_orchestrator_restricts_tool_names(self) -> None:
        """orchestrator profile keeps only allowed names."""
        names = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob", "FileWrite")
        restricted, _ = self._call(names, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"})
        assert "Bash" not in restricted
        assert "WebSearch" not in restricted
        assert "FileWrite" not in restricted
        assert "FileRead" in restricted
        assert "Glob" in restricted
        assert "SpawnAgent" in restricted

    def test_orchestrator_spawn_cap_equals_full_names(self) -> None:
        """spawn_cap must equal the full bundle names verbatim."""
        names = ("FileRead", "Bash", "SpawnAgent", "Glob")
        _, spawn_cap = self._call(names, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"})
        assert spawn_cap == names

    def test_orchestrator_preserves_order(self) -> None:
        """Restricted names preserve original order."""
        names = ("FileRead", "Bash", "SpawnAgent", "Glob")
        restricted, _ = self._call(names, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"})
        assert list(restricted) == ["FileRead", "SpawnAgent", "Glob"]

    def test_flag_unset_spawn_cap_is_none(self) -> None:
        """Explicit check: flag unset → spawn_cap is None."""
        names = ("Bash", "FileRead")
        _, spawn_cap = self._call(names, env={})
        assert spawn_cap is None

    def test_uses_os_environ_when_env_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env=None the function reads os.environ."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        names = ("Bash", "FileRead", "SpawnAgent")
        restricted, spawn_cap = self._call(names, env=None)
        assert "Bash" not in restricted
        assert "FileRead" in restricted
        assert "SpawnAgent" in restricted
        assert spawn_cap == names


# ---------------------------------------------------------------------------
# Integration: build_gate5b_full_toolhost_bundle applies orchestrator filter
# ---------------------------------------------------------------------------


class TestBuildGate5BFullToolhostBundleOrchestratorFilter:
    """Verify build_gate5b_full_toolhost_bundle applies the orchestrator filter."""

    def _base_env(self) -> dict[str, str]:
        return {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        }

    def test_flag_unset_full_tool_set_no_spawn_cap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Flag unset → bundle exposes the full gate5b set; host spawn_cap is None."""
        monkeypatch.delenv("MAGI_MAIN_AGENT_PROFILE", raising=False)
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = self._base_env()
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"
        exposed = set(bundle.exposed_tool_names)
        # Full set: write tools must be present
        assert "Bash" in exposed
        assert "FileWrite" in exposed
        assert "SpawnAgent" in exposed
        # ADK tools have names matching exposed set
        adk_names = {getattr(t, "name", None) for t in bundle.tools}
        assert adk_names == exposed
        # Host spawn_cap must be None (byte-identical to today)
        assert bundle.host._spawn_cap is None
        bundle.host.shutdown()

    def test_orchestrator_flag_restricts_bundle_tools(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """orchestrator profile → bundle tools restricted to read+SpawnAgent only."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = self._base_env()
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"

        adk_names = {getattr(t, "name", None) for t in bundle.tools}
        # Mutation tools must be absent
        assert "Bash" not in adk_names
        assert "FileWrite" not in adk_names
        assert "FileEdit" not in adk_names
        assert "PatchApply" not in adk_names
        # Read + SpawnAgent must be present (if they were in the full set)
        allowed = set(orchestrator_tool_names())
        assert adk_names <= allowed, f"unexpected tools: {adk_names - allowed}"
        assert "SpawnAgent" in adk_names
        bundle.host.shutdown()

    def test_orchestrator_flag_spawn_cap_equals_full_ceiling(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """orchestrator profile → host._spawn_cap == full exposed set (the ceiling)."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = self._base_env()
        _install_full_toolhost_config(runtime, env)

        # Build without orchestrator to get the full set
        monkeypatch.delenv("MAGI_MAIN_AGENT_PROFILE", raising=False)
        bundle_full = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        full_exposed = set(bundle_full.exposed_tool_names)
        bundle_full.host.shutdown()

        # Now build with orchestrator
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        bundle_orch = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle_orch.status == "ready"
        spawn_cap = bundle_orch.host._spawn_cap
        assert spawn_cap is not None, "spawn_cap must be set for orchestrator"
        assert isinstance(spawn_cap, tuple)
        assert set(spawn_cap) == full_exposed, (
            f"spawn_cap {set(spawn_cap)} != full exposed {full_exposed}"
        )
        bundle_orch.host.shutdown()

    def test_orchestrator_permission_scope_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The existing hardcoded permissionScope payload must be preserved."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = self._base_env()
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"

        # Capture the ToolContext built during a dispatch by intercepting it.
        # We dispatch a read-only tool that will succeed regardless of workspace.
        from magi_agent.tools.context import ToolContext  # noqa: PLC0415

        captured: list[ToolContext] = []

        # Capture via ToolContext construction intercept
        original_tc_init = ToolContext.__init__

        def capturing_init(self_tc: ToolContext, **kwargs: object) -> None:
            original_tc_init(self_tc, **kwargs)
            captured.append(self_tc)

        import unittest.mock as mock  # noqa: PLC0415

        with mock.patch.object(ToolContext, "__init__", capturing_init):
            import asyncio  # noqa: PLC0415

            asyncio.run(
                bundle.host.dispatch(
                    "FileRead",
                    {"path": "nonexistent_for_test.txt"},
                    request_digest=_sha256("test-scope"),
                    tool_call_id="call-scope-test",
                )
            )

        assert captured, "ToolContext was not constructed during dispatch"
        ctx = captured[0]
        assert ctx.permission_scope == {
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        }, f"permissionScope changed: {ctx.permission_scope!r}"
        bundle.host.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_tool_context_spawn_cap_set_on_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """orchestrator: ToolContext built during SpawnAgent dispatch has spawn_cap set."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")
        monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        import magi_agent.runtime.child_runner_live as _live_mod  # noqa: PLC0415

        class _FakeLiveChildRunner:
            openmagi_live_provider = True

            def __init__(self, **kwargs: object) -> None:
                pass

            async def run_child(self, request: object) -> dict[str, object]:
                return {
                    "childExecutionId": "child-exec-orch",
                    "status": "completed",
                    "summary": "ok",
                    "evidenceRefs": (),
                    "artifactRefs": (),
                    "auditEventRefs": (),
                }

        monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _FakeLiveChildRunner)

        runtime = _runtime()
        env = {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
            "MAGI_MAIN_AGENT_PROFILE": "orchestrator",
        }
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"
        assert bundle.host._spawn_cap is not None

        from magi_agent.tools.context import ToolContext  # noqa: PLC0415
        import unittest.mock as mock  # noqa: PLC0415

        captured: list[ToolContext] = []
        original_tc_init = ToolContext.__init__

        def capturing_init(self_tc: ToolContext, **kwargs: object) -> None:
            original_tc_init(self_tc, **kwargs)
            captured.append(self_tc)

        with mock.patch.object(ToolContext, "__init__", capturing_init):
            outcome = await bundle.host.dispatch(
                "SpawnAgent",
                {"prompt": "hello"},
                request_digest=_sha256("test-orch-spawn"),
                tool_call_id="call-orch-spawn",
            )

        assert captured, "ToolContext was not constructed"
        ctx = captured[0]
        assert ctx.spawn_cap is not None, "spawn_cap must be set for orchestrator"
        assert isinstance(ctx.spawn_cap, tuple)
        assert len(ctx.spawn_cap) > 0
        bundle.host.shutdown()

    def test_flag_unset_tool_context_spawn_cap_is_none_on_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Flag unset: ToolContext built during dispatch has spawn_cap=None."""
        monkeypatch.delenv("MAGI_MAIN_AGENT_PROFILE", raising=False)
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        }
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"
        assert bundle.host._spawn_cap is None

        from magi_agent.tools.context import ToolContext  # noqa: PLC0415
        import unittest.mock as mock  # noqa: PLC0415

        captured: list[ToolContext] = []
        original_tc_init = ToolContext.__init__

        def capturing_init(self_tc: ToolContext, **kwargs: object) -> None:
            original_tc_init(self_tc, **kwargs)
            captured.append(self_tc)

        import asyncio  # noqa: PLC0415

        with mock.patch.object(ToolContext, "__init__", capturing_init):
            asyncio.run(
                bundle.host.dispatch(
                    "FileRead",
                    {"path": "nonexistent.txt"},
                    request_digest=_sha256("test-noprofile"),
                    tool_call_id="call-noprofile",
                )
            )

        assert captured
        ctx = captured[0]
        assert ctx.spawn_cap is None, f"spawn_cap should be None, got {ctx.spawn_cap!r}"
        bundle.host.shutdown()

    def test_orchestrator_full_ceiling_restricted_hands_invariant(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invariant: orchestrator main-agent gets restricted hands, children get full ceiling.

        (a) bundle.tools / bundle.exposed_tool_names are the RESTRICTED set — no Bash.
        (b) host.exposed_tool_names and host._spawn_cap are the FULL set — Bash present.

        This locks the intentional split: the host keeps the full ceiling so
        parentToolNames + tighten-only inheritance gives children the complete
        grant ceiling, while the orchestrator's own ADK tools are restricted.
        """
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        runtime = _runtime()
        env = {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        }
        _install_full_toolhost_config(runtime, env)

        bundle = _gate5b_full_toolhost_bundle(runtime, _serve_route_config())
        assert bundle.status == "ready"

        # (a) Main-agent hands are RESTRICTED — mutation tools absent
        adk_names = {getattr(t, "name", None) for t in bundle.tools}
        assert "Bash" not in adk_names, "Bash must not be in orchestrator's own ADK tools"
        assert "FileWrite" not in adk_names
        assert set(bundle.exposed_tool_names) == adk_names, (
            "bundle.exposed_tool_names must match the ADK tool names"
        )

        # (b) Child-facing ceiling is FULL — host keeps the complete grant set
        assert "Bash" in set(bundle.host.exposed_tool_names), (
            "host.exposed_tool_names must be the FULL set (children inherit this as ceiling)"
        )
        assert bundle.host._spawn_cap is not None, "spawn_cap must be set for orchestrator"
        assert "Bash" in set(bundle.host._spawn_cap), (
            "host._spawn_cap must be the FULL set so children can receive Bash via delegation"
        )

        # (c) The two sides of the split are consistent: full ceiling > restricted hands
        assert adk_names < set(bundle.host.exposed_tool_names), (
            "restricted hands must be a strict subset of the full ceiling"
        )
        assert set(bundle.host._spawn_cap) == set(bundle.host.exposed_tool_names), (
            "spawn_cap and host.exposed_tool_names must both reflect the full ceiling"
        )
        bundle.host.shutdown()
