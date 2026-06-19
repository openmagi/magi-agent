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
        """Sections outside [model] are not clobbered; provider stays when the
        new model unambiguously belongs to the same provider's family (anthropic
        claude-* matches the existing anthropic provider)."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            "[server]\nport = 8080\n\n[model]\nprovider = \"anthropic\"\n",
            encoding="utf-8",
        )
        persist_model("claude-haiku-4-5", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["server"]["port"] == 8080
        assert data["model"]["provider"] == "anthropic"
        assert data["model"]["model"] == "claude-haiku-4-5"

    def test_provider_realigned_when_new_model_belongs_to_different_provider(
        self, tmp_path: Path
    ) -> None:
        """persist_model("gpt-5.5") on an existing provider=fireworks config
        MUST also update provider→openai. Persisting `model` without realigning
        `provider` produced the impossible `fireworks/gpt-5.5` combo that bricked
        local serve with empty responses."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[model]\nprovider = "fireworks"\nmodel = "kimi-k2p6"\n',
            encoding="utf-8",
        )
        persist_model("gpt-5.5", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["provider"] == "openai"
        assert data["model"]["model"] == "gpt-5.5"

    def test_provider_inferred_when_absent(self, tmp_path: Path) -> None:
        """A bare model id with no [model].provider sets BOTH so the result is
        always a coherent (provider, model) pair, never half-set."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text("", encoding="utf-8")
        persist_model("gemini-3.5-flash", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["provider"] == "gemini"
        assert data["model"]["model"] == "gemini-3.5-flash"

    def test_unknown_model_id_keeps_existing_provider(self, tmp_path: Path) -> None:
        """Custom/unknown model id: provider isn't inferrable, so keep the
        existing one. Only safe when a provider was already set."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[model]\nprovider = "openai"\nmodel = "gpt-5.5"\n',
            encoding="utf-8",
        )
        persist_model("my-custom-fine-tune-v3", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["model"]["provider"] == "openai"
        assert data["model"]["model"] == "my-custom-fine-tune-v3"

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


# ---------------------------------------------------------------------------
# New tests: array preservation, special-char strings, fail-safe on
# unrenderable types, and round-trip self-check in persist_model.
# ---------------------------------------------------------------------------


class TestPersistModelFailSafe:
    """Covers the two data-loss bugs + fail-safe round-trip self-check."""

    # ------------------------------------------------------------------
    # Bug 1: arrays silently dropped → must now be preserved
    # ------------------------------------------------------------------

    def test_array_preserved_after_persist(self, tmp_path: Path) -> None:
        """Config with an array value is intact after persist_model."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[mcp]\nservers = ["alpha", "beta"]\n',
            encoding="utf-8",
        )
        persist_model("new-model", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        # Array must survive unchanged.
        assert data["mcp"]["servers"] == ["alpha", "beta"]
        # Model must be updated.
        assert data["model"]["model"] == "new-model"

    def test_array_of_ints_preserved(self, tmp_path: Path) -> None:
        """Array of integers survives persist_model unchanged."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text('[retries]\nmax_attempts = 3\nports = [8080, 8081, 8082]\n', encoding="utf-8")
        persist_model("some-model", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["retries"]["ports"] == [8080, 8081, 8082]
        assert data["model"]["model"] == "some-model"

    # ------------------------------------------------------------------
    # Bug 2: special-char strings produce invalid TOML → must round-trip
    # ------------------------------------------------------------------

    def test_special_char_string_round_trips(self, tmp_path: Path) -> None:
        """String with quote, backslash, newline and tab round-trips identically."""
        import tomllib

        # The tricky string contains: double quote, backslash, newline, tab.
        tricky = 'say "hello"\npath\\to\tthing'
        cfg = tmp_path / "config.toml"
        # Write initial config with a tricky TOML-escaped string value.
        # In TOML basic strings: \" = quote, \\ = backslash, \n = newline, \t = tab.
        cfg.write_text(
            '[providers.anthropic]\napi_key = "sk-plain"\n'
            '[meta]\nnote = "say \\"hello\\"\\npath\\\\to\\tthing"\n',
            encoding="utf-8",
        )
        # Verify the file is parseable before we touch it.
        with open(cfg, "rb") as fh:
            before = tomllib.load(fh)
        assert before["meta"]["note"] == tricky

        persist_model("target-model", path=cfg)

        with open(cfg, "rb") as fh:
            after = tomllib.load(fh)
        # Special-char string must be byte-faithful.
        assert after["meta"]["note"] == tricky
        # Model updated.
        assert after["model"]["model"] == "target-model"
        # API key preserved.
        assert after["providers"]["anthropic"]["api_key"] == "sk-plain"

    def test_backslash_and_quote_string_round_trips(self, tmp_path: Path) -> None:
        """Backslash and double-quote in a string survive persist_model."""
        import tomllib

        value = 'C:\\Users\\test\\path with "spaces"'
        cfg = tmp_path / "config.toml"
        # In TOML: \\ → backslash, \" → double-quote.
        cfg.write_text(
            '[settings]\nbase_dir = "C:\\\\Users\\\\test\\\\path with \\"spaces\\""\n',
            encoding="utf-8",
        )
        with open(cfg, "rb") as fh:
            before = tomllib.load(fh)
        assert before["settings"]["base_dir"] == value  # sanity-check before touch

        persist_model("m1", path=cfg)
        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)
        assert data["settings"]["base_dir"] == value

    # ------------------------------------------------------------------
    # Fail-safe: datetime values → raises, original file UNCHANGED
    # ------------------------------------------------------------------

    def test_datetime_in_config_raises_and_file_unchanged(self, tmp_path: Path) -> None:
        """persist_model raises ValueError on a config containing a datetime; file untouched."""
        import datetime

        cfg = tmp_path / "config.toml"
        # Write a TOML file that has a datetime field.
        cfg.write_text(
            "[meta]\ncreated = 2026-06-09T10:00:00Z\n[model]\nmodel = \"old-model\"\n",
            encoding="utf-8",
        )
        original_bytes = cfg.read_bytes()

        with pytest.raises((ValueError, TypeError)):
            persist_model("new-model", path=cfg)

        # File must be UNCHANGED.
        assert cfg.read_bytes() == original_bytes

    # ------------------------------------------------------------------
    # Fail-safe: inf/nan float → raises, original file UNCHANGED
    # ------------------------------------------------------------------

    def test_inf_float_raises_and_file_unchanged(self, tmp_path: Path) -> None:
        """persist_model raises when a float inf/nan is in the config."""
        import math
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text('[model]\nmodel = "old"\n', encoding="utf-8")
        original_bytes = cfg.read_bytes()

        # Inject an inf directly into the raw dict to simulate a config that
        # somehow has inf (e.g., from a programmatic update rather than TOML parse).
        # We do this by monkeypatching the read step in the test itself.
        from magi_agent.cli import providers as prov_mod

        _orig_load = prov_mod.tomllib.load  # type: ignore[attr-defined]

        def _fake_load(fh):
            return {"model": {"model": "old", "threshold": math.inf}}

        prov_mod.tomllib.load = _fake_load  # type: ignore[attr-defined]
        try:
            with pytest.raises((ValueError, OverflowError)):
                persist_model("new-model", path=cfg)
        finally:
            prov_mod.tomllib.load = _orig_load  # type: ignore[attr-defined]

        assert cfg.read_bytes() == original_bytes

    def test_nan_float_raises_and_file_unchanged(self, tmp_path: Path) -> None:
        """persist_model raises when a float nan is in the config."""
        import math

        cfg = tmp_path / "config.toml"
        cfg.write_text('[model]\nmodel = "old"\n', encoding="utf-8")
        original_bytes = cfg.read_bytes()

        from magi_agent.cli import providers as prov_mod

        _orig_load = prov_mod.tomllib.load  # type: ignore[attr-defined]

        def _fake_load(fh):
            return {"model": {"model": "old", "score": math.nan}}

        prov_mod.tomllib.load = _fake_load  # type: ignore[attr-defined]
        try:
            with pytest.raises((ValueError, OverflowError)):
                persist_model("new-model", path=cfg)
        finally:
            prov_mod.tomllib.load = _orig_load  # type: ignore[attr-defined]

        assert cfg.read_bytes() == original_bytes

    # ------------------------------------------------------------------
    # Round-trip self-check: realistic nested config
    # ------------------------------------------------------------------

    def test_realistic_nested_config_round_trips(self, tmp_path: Path) -> None:
        """A realistic config (nested tables + keys + array) round-trips faithfully."""
        import tomllib

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            "[providers.anthropic]\n"
            'api_key = "sk-ant-test"\n'
            "\n[providers.openai]\n"
            'api_key = "sk-openai-test"\n'
            "\n[mcp]\n"
            'servers = ["server-a", "server-b"]\n'
            "\n[model]\n"
            'model = "old-model"\n'
            'provider = "anthropic"\n',
            encoding="utf-8",
        )

        persist_model("claude-opus-4", path=cfg)

        with open(cfg, "rb") as fh:
            data = tomllib.load(fh)

        assert data["providers"]["anthropic"]["api_key"] == "sk-ant-test"
        assert data["providers"]["openai"]["api_key"] == "sk-openai-test"
        assert data["mcp"]["servers"] == ["server-a", "server-b"]
        assert data["model"]["model"] == "claude-opus-4"
        assert data["model"]["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# J-1 — local dashboard /v1/chat/stream "model" reaches resolve_provider_config
#
# End-to-end seam test (per the remediation plan): a stream body carrying
# ``model: "test-model"`` must build the local headless runtime with that model,
# which forwards into ``resolve_provider_config(model_override="test-model")``.
# Proves the override reaches the runner (not just the display path).
# ---------------------------------------------------------------------------


class TestStreamModelReachesResolveProviderConfig:
    def _make_app_and_runtime(self):
        from magi_agent.config.models import (
            BuildInfo,
            PythonRuntimeAuthorityConfig,
            RuntimeConfig,
        )
        from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
        from magi_agent.transport.streaming_chat_route import (
            register_streaming_chat_routes,
        )

        try:
            from fastapi import FastAPI
        except ImportError:  # pragma: no cover - fastapi is a runtime dep
            pytest.skip("fastapi not installed")

        runtime = OpenMagiRuntime(
            config=RuntimeConfig(
                bot_id="bot-model-seam",
                user_id="user-model-seam",
                gateway_token="seam-token",
                api_proxy_url="http://api-proxy.local",
                chat_proxy_url="http://chat-proxy.local",
                redis_url="redis://redis.local:6379/0",
                model="config-default-model",
                build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
                authority=PythonRuntimeAuthorityConfig(),
            )
        )
        app = FastAPI(title="model-seam-test")
        register_streaming_chat_routes(app, runtime)
        return app, runtime

    def test_stream_model_threads_to_resolve_provider_config(
        self, monkeypatch
    ) -> None:
        from fastapi.testclient import TestClient

        monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
        monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)

        seen: list[object] = []

        from magi_agent.cli import providers as providers_mod

        def _fake_resolve(model_override=None, **kwargs):  # noqa: ANN001
            seen.append(model_override)
            return None  # None → model-free stub runner (no provider key needed)

        monkeypatch.setattr(providers_mod, "resolve_provider_config", _fake_resolve)

        app, _runtime = self._make_app_and_runtime()
        client = TestClient(app)

        response = client.post(
            "/v1/chat/stream",
            headers={"authorization": "Bearer seam-token"},
            json={
                "sessionId": "s-seam",
                "turnId": "t-seam",
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        # The build may fail downstream (no real provider), but it must occur
        # AFTER resolve_provider_config saw the override.
        assert "test-model" in seen, (
            "selected model never reached resolve_provider_config "
            f"(saw {seen!r})"
        )

    def test_stream_no_model_uses_config_default(self, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
        monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)

        seen: list[object] = []

        from magi_agent.cli import providers as providers_mod

        def _fake_resolve(model_override=None, **kwargs):  # noqa: ANN001
            seen.append(model_override)
            return None

        monkeypatch.setattr(providers_mod, "resolve_provider_config", _fake_resolve)

        app, _runtime = self._make_app_and_runtime()
        client = TestClient(app)

        client.post(
            "/v1/chat/stream",
            headers={"authorization": "Bearer seam-token"},
            json={
                "sessionId": "s-seam",
                "turnId": "t-seam",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        assert "config-default-model" in seen, (
            f"serve-config model fallback not used (saw {seen!r})"
        )
