"""Seam 1b (CLI) — orchestrator main-agent profile toolset filtering.

TDD: written RED first, then GREEN via wiring.py.

When ``MAGI_MAIN_AGENT_PROFILE=orchestrator`` the CLI main-agent assembly must:
1. Give the main agent ONLY the restricted toolset
   (intersection of full set with orchestrator_tool_names()).
2. Set ``ToolContext.spawn_cap`` = the full bundle names (the grant ceiling).

When the flag is unset, behaviour MUST be byte-identical to today.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.runtime.main_agent_profile import (
    ORCHESTRATOR_PROFILE,
    apply_orchestrator_filter,
    orchestrator_tool_names,
)


# ---------------------------------------------------------------------------
# Helpers — minimal ADK-tool stand-ins
# ---------------------------------------------------------------------------


def _fake_tool(name: str) -> object:
    """Minimal tool stub: has only a ``.name`` attribute."""
    return SimpleNamespace(name=name)


def _names(tools: list[object]) -> set[str]:
    return {getattr(t, "name", None) for t in tools}


# ---------------------------------------------------------------------------
# Tests for the small helper ``_apply_orchestrator_profile``
# ---------------------------------------------------------------------------


class TestApplyOrchestratorProfileHelper:
    """Unit-tests for magi_agent.cli.wiring._apply_orchestrator_profile.

    This is the factored seam: takes (full_tools, env) and returns
    (main_tools, spawn_cap_or_None).
    """

    def _call(
        self,
        full_tools: list[object],
        env: dict[str, str] | None = None,
    ) -> tuple[list[object], tuple[str, ...] | None]:
        from magi_agent.cli.wiring import _apply_orchestrator_profile  # noqa: PLC0415

        return _apply_orchestrator_profile(full_tools, env=env)

    def test_flag_unset_returns_full_tools_unchanged(self) -> None:
        """Flag unset → returns the same list object and None spawn_cap."""
        tools = [_fake_tool("Bash"), _fake_tool("FileRead"), _fake_tool("Glob")]
        main_tools, spawn_cap = self._call(tools, env={})
        assert main_tools is tools, "flag unset must return the exact same list"
        assert spawn_cap is None, "flag unset must return None spawn_cap"

    def test_orchestrator_profile_restricts_tools(self) -> None:
        """orchestrator profile keeps only allowed tool names."""
        full = [
            _fake_tool("FileRead"),
            _fake_tool("Bash"),
            _fake_tool("WebSearch"),
            _fake_tool("SpawnAgent"),
            _fake_tool("Glob"),
        ]
        main_tools, spawn_cap = self._call(
            full, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"}
        )
        result_names = _names(main_tools)
        assert "Bash" not in result_names
        assert "WebSearch" not in result_names
        assert "FileRead" in result_names
        assert "Glob" in result_names
        assert "SpawnAgent" in result_names

    def test_orchestrator_profile_spawn_cap_equals_full_names(self) -> None:
        """spawn_cap must equal the full bundle names verbatim."""
        full = [
            _fake_tool("FileRead"),
            _fake_tool("Bash"),
            _fake_tool("WebSearch"),
            _fake_tool("SpawnAgent"),
        ]
        full_names = tuple(t.name for t in full)  # type: ignore[union-attr]
        _, spawn_cap = self._call(
            full, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"}
        )
        assert spawn_cap == full_names

    def test_orchestrator_profile_preserves_order(self) -> None:
        """Restricted toolset preserves original order."""
        full = [
            _fake_tool("FileRead"),
            _fake_tool("Bash"),
            _fake_tool("SpawnAgent"),
            _fake_tool("Glob"),
        ]
        main_tools, _ = self._call(
            full, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"}
        )
        result_names = [t.name for t in main_tools]  # type: ignore[union-attr]
        assert result_names == ["FileRead", "SpawnAgent", "Glob"]

    def test_flag_unset_spawn_cap_is_none(self) -> None:
        """Explicit check: flag unset → spawn_cap is None (not empty tuple)."""
        tools = [_fake_tool("Bash"), _fake_tool("FileRead")]
        _, spawn_cap = self._call(tools, env={})
        assert spawn_cap is None

    def test_no_orchestrator_tools_in_full_set_yields_empty_restricted(self) -> None:
        """If the full set has no orchestrator tools, restricted is empty."""
        full = [_fake_tool("Bash"), _fake_tool("WebSearch"), _fake_tool("FileWrite")]
        main_tools, spawn_cap = self._call(
            full, env={"MAGI_MAIN_AGENT_PROFILE": "orchestrator"}
        )
        assert main_tools == []
        assert spawn_cap == ("Bash", "WebSearch", "FileWrite")

    def test_uses_os_environ_when_env_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env=None the function reads os.environ."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        full = [_fake_tool("Bash"), _fake_tool("FileRead")]
        main_tools, spawn_cap = self._call(full, env=None)
        assert _names(main_tools) == {"FileRead"}
        assert spawn_cap == ("Bash", "FileRead")


# ---------------------------------------------------------------------------
# Integration test: _build_first_party_adk_tools invokes the helper
# ---------------------------------------------------------------------------


class TestBuildFirstPartyADKToolsCallsHelper:
    """Verify that _build_first_party_adk_tools applies the orchestrator filter."""

    def test_orchestrator_flag_calls_apply_helper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """_build_first_party_adk_tools calls _apply_orchestrator_profile."""
        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        call_log: list[dict[str, object]] = []
        real_apply = wiring_mod._apply_orchestrator_profile

        def spy_apply(
            full_tools: list[object],
            env: dict[str, str] | None = None,
        ) -> tuple[list[object], tuple[str, ...] | None]:
            result = real_apply(full_tools, env=env)
            call_log.append(
                {
                    "full_names": tuple(
                        getattr(t, "name", None) for t in full_tools
                    ),
                    "restricted_names": tuple(
                        getattr(t, "name", None) for t in result[0]
                    ),
                    "spawn_cap": result[1],
                }
            )
            return result

        monkeypatch.setattr(wiring_mod, "_apply_orchestrator_profile", spy_apply)
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")

        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="test-session",
        )

        assert call_log, "_apply_orchestrator_profile was not called"
        entry = call_log[0]
        # Restricted set must be a subset of full set
        restricted = set(entry["restricted_names"])
        full = set(entry["full_names"])
        assert restricted.issubset(full), f"{restricted} not ⊆ {full}"
        # spawn_cap must equal full names (as tuple)
        assert set(entry["spawn_cap"]) == full  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration test: ToolContext.spawn_cap is set when orchestrator
# ---------------------------------------------------------------------------


class TestToolContextSpawnCapWiring:
    """Verify that the tool_context_factory sets spawn_cap when orchestrator."""

    def test_flag_unset_tool_context_spawn_cap_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Flag unset: every ToolContext built has spawn_cap is None."""
        monkeypatch.delenv("MAGI_MAIN_AGENT_PROFILE", raising=False)
        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        # We call _build_first_party_adk_tools to exercise the real path.
        # The resulting tools are ADK FunctionTool wrappers; we introspect
        # the tool_context_factory by calling _build_first_party_adk_tools
        # with a monkeypatched build_adk_function_tools_for_registry that
        # captures the factory.
        captured_factory: dict[str, object] = {}

        real_build_adk = None
        try:
            from magi_agent.adk_bridge.tool_adapter import (  # noqa: PLC0415
                build_adk_function_tools_for_registry,
            )

            real_build_adk = build_adk_function_tools_for_registry
        except Exception:
            pass

        if real_build_adk is None:
            pytest.skip("adk_bridge not importable")

        import magi_agent.adk_bridge.tool_adapter as ta  # noqa: PLC0415

        def capturing_build(
            registry: object,
            dispatcher: object,
            mode: object = "act",
            tool_context_factory: object = None,
            attach_enabled: bool = True,
            exposed_tool_names: object = None,
        ) -> list[object]:
            captured_factory["factory"] = tool_context_factory
            return []

        monkeypatch.setattr(ta, "build_adk_function_tools_for_registry", capturing_build)

        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="sess-noprofile",
        )

        factory = captured_factory.get("factory")
        assert factory is not None, "tool_context_factory not captured"

        adk_ctx = SimpleNamespace(
            function_call=SimpleNamespace(name="FileRead", id="call-1"),
            state={},
        )
        ctx = factory(adk_ctx)  # type: ignore[operator]
        assert ctx.spawn_cap is None, f"spawn_cap should be None, got {ctx.spawn_cap!r}"

    def test_orchestrator_flag_tool_context_spawn_cap_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """orchestrator profile: ToolContext.spawn_cap == full bundle names."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")
        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        captured_state: dict[str, object] = {}

        import magi_agent.adk_bridge.tool_adapter as ta  # noqa: PLC0415

        def capturing_build(
            registry: object,
            dispatcher: object,
            mode: object = "act",
            tool_context_factory: object = None,
            attach_enabled: bool = True,
            exposed_tool_names: object = None,
        ) -> list[object]:
            captured_state["factory"] = tool_context_factory
            # Return a fake tool so wrap_cli_adk_tools_with_evidence_collector
            # has something to work with.
            return [_fake_tool("FileRead")]

        monkeypatch.setattr(ta, "build_adk_function_tools_for_registry", capturing_build)

        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="sess-orch",
        )

        factory = captured_state.get("factory")
        assert factory is not None, "tool_context_factory not captured"

        adk_ctx = SimpleNamespace(
            function_call=SimpleNamespace(name="FileRead", id="call-1"),
            state={},
        )
        ctx = factory(adk_ctx)  # type: ignore[operator]
        assert ctx.spawn_cap is not None, "spawn_cap must not be None for orchestrator"
        assert isinstance(ctx.spawn_cap, tuple)
        assert len(ctx.spawn_cap) > 0
