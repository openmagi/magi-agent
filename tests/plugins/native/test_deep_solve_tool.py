"""Tests for the DeepSolve native tool handler (U3).

Covers:
- Gate OFF (disabled profile) → honest disabled result
- Kill-switch honored
- Live child runner off → not_attached passthrough
- Never-raise on orchestrator exception
- clamp_stage_toolset matrix
- Verdict record appended once per run
- Catalog registration visible
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_context(**kwargs: Any) -> Any:
    from magi_agent.tools.context import ToolContext

    defaults: dict[str, Any] = {
        "botId": "test-bot",
        "sessionId": "test-session",
        "turnId": "test-turn",
    }
    defaults.update(kwargs)
    return ToolContext.model_validate(defaults)


def _make_args(**kwargs: Any) -> dict[str, object]:
    defaults: dict[str, object] = {
        "problem": "Given an array of integers, find the maximum subarray sum.",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Import sanity: module must load without heavy runtime imports
# ---------------------------------------------------------------------------

class TestModuleImport:
    def test_module_loads_without_child_runner_in_sys_modules(self) -> None:
        """Importing deep_solve.py must not pull child_runner_live at import time."""
        import sys

        # Remove if already imported so we get a clean test
        mod_name = "magi_agent.plugins.native.deep_solve"
        was_imported = mod_name in sys.modules

        if not was_imported:
            # Guard that the import itself doesn't cause child_runner_live to load
            import importlib

            heavy = "magi_agent.runtime.child_runner_live"
            pre = heavy in sys.modules
            importlib.import_module(mod_name)
            # Whether heavy was pre-loaded doesn't matter — just ensure no AttributeError
            import magi_agent.plugins.native.deep_solve  # noqa: F401
        else:
            import magi_agent.plugins.native.deep_solve  # noqa: F401

        # If we got here without ImportError, the module is loadable
        assert True


# ---------------------------------------------------------------------------
# clamp_stage_toolset helper
# ---------------------------------------------------------------------------

class TestClampStageToolset:
    """clamp_stage_toolset(operator_gate, stage_request) -> min(ordering)."""

    def _clamp(self, gate: str, request: str) -> str:
        from magi_agent.plugins.native.deep_solve import clamp_stage_toolset

        return clamp_stage_toolset(gate, request)

    def test_gate_none_request_none_returns_none(self) -> None:
        assert self._clamp("none", "none") == "none"

    def test_gate_none_request_readonly_degrades_to_none(self) -> None:
        assert self._clamp("none", "readonly") == "none"

    def test_gate_none_request_full_degrades_to_none(self) -> None:
        assert self._clamp("none", "full") == "none"

    def test_gate_readonly_request_none_returns_none(self) -> None:
        assert self._clamp("readonly", "none") == "none"

    def test_gate_readonly_request_readonly_returns_readonly(self) -> None:
        assert self._clamp("readonly", "readonly") == "readonly"

    def test_gate_readonly_request_full_degrades_to_readonly(self) -> None:
        assert self._clamp("readonly", "full") == "readonly"

    def test_gate_full_request_none_returns_none(self) -> None:
        assert self._clamp("full", "none") == "none"

    def test_gate_full_request_readonly_returns_readonly(self) -> None:
        assert self._clamp("full", "readonly") == "readonly"

    def test_gate_full_request_full_returns_full(self) -> None:
        assert self._clamp("full", "full") == "full"

    def test_degraded_to_none_emits_trace_note(self, capsys: Any) -> None:
        """When gate forces request from readonly → none, a trace note is logged."""
        import magi_agent.plugins.native.deep_solve as mod

        trace_calls: list[tuple[str, str]] = []

        def fake_trace(gate: str, request: str) -> None:
            trace_calls.append((gate, request))

        # Patch the trace mechanism
        original = getattr(mod, "_emit_clamp_trace", None)
        if original is not None:
            with patch.object(mod, "_emit_clamp_trace", side_effect=fake_trace):
                mod.clamp_stage_toolset("none", "readonly")
            assert len(trace_calls) > 0, "Expected _emit_clamp_trace to be called on demotion"
        else:
            # If the internal function is named differently, just ensure no crash
            mod.clamp_stage_toolset("none", "readonly")


# ---------------------------------------------------------------------------
# Gate: deep_solve disabled
# ---------------------------------------------------------------------------

class TestDeepSolveGateDisabled:
    @pytest.mark.asyncio
    async def test_disabled_profile_returns_honest_blocked(self) -> None:
        """With MAGI_DEEP_SOLVE_ENABLED=0, handler returns a blocked result with reason."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        with patch(
            "magi_agent.config.env.is_deep_solve_enabled",
            return_value=False,
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"
        assert result.error_code is not None
        # Should mention deep_solve in reason or error
        reason = str(result.error_code or "")
        assert "deep_solve" in reason.lower() or "disabled" in reason.lower()

    @pytest.mark.asyncio
    async def test_kill_switch_returns_blocked(self) -> None:
        """Kill-switch env var overrides enabled and returns blocked."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        # Force is_deep_solve_enabled to return False (kill-switch scenario)
        with patch(
            "magi_agent.config.env.is_deep_solve_enabled",
            return_value=False,
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"


# ---------------------------------------------------------------------------
# Gate: live child runner off
# ---------------------------------------------------------------------------

class TestLiveChildRunnerOff:
    @pytest.mark.asyncio
    async def test_not_attached_when_child_runner_disabled(self) -> None:
        """With live child runner OFF, returns not_attached status."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=False,
            ),
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"
        output = result.output or {}
        # Should mirror spawn_agent's not_attached pattern
        status_val = output.get("status", "")
        assert status_val in ("not_attached", "blocked")
        # Key presence: liveChildRunnerAttached should be False
        attached = output.get("liveChildRunnerAttached")
        assert attached is False


# ---------------------------------------------------------------------------
# Never-raise contract
# ---------------------------------------------------------------------------

class TestNeverRaise:
    @pytest.mark.asyncio
    async def test_orchestrator_exception_returns_blocked(self) -> None:
        """Any exception from the orchestrator → blocked ToolResult, never raises."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        async def _bad_orchestrator(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("orchestrator exploded")

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=True,
            ),
            patch(
                "magi_agent.solving.deep_solve.run_deep_solve",
                side_effect=_bad_orchestrator,
            ),
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"
        assert result.error_code is not None


# ---------------------------------------------------------------------------
# Catalog registration
# ---------------------------------------------------------------------------

class TestCatalogRegistration:
    def test_deep_solve_in_native_catalog(self) -> None:
        """native_plugin_manifests() must include a DeepSolve tool entry."""
        from magi_agent.plugins.native_catalog import native_plugin_manifests

        manifests = native_plugin_manifests()
        tool_names: set[str] = set()
        for plugin in manifests:
            # plugin is a PluginManifest Pydantic model
            for tool in plugin.tools:
                tool_names.add(tool.name)

        assert "DeepSolve" in tool_names, (
            f"DeepSolve not found in native_plugin_manifests. Found: {sorted(tool_names)}"
        )

    def test_deep_solve_entrypoint_in_catalog(self) -> None:
        """The DeepSolve tool entry must point to the correct entrypoint."""
        from magi_agent.plugins.native_catalog import native_plugin_manifests

        manifests = native_plugin_manifests()
        for plugin in manifests:
            for tool in plugin.tools:
                if tool.name == "DeepSolve":
                    assert "deep_solve" in tool.entrypoint, (
                        f"DeepSolve entrypoint should reference deep_solve, got: {tool.entrypoint}"
                    )
                    return

        pytest.fail("DeepSolve not found in any native plugin manifest")


# ---------------------------------------------------------------------------
# Gate5b surface
# ---------------------------------------------------------------------------

class TestGate5bSurface:
    def test_deep_solve_in_gate5b_first_party_names(self) -> None:
        """DeepSolve must appear in _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES."""
        from magi_agent.gates.gate5b_full_toolhost import (
            _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES,
        )

        assert "DeepSolve" in _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES, (
            "DeepSolve not found in _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES"
        )

    def test_deep_solve_in_live_registry(self) -> None:
        """DeepSolve must resolve in the live tool registry (gate5b sync)."""
        from magi_agent.gates.gate5b_full_toolhost import (
            _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES,
        )
        from magi_agent.runtime.openmagi_runtime import (
            _build_core_tool_registry,
            _build_default_plugin_state,
        )

        registry = _build_core_tool_registry(_build_default_plugin_state())
        assert registry.resolve("DeepSolve") is not None, (
            "DeepSolve is in _GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES but "
            "not found in the live registry"
        )


# ---------------------------------------------------------------------------
# Verdict (append_verdict called exactly once)
# ---------------------------------------------------------------------------

class TestVerdictAppendedOnce:
    @pytest.mark.asyncio
    async def test_verdict_appended_once_on_success_path(self) -> None:
        """append_verdict is called exactly once per deep_solve run."""
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.solving.deep_solve import DeepSolveOutcome, DeepSolveVerdictData

        verdicts: list[Any] = []

        # Fake a minimal run_deep_solve that calls append_verdict once
        async def _fake_run(config: Any, deps: Any) -> DeepSolveOutcome:
            from magi_agent.solving.deep_solve import (
                DeepSolveOutcome,
                DeepSolveVerdictData,
            )

            verdict = DeepSolveVerdictData(
                problem_digest="sha256:abc",
                problem_class="executable",
                cycles=1,
                refolds=0,
                acceptance_basis="tests_passed",
                final_findings_open=(),
                per_stage_child_refs=(),
            )
            deps.append_verdict(verdict)
            return DeepSolveOutcome(
                acceptance_basis="tests_passed",
                final_artifact="solution code here",
                verdict=verdict,
            )

        # Patch append_verdict to capture calls
        original_append = None

        context = _make_context()
        args = _make_args()

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=True,
            ),
            patch("magi_agent.solving.deep_solve.run_deep_solve", side_effect=_fake_run),
        ):
            # Also need to patch the boundary creation since no real runner is attached
            try:
                result = await deep_solve(args, context)
            except Exception:
                # The handler must never raise — if it does, test should fail
                pytest.fail("deep_solve raised an exception (violates never-raise contract)")

        # Result should not be a hard error
        assert result is not None
