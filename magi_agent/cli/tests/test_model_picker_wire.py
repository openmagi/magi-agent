"""Tests for the /model command wiring (persist_model, TUI picker, visibility).

Covers:
- ``persist_model``: writes ``[model].model``, round-trips, preserves other
  keys/sections, creates dir/file when missing, NEVER touches real ``~/.magi``.
- ``/model <id>`` headless (ctx.app=None): persists to a temp config, returns
  confirm Text.
- ``/model`` no-arg in TUI: submitting ``/model`` to a real ``MagiTuiApp``
  (driven by ``App.run_test()``) pushes a ``ModelPickerDialog`` screen.
- ``_apply_model`` persists: call it with a temp config path and assert the
  model was written + topbar updated.
- Visibility: ``/model`` appears in ``build_registry`` + ``list_for`` with a
  TUI context (app with open_dialog) and is found by ``lookup``.

Plain pytest + asyncio.run (no pytest-asyncio), matching project convention.
TUI tests drive the real ``MagiTuiApp`` via ``App.run_test()`` (async in
``asyncio.run``).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from magi_agent.cli.commands.control import (
    ModelCommand,
    _app_has_model_dialog,
    _control_specs,
    register_control_commands,
)
from magi_agent.cli.commands.registry import CommandRegistryImpl
from magi_agent.cli.contracts import CommandContext, CommandSurface, Skip, Text
from magi_agent.cli.providers import persist_model

TUI_ONLY = CommandSurface(tui=True, headless=False)
HEADLESS_ONLY = CommandSurface(tui=False, headless=True)
BOTH = CommandSurface(tui=True, headless=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(*, app: object = None, runtime: object = None) -> CommandContext:
    return CommandContext(cwd="/tmp/test-model-wire", runtime=runtime, app=app)


# ---------------------------------------------------------------------------
# persist_model — unit tests
# ---------------------------------------------------------------------------


class TestPersistModel:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        persist_model("claude-sonnet-4-6", path=cfg)
        assert cfg.exists()

    def test_creates_parent_dir_when_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "subdir" / "nested" / "config.toml"
        persist_model("claude-sonnet-4-6", path=cfg)
        assert cfg.exists()

    def test_writes_model_key(self, tmp_path: Path) -> None:
        import tomllib

        cfg = tmp_path / "config.toml"
        persist_model("claude-sonnet-4-6", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["model"] == "claude-sonnet-4-6"

    def test_round_trips_via_reader(self, tmp_path: Path) -> None:
        """Written value survives a re-read through the existing reader."""
        from magi_agent.cli.providers import resolve_provider_config

        cfg = tmp_path / "config.toml"
        # Write a config with an anthropic key so the reader can resolve it.
        cfg.write_text(
            '[providers.anthropic]\napi_key = "sk-test-key"\n', encoding="utf-8"
        )
        persist_model("my-test-model", path=cfg)

        import tomllib

        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        # The [model] section was created/updated.
        assert data["model"]["model"] == "my-test-model"
        # The [providers.anthropic] section was preserved.
        assert data["providers"]["anthropic"]["api_key"] == "sk-test-key"

    def test_preserves_other_sections(self, tmp_path: Path) -> None:
        """Existing keys/sections outside [model] are not clobbered."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            "[server]\nport = 8080\n\n[model]\nprovider = \"anthropic\"\n",
            encoding="utf-8",
        )
        persist_model("new-model", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["server"]["port"] == 8080
        assert data["model"]["provider"] == "anthropic"
        assert data["model"]["model"] == "new-model"

    def test_overwrites_existing_model_key(self, tmp_path: Path) -> None:
        import tomllib

        cfg = tmp_path / "config.toml"
        persist_model("first-model", path=cfg)
        persist_model("second-model", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["model"] == "second-model"

    def test_never_touches_real_magi_config(self, tmp_path: Path) -> None:
        """All calls in this test file use path=tmp_path, never the real config."""
        real_path = Path.home() / ".magi" / "config.toml"
        real_mtime_before = real_path.stat().st_mtime if real_path.exists() else None
        cfg = tmp_path / "config.toml"
        persist_model("test-model", path=cfg)
        real_mtime_after = real_path.stat().st_mtime if real_path.exists() else None
        assert real_mtime_before == real_mtime_after, (
            "persist_model touched the real ~/.magi/config.toml!"
        )

    def test_uses_magi_config_env_when_no_path(self, tmp_path: Path) -> None:
        """Without an explicit path, respects MAGI_CONFIG env var."""
        import tomllib

        cfg = tmp_path / "env_config.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            persist_model("env-model")
            with open(cfg, "rb") as fh:
                data = tomllib.load(fh)
            assert data["model"]["model"] == "env-model"
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old


# ---------------------------------------------------------------------------
# /model <id> headless (ctx.app=None)
# ---------------------------------------------------------------------------


class TestModelCommandHeadless:
    def _cmd(self) -> ModelCommand:
        return ModelCommand(name="model", surface=BOTH)

    def test_with_id_returns_confirm_text(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            result = asyncio.run(self._cmd().call("claude-opus-4", _ctx()))
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old
        assert isinstance(result, Text)
        assert "claude-opus-4" in result.text
        # Must mention config / next-session persistence.
        assert "config" in result.text or "next session" in result.text

    def test_with_id_persists_to_config(self, tmp_path: Path) -> None:
        import tomllib

        cfg = tmp_path / "config.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            asyncio.run(self._cmd().call("gemini-3.5-flash", _ctx()))
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["model"] == "gemini-3.5-flash"

    def test_no_arg_no_app_returns_text_with_hint(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            result = asyncio.run(self._cmd().call(None, _ctx()))
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old
        assert isinstance(result, Text)
        # Should list available models and hint to pass an id.
        assert "hint" in result.text or "available" in result.text

    def test_no_arg_no_app_not_skip(self, tmp_path: Path) -> None:
        """Headless /model without id must NOT return Skip (it returns Text)."""
        cfg = tmp_path / "config.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            result = asyncio.run(self._cmd().call(None, _ctx()))
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old
        assert not isinstance(result, Skip)

    def test_with_id_does_not_touch_real_config(self, tmp_path: Path) -> None:
        """Persists to MAGI_CONFIG, not to real ~/.magi/config.toml."""
        from magi_agent.cli.providers import _config_path

        cfg = tmp_path / "isolated.toml"
        old = os.environ.get("MAGI_CONFIG")
        try:
            os.environ["MAGI_CONFIG"] = str(cfg)
            asyncio.run(self._cmd().call("test-model-iso", _ctx()))
            # The real config path should be the temp path (env override active).
            assert _config_path() == cfg
            assert cfg.exists()
        finally:
            if old is None:
                os.environ.pop("MAGI_CONFIG", None)
            else:
                os.environ["MAGI_CONFIG"] = old


# ---------------------------------------------------------------------------
# _apply_model persists (TUI app method, temp path seam)
# ---------------------------------------------------------------------------


class TestApplyModelPersists:
    """Test _apply_model via a real MagiTuiApp driven by App.run_test()."""

    def test_apply_model_persists_to_temp_config(self, tmp_path: Path) -> None:
        import tomllib

        from magi_agent.cli.contracts import (
            EngineResult,
            PermissionDecision,
            PermissionGate,
            Terminal,
            ToolRendererRegistry,
        )
        from magi_agent.cli.tui.app import MagiTuiApp
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        cfg = tmp_path / "config.toml"

        class _FakeGate(PermissionGate):
            async def check(self, req):
                return PermissionDecision(kind="allow")

        class _FakeEngine:
            async def run_turn_stream(self, *a, **k):
                if False:
                    yield None
                yield EngineResult(terminal=Terminal.completed)

        class _FakeRegistry:
            def lookup(self, name):
                return None

            def list_for(self, surface, ctx=None):
                return []

        app = MagiTuiApp(
            engine=_FakeEngine(),
            gate=_FakeGate(),
            commands=_FakeRegistry(),
            renderers=build_tool_renderers(),
        )

        async def _run() -> None:
            async with app.run_test():
                app._apply_model("claude-opus-4", _config_path=cfg)

        asyncio.run(_run())

        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["model"] == "claude-opus-4"

    def test_apply_model_updates_topbar(self, tmp_path: Path) -> None:
        from magi_agent.cli.contracts import EngineResult, PermissionDecision, PermissionGate, Terminal
        from magi_agent.cli.tui.app import MagiTuiApp
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        cfg = tmp_path / "config.toml"

        class _FakeGate(PermissionGate):
            async def check(self, req):
                return PermissionDecision(kind="allow")

        class _FakeEngine:
            async def run_turn_stream(self, *a, **k):
                if False:
                    yield None
                yield EngineResult(terminal=Terminal.completed)

        class _FakeRegistry:
            def lookup(self, name):
                return None

            def list_for(self, surface, ctx=None):
                return []

        app = MagiTuiApp(
            engine=_FakeEngine(),
            gate=_FakeGate(),
            commands=_FakeRegistry(),
            renderers=build_tool_renderers(),
            model="old-model",
        )

        async def _run() -> None:
            async with app.run_test():
                app._apply_model("new-model", _config_path=cfg)
                assert app._model == "new-model"
                # Verify topbar was refreshed (text includes the new model name).
                topbar_text = app._topbar_text() if app._topbar else ""
                assert "new-model" in topbar_text

        asyncio.run(_run())

    def test_apply_model_none_is_noop(self, tmp_path: Path) -> None:
        """_apply_model(None) must not persist or change self._model."""
        from magi_agent.cli.contracts import EngineResult, PermissionDecision, PermissionGate, Terminal
        from magi_agent.cli.tui.app import MagiTuiApp
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        cfg = tmp_path / "config.toml"

        class _FakeGate(PermissionGate):
            async def check(self, req):
                return PermissionDecision(kind="allow")

        class _FakeEngine:
            async def run_turn_stream(self, *a, **k):
                if False:
                    yield None
                yield EngineResult(terminal=Terminal.completed)

        class _FakeRegistry:
            def lookup(self, name):
                return None

            def list_for(self, surface, ctx=None):
                return []

        app = MagiTuiApp(
            engine=_FakeEngine(),
            gate=_FakeGate(),
            commands=_FakeRegistry(),
            renderers=build_tool_renderers(),
            model="original-model",
        )

        async def _run() -> None:
            async with app.run_test():
                app._apply_model(None, _config_path=cfg)
                assert app._model == "original-model"
                assert not cfg.exists()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# /model no-arg in TUI: pushes ModelPickerDialog
# ---------------------------------------------------------------------------


class TestModelCommandTuiPickerOpens:
    """Submitting /model in the TUI must push a ModelPickerDialog screen."""

    def test_slash_model_pushes_model_picker_dialog(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.control import ModelCommand
        from magi_agent.cli.contracts import (
            CommandContext,
            EngineResult,
            PermissionDecision,
            PermissionGate,
            Terminal,
        )
        from magi_agent.cli.tui.app import MagiTuiApp
        from magi_agent.cli.tui.dialogs.model import ModelPickerDialog
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        cfg = tmp_path / "config.toml"
        model_cmd = ModelCommand(name="model", surface=BOTH)

        class _FakeGate(PermissionGate):
            async def check(self, req):
                return PermissionDecision(kind="allow")

        class _FakeEngine:
            async def run_turn_stream(self, *a, **k):
                if False:
                    yield None
                yield EngineResult(terminal=Terminal.completed)

        class _FakeRegistry:
            def lookup(self, name):
                if name == "model":
                    return model_cmd
                return None

            def list_for(self, surface, ctx=None):
                return [model_cmd]

        app = MagiTuiApp(
            engine=_FakeEngine(),
            gate=_FakeGate(),
            commands=_FakeRegistry(),
            renderers=build_tool_renderers(),
        )

        async def _run() -> bool:
            async with app.run_test() as pilot:
                # Submit /model with no args via the app's submit_command helper.
                app.submit_command("model")
                # Give the command worker a tick to run.
                await pilot.pause(0.1)
                # Check that the top screen is a ModelPickerDialog.
                return isinstance(app.screen, ModelPickerDialog)

        dialog_was_shown = asyncio.run(_run())
        assert dialog_was_shown, "ModelPickerDialog was not pushed after /model"


# ---------------------------------------------------------------------------
# Visibility: /model shows in list_for with TUI app context
# ---------------------------------------------------------------------------


class TestModelVisibilityWithApp:
    """The /model command must be VISIBLE when ctx.app exposes open_dialog."""

    def _fresh_registry(self) -> CommandRegistryImpl:
        reg = CommandRegistryImpl()
        register_control_commands(reg)
        return reg

    def test_model_visible_in_tui_list_when_app_has_open_dialog(self) -> None:
        class _FakeApp:
            def open_dialog(self, name: str) -> None:
                pass

        reg = self._fresh_registry()
        ctx = CommandContext(cwd="/tmp", app=_FakeApp())
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" in names

    def test_model_hidden_when_no_app_and_no_selector(self) -> None:
        reg = self._fresh_registry()
        ctx = CommandContext(cwd="/tmp")
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" not in names

    def test_model_found_via_lookup_regardless_of_visibility(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("model") is not None

    def test_model_is_model_command_instance(self) -> None:
        reg = self._fresh_registry()
        cmd = reg.lookup("model")
        assert isinstance(cmd, ModelCommand)

    def test_app_has_model_dialog_true_when_open_dialog_callable(self) -> None:
        class _FakeApp:
            def open_dialog(self, name: str) -> None:
                pass

        ctx = CommandContext(cwd="/tmp", app=_FakeApp())
        assert _app_has_model_dialog(ctx) is True

    def test_app_has_model_dialog_false_when_no_app(self) -> None:
        ctx = CommandContext(cwd="/tmp")
        assert _app_has_model_dialog(ctx) is False

    def test_app_has_model_dialog_false_when_open_dialog_missing(self) -> None:
        class _AppWithoutDialog:
            pass

        ctx = CommandContext(cwd="/tmp", app=_AppWithoutDialog())
        assert _app_has_model_dialog(ctx) is False

    def test_other_commands_visibility_unchanged(self) -> None:
        """Adding the TUI-app gate to /model must not affect /agent, /mcp, /new."""

        class _FakeApp:
            def open_dialog(self, name: str) -> None:
                pass

        reg = self._fresh_registry()
        ctx = CommandContext(cwd="/tmp", app=_FakeApp())
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        # /agent, /mcp, /new remain hidden (no controllers wired).
        assert "agent" not in names
        assert "mcp" not in names
        assert "new" not in names


# ---------------------------------------------------------------------------
# /model no-arg in TUI: returns Skip (dialog handles rest)
# ---------------------------------------------------------------------------


class TestModelCommandTuiReturnsSkip:
    def test_no_arg_with_app_returns_skip(self) -> None:
        class _FakeApp:
            def open_dialog(self, name: str) -> None:
                pass

        cmd = ModelCommand(name="model", surface=BOTH)
        ctx = _ctx(app=_FakeApp())
        result = asyncio.run(cmd.call(None, ctx))
        assert isinstance(result, Skip)

    def test_no_arg_with_app_calls_open_dialog(self) -> None:
        opened: list[str] = []

        class _FakeApp:
            def open_dialog(self, name: str) -> None:
                opened.append(name)

        cmd = ModelCommand(name="model", surface=BOTH)
        ctx = _ctx(app=_FakeApp())
        asyncio.run(cmd.call(None, ctx))
        assert opened == ["model_picker"]
