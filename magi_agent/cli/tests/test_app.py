"""Tests for cli/app.py (Typer entrypoint) and cli/wiring.py (PR-F1).

Tests cover:
- Mode branch: non-interactive -> headless, interactive -> TUI
- Agent default command with stub driver (MAGI_CLI_ENABLED=1)
- Stub subcommands (config, doctor, mcp, auth)
- build_tui_app constructs a MagiTuiApp
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from magi_agent.cli.headless import StubEngineDriver
from magi_agent.cli.wiring import build_headless_runtime


@pytest.fixture(autouse=True)
def _restore_process_env_after_test():
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Import and return the Typer app (deferred to keep test module import cheap)."""
    from magi_agent.cli.app import app
    return app


# ---------------------------------------------------------------------------
# build_headless_runtime
# ---------------------------------------------------------------------------

class TestBuildHeadlessRuntime:
    def test_returns_engine(self) -> None:
        from magi_agent.cli.engine import MagiEngineDriver
        rt = build_headless_runtime(cwd="/tmp", session_id="sid1")
        assert isinstance(rt.engine, MagiEngineDriver)

    def test_returns_gate(self) -> None:
        from magi_agent.cli.permissions import RulesPermissionGate
        rt = build_headless_runtime(cwd="/tmp", session_id="sid2")
        assert isinstance(rt.gate, RulesPermissionGate)

    def test_returns_commands_registry(self) -> None:
        from magi_agent.cli.contracts import CommandRegistry
        rt = build_headless_runtime(cwd="/tmp", session_id="sid3")
        assert isinstance(rt.commands, CommandRegistry)

    def test_returns_session_log(self) -> None:
        from magi_agent.cli.session_log import SessionLog
        rt = build_headless_runtime(cwd="/tmp", session_id="sid4")
        assert isinstance(rt.session_log, SessionLog)

    def test_accepts_permission_mode(self) -> None:
        rt = build_headless_runtime(
            cwd="/tmp", session_id="sid5", permission_mode="bypassPermissions"
        )
        # Just shouldn't raise.
        assert rt.engine is not None

    def test_bypass_permission_mode_resolves_gate_asks(self) -> None:
        import asyncio

        from magi_agent.cli.contracts import ControlRequest

        rt = build_headless_runtime(
            cwd="/tmp", session_id="sid-bypass", permission_mode="bypassPermissions"
        )

        decision = asyncio.run(
            rt.gate.check(
                ControlRequest(
                    request_id="req-1",
                    turn_id="turn-1",
                    tool_name="FileWrite",
                    arguments={"path": "out.txt", "content": "ok"},
                    reason="workspace mutation requires approval",
                )
            )
        )

        assert decision.kind == "allow"

    def test_smart_approve_permission_mode_wires_classifier_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        import asyncio

        from magi_agent.cli.contracts import ControlRequest

        monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "off")

        rt = build_headless_runtime(
            cwd=tmp_path,
            session_id="sid-smart",
            permission_mode="smartApprove",  # type: ignore[arg-type]
            runner=MagicMock(),
        )

        readonly_decision = asyncio.run(
            rt.gate.check(
                ControlRequest(
                    request_id="req-read",
                    turn_id="turn-smart",
                    tool_name="FileRead",
                    arguments={"path": "README.md"},
                    reason="tool_use",
                )
            )
        )
        mutating_decision = asyncio.run(
            rt.gate.check(
                ControlRequest(
                    request_id="req-write",
                    turn_id="turn-smart",
                    tool_name="FileWrite",
                    arguments={"path": "out.txt", "content": "nope"},
                    reason="tool_use",
                )
            )
        )

        assert readonly_decision.kind == "allow"
        assert mutating_decision.kind == "deny"

    def test_accepts_runner_injection(self) -> None:
        """Accepting an explicit runner passes it through to MagiEngineDriver."""
        mock_runner = MagicMock()
        rt = build_headless_runtime(cwd="/tmp", session_id="sid6", runner=mock_runner)
        # The engine should hold the injected runner.
        assert rt.engine._runner is mock_runner

    def test_builds_default_local_runner_when_not_injected(self, tmp_path) -> None:
        """The installed CLI must not construct a no-runner engine by default."""
        rt = build_headless_runtime(cwd=tmp_path, session_id="sid-local-runner")
        assert rt.engine._runner is not None
        assert hasattr(rt.engine._runner, "run_async")

    def test_no_textual_imported(self) -> None:
        """build_headless_runtime must not import textual."""
        for key in list(sys.modules.keys()):
            if key == "textual" or key.startswith("textual."):
                del sys.modules[key]
        build_headless_runtime(cwd="/tmp", session_id="sid-no-tui")
        leaked = [m for m in sys.modules if m == "textual" or m.startswith("textual.")]
        assert not leaked, f"textual leaked: {leaked}"


# ---------------------------------------------------------------------------
# build_tui_app
# ---------------------------------------------------------------------------

class TestBuildTuiApp:
    def test_returns_magi_tui_app(self) -> None:
        from magi_agent.cli.tui.app import MagiTuiApp
        from magi_agent.cli.wiring import build_tui_app
        tui = build_tui_app(cwd="/tmp", session_id="tui-test")
        assert isinstance(tui, MagiTuiApp)

    def test_build_tui_app_threads_mode(self) -> None:
        """``--mode plan`` must reach the App so the topbar honors it (not [act])."""
        from magi_agent.cli.wiring import build_tui_app
        tui = build_tui_app(cwd="/tmp", session_id="tui-mode", mode="plan")
        assert tui._mode == "plan"
        assert "[plan]" in tui._topbar_text()

    def test_build_tui_app_uses_resolved_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The topbar/footer show the RESOLVED model, not the raw (None) flag.

        An explicit ``runner`` is injected so the runner-build path (which also
        reads ``resolve_provider_config``) is skipped — this isolates the new
        display-only resolve in ``build_tui_app``.
        """
        from magi_agent.cli import providers as providers_mod
        from magi_agent.cli.wiring import build_tui_app

        monkeypatch.setattr(
            providers_mod,
            "resolve_provider_config",
            lambda *, model_override=None: SimpleNamespace(model="claude-x"),
        )
        tui = build_tui_app(
            cwd="/tmp", session_id="tui-rm", model=None, runner=MagicMock()
        )
        # Footer is fed self._model at compose, so asserting _model covers both.
        assert tui._model == "claude-x"
        assert "claude-x" in tui._topbar_text()

    def test_build_tui_app_unconfigured_model_shows_no_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unconfigured machine (resolve -> None) keeps the honest 'no model'."""
        from magi_agent.cli import providers as providers_mod
        from magi_agent.cli.wiring import build_tui_app

        monkeypatch.setattr(
            providers_mod,
            "resolve_provider_config",
            lambda *, model_override=None: None,
        )
        tui = build_tui_app(
            cwd="/tmp", session_id="tui-nm", model=None, runner=MagicMock()
        )
        assert tui._model is None
        assert "no model" in tui._topbar_text()

    def test_build_tui_app_at_provider_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """With MAGI_TUI_FILE_MENTIONS OFF, @ stays dead-but-silent (no provider)."""
        from magi_agent.cli.wiring import build_tui_app

        # MAGI_TUI_FILE_MENTIONS is now profile-default-ON (no-default-off), so a
        # bare delenv leaves the file-mention provider active. This test covers
        # the OFF path, so pin the flag explicitly OFF.
        monkeypatch.setenv("MAGI_TUI_FILE_MENTIONS", "0")
        (tmp_path / "readme.md").write_text("x", encoding="utf-8")
        tui = build_tui_app(
            cwd=tmp_path, session_id="atoff", runner=MagicMock()
        )
        assert tui._router.route("@readme").results == []

    def test_build_tui_app_at_provider_lists_files_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """With the flag ON, typing @ lists workspace files."""
        from magi_agent.cli.wiring import build_tui_app

        monkeypatch.setenv("MAGI_TUI_FILE_MENTIONS", "1")
        (tmp_path / "readme.md").write_text("x", encoding="utf-8")
        tui = build_tui_app(
            cwd=tmp_path, session_id="aton", runner=MagicMock()
        )
        results = tui._router.route("@readme").results
        assert results
        assert any("readme.md" in c.value for c in results)

    def test_build_tui_app_defaults_cwd_to_getcwd_for_at(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Production path: no cwd= passed -> build_tui_app defaults to os.getcwd()."""
        from magi_agent.cli.wiring import build_tui_app

        monkeypatch.setenv("MAGI_TUI_FILE_MENTIONS", "1")
        (tmp_path / "readme.md").write_text("x", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        tui = build_tui_app(session_id="atcwd", runner=MagicMock())
        results = tui._router.route("@readme").results
        assert results
        assert any("readme.md" in c.value for c in results)

    def test_build_tui_app_hash_stays_dead_but_silent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Even with @ enabled, # has no provider injected -> no results."""
        from magi_agent.cli.wiring import build_tui_app

        monkeypatch.setenv("MAGI_TUI_FILE_MENTIONS", "1")
        tui = build_tui_app(
            cwd=tmp_path, session_id="athash", runner=MagicMock()
        )
        assert tui._router.route("#foo").results == []

    def test_engine_is_same_type_as_headless(self) -> None:
        """TUI uses MagiEngineDriver — same engine class as headless."""
        from magi_agent.cli.engine import MagiEngineDriver
        from magi_agent.cli.wiring import build_tui_app
        tui = build_tui_app(cwd="/tmp", session_id="tui-engine-check")
        assert isinstance(tui._engine, MagiEngineDriver)

    def test_app_sink_is_attached_to_gate(self) -> None:
        """FIX 2: the app's TextualSink must be wired into the gate's sinks.

        Without this, an ``ask`` verdict resolves to safe-deny and the
        ToolUseConfirm modal never appears in production.
        """
        from magi_agent.cli.wiring import build_tui_app
        tui = build_tui_app(cwd="/tmp", session_id="tui-sink-check")
        assert hasattr(tui._gate, "sinks")
        assert tui.sink in tui._gate.sinks

    def test_bypass_permission_mode_does_not_attach_tui_modal_sink(self) -> None:
        """bypassPermissions must not race the TUI modal sink.

        The bypass sink resolves asks without a frame; adding the Textual sink as a
        loser can still push ToolUseConfirm before cancellation reaches it.
        """
        from magi_agent.cli.contracts import ControlRequest
        from magi_agent.cli.permissions import HeadlessSink
        from magi_agent.cli.wiring import build_tui_app

        tui = build_tui_app(
            cwd="/tmp",
            session_id="tui-bypass-sink-check",
            permission_mode="bypassPermissions",
        )

        assert hasattr(tui._gate, "sinks")
        assert tui.sink not in tui._gate.sinks
        assert any(
            isinstance(sink, HeadlessSink)
            and sink.permission_mode == "bypassPermissions"
            for sink in tui._gate.sinks
        )
        decision = asyncio.run(
            tui._gate.check(
                ControlRequest(
                    requestId="req-browser",
                    turnId="turn-browser",
                    toolName="BrowserTask",
                    arguments={"task": "open example.com"},
                    reason="tool_use",
                )
            )
        )
        assert decision.kind == "allow"

    def test_runtime_runner_receives_composio_toolsets_without_explicit_runner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        from types import ModuleType

        from magi_agent.cli.wiring import build_tui_app

        class Agent:
            def __init__(self) -> None:
                self.tools: list[object] = []

        class Runner:
            def __init__(self) -> None:
                self.agent = Agent()

        class RuntimeWithRunner:
            def __init__(self) -> None:
                self.runner = Runner()

        class FakeBundle:
            active = True
            status = "ready"
            toolsets = ("composio-toolset",)
            mcp_server_label = "composio"

        class FakeMagiTuiApp:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs
                self._gate = kwargs["gate"]
                self.sink = object()

        fake_app_module = ModuleType("magi_agent.cli.tui.app")
        fake_app_module.MagiTuiApp = FakeMagiTuiApp
        fake_tool_render_module = ModuleType("magi_agent.cli.tui.tool_render")
        fake_tool_render_module.build_tool_renderers = lambda: {}
        monkeypatch.setitem(sys.modules, fake_app_module.__name__, fake_app_module)
        monkeypatch.setitem(
            sys.modules,
            fake_tool_render_module.__name__,
            fake_tool_render_module,
        )
        runtime = RuntimeWithRunner()

        monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")

        with patch(
            "magi_agent.cli.wiring.build_composio_toolset_bundle",
            return_value=FakeBundle(),
        ):
            tui = build_tui_app(
                cwd=tmp_path,
                session_id="sid-runtime-runner",
                runtime=runtime,
            )

        assert runtime.runner.agent.tools == ["composio-toolset"]
        assert tui.kwargs["runtime"] is runtime


# ---------------------------------------------------------------------------
# Mode branch: non-interactive -> headless
# ---------------------------------------------------------------------------

class TestModeBranchNonInteractive:
    def test_non_interactive_stdin_calls_headless(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """When stdin is not a tty, the headless branch is chosen."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        headless_called = []
        tui_called = []

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            headless_called.append(prompt)
            return 0

        def fake_build_tui(*args, **kwargs):
            tui_called.append(True)
            m = MagicMock()
            m.run = MagicMock()
            return m

        runner = CliRunner()
        # CliRunner uses StringIO for stdin — not a tty, so isatty() -> False.
        with patch("magi_agent.cli.app.run_headless", fake_headless), \
             patch("magi_agent.cli.app.build_tui_app", fake_build_tui):
            result = runner.invoke(_make_app(), ["hello world"], catch_exceptions=False)

        assert headless_called, f"Headless was not called; tui_called={tui_called}"
        assert not tui_called, "TUI was called in non-interactive mode"

    def test_prompt_arg_forces_headless(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """An explicit prompt argument forces headless even if stdin were a tty."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        headless_called = []
        tui_called = []

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            headless_called.append(prompt)
            return 0

        def fake_build_tui(*args, **kwargs):
            tui_called.append(True)
            m = MagicMock()
            m.run = MagicMock()
            return m

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless), \
             patch("magi_agent.cli.app.build_tui_app", fake_build_tui):
            result = runner.invoke(_make_app(), ["my prompt"], catch_exceptions=False)

        assert headless_called, "Headless not called with explicit prompt"
        assert not tui_called

    def test_print_flag_forces_headless(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """--print / -p flag forces non-interactive (headless) path."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        headless_called = []

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            headless_called.append(True)
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["-p", "hello"], catch_exceptions=False)

        assert headless_called, "-p flag did not trigger headless path"


# ---------------------------------------------------------------------------
# Mode branch: interactive -> TUI
# ---------------------------------------------------------------------------

class TestModeBranchInteractive:
    def test_interactive_no_prompt_calls_tui(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """When stdin IS a tty and no prompt/--print, TUI is launched."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        tui_run_called = []
        headless_called = []

        async def fake_headless(*args, **kw):
            headless_called.append(True)
            return 0

        def fake_build_tui(*args, **kwargs):
            m = MagicMock()
            def fake_run():
                tui_run_called.append(True)
            m.run = fake_run
            return m

        # The app does `sys.stdin.isatty()`. CliRunner replaces sys.stdin with
        # a BytesIO, which has no isatty() method. We patch the sys module that
        # the app imports (its own `sys` reference) by replacing sys.stdin with
        # a fake that returns True for isatty().
        import magi_agent.cli.app as app_module

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdin.read.return_value = ""

        with patch("magi_agent.cli.app.run_headless", fake_headless), \
             patch("magi_agent.cli.app.build_tui_app", fake_build_tui), \
             patch.object(app_module, "sys", wraps=app_module.sys) as mock_sys:
            mock_sys.stdin = fake_stdin
            runner = CliRunner()
            result = runner.invoke(app_module.app, [], catch_exceptions=False)

        # In interactive mode (no prompt, no -p, isatty=True) TUI should run.
        assert tui_run_called, \
            f"TUI was not launched in interactive mode; headless_called={headless_called}, output={result.output}"
        assert not headless_called, "Headless should not run in interactive mode"

    def _capture_tui_kwargs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, extra_args: list[str]
    ) -> dict[str, Any]:
        """Invoke the interactive TUI path and return ALL kwargs build_tui_app got."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        captured: dict[str, Any] = {}

        def fake_build_tui(*args, **kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.run = MagicMock()
            return m

        import magi_agent.cli.app as app_module

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdin.read.return_value = ""

        with patch("magi_agent.cli.app.build_tui_app", fake_build_tui), \
             patch.object(app_module, "sys", wraps=app_module.sys) as mock_sys:
            mock_sys.stdin = fake_stdin
            runner = CliRunner()
            runner.invoke(app_module.app, extra_args, catch_exceptions=False)
        return captured

    def _launch_tui(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, extra_args: list[str]
    ) -> str:
        """Invoke the interactive TUI path and return the permission_mode passed."""
        return self._capture_tui_kwargs(
            monkeypatch, tmp_path, extra_args
        ).get("permission_mode")

    def test_interactive_threads_cwd_into_build_tui_app(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The interactive caller must thread cwd so the @-provider has a real root."""
        captured = self._capture_tui_kwargs(monkeypatch, tmp_path, [])
        assert captured.get("cwd") == os.getcwd()

    def test_interactive_default_upgrades_to_bypass_permissions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Bare `magi` (no --permission-mode) launches the TUI in bypassPermissions."""
        assert self._launch_tui(monkeypatch, tmp_path, []) == "bypassPermissions"

    def test_interactive_explicit_accept_edits_is_honored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """An explicit --permission-mode acceptEdits is passed through unchanged."""
        assert (
            self._launch_tui(
                monkeypatch, tmp_path, ["--permission-mode", "acceptEdits"]
            )
            == "acceptEdits"
        )

    def test_interactive_explicit_mode_is_honored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """An explicit non-default --permission-mode is passed through unchanged."""
        assert (
            self._launch_tui(
                monkeypatch, tmp_path, ["--permission-mode", "bypassPermissions"]
            )
            == "bypassPermissions"
        )

    def test_interactive_explicit_default_mode_is_honored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """An explicit --permission-mode default keeps strict prompt mode."""
        assert (
            self._launch_tui(monkeypatch, tmp_path, ["--permission-mode", "default"])
            == "default"
        )


# ---------------------------------------------------------------------------
# Agent default command: real headless turn with stub driver
# ---------------------------------------------------------------------------

class TestAgentDefaultCommand:
    def test_permission_mode_help_exposes_smart_approve(self) -> None:
        runner = CliRunner()
        result = runner.invoke(_make_app(), ["agent", "--help"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "smartApprove" in result.output

    def test_agent_model_help_uses_current_default_example(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            _make_app(),
            ["agent", "--help"],
            catch_exceptions=False,
            terminal_width=120,
        )

        assert result.exit_code == 0, result.output
        assert "claude-sonnet-5" in result.output
        assert "not yet fully wired" not in result.output
        assert "claude-sonnet-4-5" not in result.output

    def test_legalbench_model_help_uses_current_default_example(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            _make_app(),
            ["legalbench", "--help"],
            catch_exceptions=False,
            terminal_width=120,
        )

        assert result.exit_code == 0, result.output
        assert "claude-sonnet-5" in result.output
        assert "claude-sonnet-4-5" not in result.output

    def test_smart_approve_permission_mode_reaches_headless(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """The public CLI must accept and forward --permission-mode smartApprove."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        captured: dict[str, object] = {}

        def fake_build_headless_runtime(**kwargs: object) -> object:
            captured["build_permission_mode"] = kwargs["permission_mode"]
            return SimpleNamespace(
                gate=object(),
                commands=object(),
                engine=StubEngineDriver(text="ok"),
                session_log=SimpleNamespace(path=tmp_path / "sid-smart"),
                mcp_servers=(),
            )

        async def fake_headless(prompt: str, **kwargs: object) -> int:
            captured["prompt"] = prompt
            captured["run_permission_mode"] = kwargs["permission_mode"]
            return 0

        runner = CliRunner()
        with patch(
            "magi_agent.cli.app.build_headless_runtime",
            fake_build_headless_runtime,
        ), patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(
                _make_app(),
                ["--permission-mode", "smartApprove", "hello"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert captured == {
            "build_permission_mode": "smartApprove",
            "prompt": "hello",
            "run_permission_mode": "smartApprove",
        }

    def test_agent_command_applies_local_full_runtime_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        monkeypatch.delenv("MAGI_RUNNER_POLICY_ROUTING_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_COMPOSIO_ENABLED", raising=False)
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        captured: dict[str, object] = {}

        def fake_build_headless_runtime(**kwargs: object) -> object:
            captured["build_permission_mode"] = kwargs.get("permission_mode")
            captured["runner_policy_routing_enabled"] = kwargs.get(
                "runner_policy_routing_enabled"
            )
            return SimpleNamespace(
                gate=object(),
                commands=object(),
                engine=StubEngineDriver(text="ok"),
                session_log=SimpleNamespace(path=tmp_path / "sid-policy"),
                mcp_servers=(),
            )

        async def fake_headless(prompt: str, **kwargs: object) -> int:
            captured["prompt"] = prompt
            captured["run_permission_mode"] = kwargs.get("permission_mode")
            return 0

        runner = CliRunner()
        with patch(
            "magi_agent.cli.app.build_headless_runtime",
            fake_build_headless_runtime,
        ), patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(
                _make_app(),
                ["hello"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert captured == {
            "build_permission_mode": "bypassPermissions",
            "runner_policy_routing_enabled": True,
            "prompt": "hello",
            "run_permission_mode": "bypassPermissions",
        }
        assert os.environ["MAGI_RUNNER_POLICY_ROUTING_ENABLED"] == "1"
        assert os.environ["MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED"] == "0"
        assert os.environ["MAGI_GA_LIVE_ENABLED"] == "1"

    def test_agent_command_normalizes_eval_runtime_profile(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        monkeypatch.delenv("MAGI_RUNNER_POLICY_ROUTING_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
        monkeypatch.setenv("MAGI_RUNTIME_PROFILE", " EVAL ")
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        captured: dict[str, object] = {}

        def fake_build_headless_runtime(**kwargs: object) -> object:
            captured["build_permission_mode"] = kwargs["permission_mode"]
            captured["runner_policy_routing_enabled"] = kwargs.get(
                "runner_policy_routing_enabled"
            )
            return SimpleNamespace(
                gate=object(),
                commands=object(),
                engine=StubEngineDriver(text="ok"),
                session_log=SimpleNamespace(path=tmp_path / "sid-eval"),
                mcp_servers=(),
            )

        async def fake_headless(prompt: str, **kwargs: object) -> int:
            captured["prompt"] = prompt
            captured["run_permission_mode"] = kwargs["permission_mode"]
            return 0

        runner = CliRunner()
        with patch(
            "magi_agent.cli.app.build_headless_runtime",
            fake_build_headless_runtime,
        ), patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(
                _make_app(),
                ["hello"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert captured == {
            "build_permission_mode": "bypassPermissions",
            "runner_policy_routing_enabled": False,
            "prompt": "hello",
            "run_permission_mode": "bypassPermissions",
        }
        assert os.environ["MAGI_GA_LIVE_ENABLED"] == "0"
        assert os.environ["MAGI_RUNNER_POLICY_ROUTING_ENABLED"] == "0"
        assert "MAGI_COMPOSIO_ENABLED" not in os.environ

    def test_agent_command_runs_headless_turn(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """The default agent command runs run_headless via the real wiring, exit 0."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        captured = {}

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            captured["prompt"] = prompt
            captured["output"] = output
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["hello from test"], catch_exceptions=False)

        assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
        assert captured.get("prompt") == "hello from test"

    def test_output_flag_forwarded(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """--output flag is passed through to run_headless."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        captured = {}

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            captured["output"] = output
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            # Flags must come before the positional prompt argument.
            result = runner.invoke(_make_app(), ["--output", "json", "hi"], catch_exceptions=False)

        assert captured.get("output") == "json", f"captured={captured}, output={result.output}"

    def test_real_stub_driver_end_to_end(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """End-to-end: inject StubEngineDriver, check output is produced."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        buf = io.StringIO()

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            # Use the real headless engine with a stub driver, capturing to buf.
            from magi_agent.cli.headless import run_headless
            return await run_headless(
                prompt,
                output="text",
                driver=StubEngineDriver(text="stub reply"),
                stream=buf,
            )

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["test query"], catch_exceptions=False)

        assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
        assert "stub reply" in buf.getvalue()

    def test_real_default_cli_prompt_does_not_return_no_runner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """Regression: the shipped CLI must run a turn without injected mocks."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        # Pin the no-provider path: clear provider keys and point config
        # resolution at a non-existent file so the stub runner is selected
        # deterministically regardless of the developer's environment.
        for _env in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "FIREWORKS_API_KEY",
            "MAGI_PROVIDER",
        ):
            monkeypatch.delenv(_env, raising=False)
        monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))

        runner = CliRunner()
        result = runner.invoke(
            _make_app(),
            ["--output", "json", "hello"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert "no_runner" not in result.output
        assert "Local ADK runtime" in result.output


# ---------------------------------------------------------------------------
# Stub subcommands
# ---------------------------------------------------------------------------

class TestStubSubcommands:
    @pytest.mark.parametrize("subcmd", ["config", "mcp"])
    def test_stub_subcommand_actually_runs(self, subcmd: str) -> None:
        """Stub subcommands (config/mcp) must INVOKE the subcommand.

        Regression: previously the root-callback positional ``[prompt]`` arg
        shadowed these subcommands — ``magi config`` ran the agent with
        ``prompt="config"`` and the stub body never fired. The
        ``DefaultCommandGroup`` routes a known subcommand name to that
        subcommand, so the echo ("not yet implemented") must actually print and
        the command must exit 0 — even with the CLI enabled.
        """
        runner = CliRunner()
        result = runner.invoke(
            _make_app(), [subcmd], env={"MAGI_CLI_ENABLED": "1"}, catch_exceptions=False
        )
        assert result.exit_code == 0, \
            f"Subcommand '{subcmd}' did not run cleanly: exit {result.exit_code}\n{result.output}"
        assert "not yet implemented" in result.output, \
            f"Subcommand '{subcmd}' stub body did not run; output:\n{result.output}"

    def test_doctor_subcommand_no_longer_routes_to_agent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            _make_app(), ["doctor"], env={"MAGI_CLI_ENABLED": "1"}, catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "Composio:" in result.output

    def test_auth_subcommand_no_longer_routes_to_agent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            _make_app(), ["auth"], env={"MAGI_CLI_ENABLED": "1"}, catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "magi auth" in result.output


# ---------------------------------------------------------------------------
# Default-command routing (DefaultCommandGroup)
# ---------------------------------------------------------------------------

class TestDefaultCommandRouting:
    def test_bare_prompt_routes_to_agent_headless(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A bare prompt (not a subcommand, not a flag) routes to the AGENT.

        ``magi "hello world"`` must reach run_headless with the prompt — NOT a
        subcommand. Asserts run_headless was called with prompt "hello world".
        """
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        captured = {}

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            captured["prompt"] = prompt
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["hello world"], catch_exceptions=False)

        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
        assert captured.get("prompt") == "hello world", f"captured={captured}"

    def test_unknown_first_token_reaches_agent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A first token that is NOT a subcommand falls back to the agent."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        captured = {}

        async def fake_headless(prompt, *, output, gate, commands, driver, session_id, stream, **kw):
            captured["prompt"] = prompt
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["summarize this repo"], catch_exceptions=False)

        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
        assert captured.get("prompt") == "summarize this repo", f"captured={captured}"

    def test_known_first_token_reaches_subcommand(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A first token that IS a subcommand reaches the subcommand, not the agent."""
        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

        headless_called = []

        async def fake_headless(*args, **kw):
            headless_called.append(True)
            return 0

        runner = CliRunner()
        with patch("magi_agent.cli.app.run_headless", fake_headless):
            result = runner.invoke(_make_app(), ["doctor"], catch_exceptions=False)

        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
        assert "Composio:" in result.output, f"output:\n{result.output}"
        assert not headless_called, "Subcommand wrongly routed to the agent (headless)"


# ---------------------------------------------------------------------------
# main() entry in cli/__main__.py
# ---------------------------------------------------------------------------

class TestMainEntry:
    def test_main_exists_and_is_callable(self) -> None:
        from magi_agent.cli.__main__ import main
        assert callable(main)

    def test_version_flag_exits_zero_via_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() with --version exits 0 via stdlib-only fast path."""
        monkeypatch.setattr(sys, "argv", ["magi", "--version"])
        with pytest.raises(SystemExit) as exc_info:
            from magi_agent.cli.__main__ import main
            main()
        assert exc_info.value.code == 0

    def test_version_short_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """-V also triggers the version fast path."""
        monkeypatch.setattr(sys, "argv", ["magi", "-V"])
        with pytest.raises(SystemExit) as exc_info:
            from magi_agent.cli.__main__ import main
            main()
        assert exc_info.value.code == 0


def test_build_headless_runtime_attaches_composio_to_injected_runner(monkeypatch, tmp_path) -> None:
    class Agent:
        def __init__(self) -> None:
            self.tools: list[object] = []

    class Runner:
        def __init__(self) -> None:
            self.agent = Agent()

    class FakeBundle:
        active = True
        status = "ready"
        toolsets = ("composio-toolset",)
        mcp_server_label = "composio"

    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    runner = Runner()

    with patch("magi_agent.cli.wiring.build_composio_toolset_bundle", return_value=FakeBundle()):
        rt = build_headless_runtime(cwd=tmp_path, session_id="sid-composio", runner=runner)

    assert runner.agent.tools == ["composio-toolset"]
    assert rt.composio.status == "ready"
    assert rt.mcp_servers == ("composio",)


def test_build_headless_runtime_attaches_composio_to_default_local_runner(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeBundle:
        active = True
        status = "ready"
        toolsets = ("composio-toolset",)
        mcp_server_label = "composio"

    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "MAGI_PROVIDER",
        "MAGI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    with patch(
        "magi_agent.cli.wiring.build_composio_toolset_bundle",
        return_value=FakeBundle(),
    ):
        rt = build_headless_runtime(cwd=tmp_path, session_id="sid-composio-none")

    assert rt.composio.status == "ready"
    assert rt.mcp_servers == ("composio",)
    assert getattr(rt.engine._runner, "agent").tools == ["composio-toolset"]


def test_build_headless_runtime_default_runner_attaches_first_party_tools(
    monkeypatch,
    tmp_path,
) -> None:
    import magi_agent.cli.real_runner as real_runner
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    class FakeLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream: bool = False):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="ok")],
                )
            )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        real_runner,
        "_build_litellm_model",
        lambda _config: FakeLlm(model="fake"),
    )

    rt = build_headless_runtime(cwd=tmp_path, session_id="sid-first-party")

    tool_names = {tool.name for tool in getattr(rt.engine._runner, "agent").tools}
    assert {
        "FileRead",
        "Grep",
        "Bash",
        "Browser",
        "DocumentWrite",
        "AgentMemorySearch",
        "SkillLoader",
    }.issubset(tool_names)


def test_build_headless_runtime_composio_appends_to_first_party_tools(
    monkeypatch,
    tmp_path,
) -> None:
    import magi_agent.cli.real_runner as real_runner
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    class FakeLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream: bool = False):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="ok")],
                )
            )

    class FakeBundle:
        active = True
        status = "ready"
        toolsets = ("composio-toolset",)
        mcp_server_label = "composio"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setattr(
        real_runner,
        "_build_litellm_model",
        lambda _config: FakeLlm(model="fake"),
    )

    with patch(
        "magi_agent.cli.wiring.build_composio_toolset_bundle",
        return_value=FakeBundle(),
    ):
        rt = build_headless_runtime(cwd=tmp_path, session_id="sid-composio-first-party")

    tools = getattr(rt.engine._runner, "agent").tools
    tool_names = {tool.name for tool in tools if hasattr(tool, "name")}
    assert "FileRead" in tool_names
    assert "Browser" in tool_names
    assert "composio-toolset" in tools
    assert rt.mcp_servers == ("composio",)


def test_build_headless_runtime_does_not_report_mcp_when_runner_has_no_agent(
    monkeypatch,
    tmp_path,
) -> None:
    class RunnerWithoutAgent:
        pass

    class FakeBundle:
        active = True
        status = "ready"
        toolsets = ("composio-toolset",)
        mcp_server_label = "composio"

    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")

    with patch(
        "magi_agent.cli.wiring.build_composio_toolset_bundle",
        return_value=FakeBundle(),
    ):
        rt = build_headless_runtime(
            cwd=tmp_path,
            session_id="sid-composio-no-agent",
            runner=RunnerWithoutAgent(),
        )

    assert rt.composio.status == "ready"
    assert rt.mcp_servers == ()


def test_local_serve_applies_full_runtime_defaults_without_route_hardblock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import magi_agent.main as main_module
    from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL
    from magi_agent.config.serve_token import local_serve_gateway_token

    # P0: use the per-install local serve token rather than the old constant.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MAGI_CONFIG", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE", raising=False)
    local_serve_gateway_token.cache_clear()
    local_token = local_serve_gateway_token()

    monkeypatch.delenv("MAGI_RUNNER_POLICY_ROUTING_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_COMPOSIO_ENABLED", raising=False)
    monkeypatch.setenv("BOT_ID", "local-bot")
    monkeypatch.setenv("USER_ID", "local-user")
    monkeypatch.setenv("GATEWAY_TOKEN", local_token)
    monkeypatch.setenv("CORE_AGENT_API_PROXY_URL", "http://127.0.0.1:0")
    monkeypatch.setenv("CORE_AGENT_CHAT_PROXY_URL", "http://127.0.0.1:0")
    monkeypatch.setenv("CORE_AGENT_REDIS_URL", "redis://127.0.0.1:0/0")
    monkeypatch.setenv("CORE_AGENT_MODEL", LOCAL_DEV_MODEL_SENTINEL)
    monkeypatch.setenv("CORE_AGENT_VERSION", "test-local")

    with patch.object(main_module, "create_app", return_value=object()), patch.object(
        main_module.uvicorn,
        "run",
    ) as run, patch.object(main_module, "_print_local_startup_notice"):
        main_module.main(["serve", "--port", "0"])

    run.assert_called_once()
    assert os.environ["MAGI_RUNNER_POLICY_ROUTING_ENABLED"] == "1"
    assert os.environ["MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED"] == "0"
    assert os.environ["MAGI_GA_LIVE_ENABLED"] == "1"
    assert os.environ["MAGI_BROWSER_TOOL_ENABLED"] == "1"
    assert os.environ["MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"] == "1"
    assert "MAGI_COMPOSIO_ENABLED" not in os.environ
    local_serve_gateway_token.cache_clear()
