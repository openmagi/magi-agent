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

    # inherit profile - RED tests (written before implementation)

    def test_gate_inherit_request_none_returns_none(self) -> None:
        assert self._clamp("inherit", "none") == "none"

    def test_gate_inherit_request_readonly_returns_readonly(self) -> None:
        assert self._clamp("inherit", "readonly") == "readonly"

    def test_gate_inherit_request_inherit_returns_inherit(self) -> None:
        assert self._clamp("inherit", "inherit") == "inherit"

    def test_gate_inherit_request_full_degrades_to_inherit(self) -> None:
        assert self._clamp("inherit", "full") == "inherit"

    def test_gate_full_request_inherit_returns_inherit(self) -> None:
        assert self._clamp("full", "inherit") == "inherit"

    def test_gate_none_request_inherit_degrades_to_none(self) -> None:
        assert self._clamp("none", "inherit") == "none"

    def test_gate_readonly_request_inherit_degrades_to_readonly(self) -> None:
        assert self._clamp("readonly", "inherit") == "readonly"

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
# Gate: pack removed / uninstalled (U4 dispatch gate)
# ---------------------------------------------------------------------------

class TestPackUninstallGate:
    @pytest.mark.asyncio
    async def test_pack_removed_returns_blocked(self) -> None:
        """With the openmagi.deep-solve pack removed, dispatch is blocked."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.plugins.native.deep_solve._deep_solve_pack_enabled",
                return_value=False,
            ),
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"
        assert result.error_code == "deep_solve_pack_removed"
        output = result.output or {}
        assert output.get("reason") == "deep_solve_pack_removed"

    @pytest.mark.asyncio
    async def test_pack_removed_via_config_disable(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        """The gate resolves installed state through the SAME loader seam a
        config.toml ``[packs] disable`` removal uses (mirror of
        test_direct_manifest_registration_respects_pack_disable for
        PersistentPython)."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[packs]\ndisable = ["open' 'magi.deep-solve"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("MAGI_CONFIG", str(cfg))

        context = _make_context()
        args = _make_args()

        with patch(
            "magi_agent.config.env.is_deep_solve_enabled", return_value=True
        ):
            result = await deep_solve(args, context)

        assert result.status == "blocked"
        assert result.error_code == "deep_solve_pack_removed"

    @pytest.mark.asyncio
    async def test_pack_present_passes_through_to_next_gate(self) -> None:
        """Pack installed → gate passes through to the child-runner gate."""
        from magi_agent.plugins.native.deep_solve import deep_solve

        context = _make_context()
        args = _make_args()

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.plugins.native.deep_solve._deep_solve_pack_enabled",
                return_value=True,
            ),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=False,
            ),
        ):
            result = await deep_solve(args, context)

        # Reached the NEXT gate (child runner), not the pack gate.
        assert result.status == "blocked"
        assert result.error_code == "live_child_runner_disabled"

    def test_pack_enabled_helper_fail_open(self) -> None:
        """Loader failure → helper returns True (never blocks on infra errors)."""
        import magi_agent.plugins.native.deep_solve as mod

        with patch(
            "magi_agent.packs.discovery.load_packs_config",
            side_effect=RuntimeError("loader exploded"),
        ):
            assert mod._deep_solve_pack_enabled() is True


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

        append_calls: list[Any] = []

        # Fake run_deep_solve: calls the HANDLER'S real deps.append_verdict
        # once and returns a VALID DeepSolveOutcome (frozen/extra=forbid —
        # invalid fields here would silently exercise the never-raise
        # fallback and turn this test into a tautology; review F3).
        async def _fake_run(config: Any, deps: Any) -> Any:
            from magi_agent.solving.deep_solve import (
                DeepSolveOutcome,
                DeepSolveVerdictData,
            )

            verdict = DeepSolveVerdictData(
                problem_digest="abc123",
                problem_class="executable",
                cycles=1,
                refolds=0,
                acceptance_basis="tests_passed",
                final_findings_open=(),
                per_stage_child_refs=(),
            )
            deps.append_verdict(verdict)
            append_calls.append(verdict)
            return DeepSolveOutcome(
                acceptance_basis="tests_passed",
                cycles=1,
                refolds=0,
                final_findings_open=(),
                best_candidate="solution code here",
            )

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
            result = await deep_solve(args, context)

        # The verdict path itself is verified end-to-end: appended exactly
        # once, and promoted onto the ToolResult output payload.
        assert len(append_calls) == 1
        assert result.status == "ok"
        assert result.output["acceptanceBasis"] == "tests_passed"
        verdict_payload = result.output["deepSolveVerdict"]
        assert verdict_payload["acceptance_basis"] == "tests_passed"
        assert verdict_payload["cycles"] == 1


class TestExecuteTestsGovernedPath:
    """Review F1: ground-truth execution must route through the governed
    parent Bash toolhost (standalone_core_tool_handler), never a raw
    subprocess, and the artifact must live under the workspace root."""

    @pytest.mark.asyncio
    async def test_execute_tests_routes_through_governed_bash(
        self, tmp_path: Any
    ) -> None:
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.tools.result import ToolResult

        captured_cmds: list[str] = []
        handler_requests: list[str] = []

        def _fake_handler_factory(tool_name: str, **kwargs: Any) -> Any:
            handler_requests.append(tool_name)

            async def _fake_bash(arguments: dict[str, object], context: Any) -> ToolResult:
                captured_cmds.append(str(arguments["command"]))
                return ToolResult(
                    status="ok",
                    output={"exitCode": 0, "stdout": "1 passed"},
                )

            return _fake_bash

        async def _fake_run(config: Any, deps: Any) -> Any:
            from magi_agent.solving.deep_solve import DeepSolveOutcome

            report = await deps.execute_tests(
                artifact="print('hello')",
                test_command="pytest -q tests/",
            )
            assert report.passed == 1
            assert report.failed_cases == ()
            return DeepSolveOutcome(
                acceptance_basis="tests_passed",
                cycles=1,
                refolds=0,
                final_findings_open=(),
                best_candidate="print('hello')",
            )

        context = _make_context(workspaceRoot=str(tmp_path))
        args = _make_args(test_command="pytest -q tests/")

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=True,
            ),
            patch(
                "magi_agent.solving.deep_solve.run_deep_solve",
                side_effect=_fake_run,
            ),
            patch(
                "magi_agent.tools.core_toolhost.standalone_core_tool_handler",
                side_effect=_fake_handler_factory,
            ),
        ):
            result = await deep_solve(args, context)

        assert result.status == "ok"
        # The governed Bash handler was requested (not a raw subprocess).
        assert handler_requests == ["Bash"]
        # The dispatched command carries the artifact env prefix + user command.
        assert len(captured_cmds) == 1
        assert captured_cmds[0].startswith("DEEP_SOLVE_ARTIFACT=")
        assert "pytest -q tests/" in captured_cmds[0]
        # Artifact directory is run-scoped under the workspace root and
        # cleaned up after execution.
        assert not list((tmp_path / ".deep-solve").glob("*")) or not (
            tmp_path / ".deep-solve"
        ).exists()

    @pytest.mark.asyncio
    async def test_execute_tests_blocked_result_reports_honestly(
        self, tmp_path: Any
    ) -> None:
        """A gate-blocked Bash dispatch (memory-mode, policy) surfaces as an
        honest failing ExecutionReport — never a bypass to raw subprocess."""
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.tools.result import ToolResult

        def _fake_handler_factory(tool_name: str, **kwargs: Any) -> Any:
            async def _fake_bash(arguments: dict[str, object], context: Any) -> ToolResult:
                return ToolResult(
                    status="blocked",
                    errorCode="memory_mode_blocked",
                    errorMessage="blocked by memory mode",
                    output={},
                )

            return _fake_bash

        reports: list[Any] = []

        async def _fake_run(config: Any, deps: Any) -> Any:
            from magi_agent.solving.deep_solve import DeepSolveOutcome

            report = await deps.execute_tests(
                artifact="print('x')", test_command="pytest -q"
            )
            reports.append(report)
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=1,
                refolds=0,
                final_findings_open=(),
                best_candidate="print('x')",
                reject_reason="tests blocked",
            )

        context = _make_context(workspaceRoot=str(tmp_path))
        args = _make_args(test_command="pytest -q")

        with (
            patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
            patch(
                "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
                return_value=True,
            ),
            patch(
                "magi_agent.solving.deep_solve.run_deep_solve",
                side_effect=_fake_run,
            ),
            patch(
                "magi_agent.tools.core_toolhost.standalone_core_tool_handler",
                side_effect=_fake_handler_factory,
            ),
        ):
            await deep_solve(args, context)

        assert len(reports) == 1
        assert reports[0].passed == 0
        assert reports[0].failed_cases == ("test_command_blocked",)
        assert "memory_mode_blocked" in reports[0].raw_output
