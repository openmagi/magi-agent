"""Tests for PR4 — runtime-control command seams (/model /agent /mcp /new).

Covers:
- Each command with a fake controller wired: action routes through the Protocol
  (spy records the call) and the right ``Text`` is returned.
- Each command with NO controller: returns ``Skip()`` without crashing.
- ``register_control_commands``: commands are HIDDEN from ``list_for`` when no
  controller is present (default-off); VISIBLE when the matching controller is
  wired; always findable via ``lookup`` regardless.
- ``build_registry``: the four control commands are present (``lookup``) but
  hidden by default (not in ``list_for`` without controllers).
- Both TUI and headless surfaces see the commands when wired (surface=both).

Plain pytest + asyncio.run — no pytest-asyncio, matching existing convention.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from magi_agent.cli.commands.control import (
    AgentCommand,
    AgentSelector,
    McpCommand,
    McpController,
    ModelCommand,
    ModelSelector,
    NewSessionCommand,
    SessionLifecycle,
    _agent_selector,
    _mcp_controller,
    _model_selector,
    _session_lifecycle,
    control_commands,
    register_control_commands,
)
from magi_agent.cli.commands.registry import CommandRegistryImpl
from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    LocalCommand,
    Skip,
    Text,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOTH = CommandSurface(tui=True, headless=True)
TUI_ONLY = CommandSurface(tui=True, headless=False)
HEADLESS_ONLY = CommandSurface(tui=False, headless=True)


def _ctx(runtime: object = None) -> CommandContext:
    return CommandContext(cwd="/tmp/test-control", runtime=runtime)


# ---------------------------------------------------------------------------
# Fake controllers (spy implementations of the Protocols)
# ---------------------------------------------------------------------------


@dataclass
class FakeModelSelector:
    """Spy ModelSelector that records select_model calls."""

    _models: list[str] = field(default_factory=lambda: ["claude-3-5", "gpt-4o"])
    _current: str | None = "claude-3-5"
    selected: list[str] = field(default_factory=list)

    def list_models(self) -> list[str]:
        return list(self._models)

    def current_model(self) -> str | None:
        return self._current

    def select_model(self, model_id: str) -> None:
        self.selected.append(model_id)
        self._current = model_id


@dataclass
class FakeAgentSelector:
    """Spy AgentSelector that records select_agent calls."""

    _agents: list[str] = field(default_factory=lambda: ["researcher", "coder"])
    _current: str | None = "researcher"
    selected: list[str] = field(default_factory=list)

    def list_agents(self) -> list[str]:
        return list(self._agents)

    def current_agent(self) -> str | None:
        return self._current

    def select_agent(self, agent_id: str) -> None:
        self.selected.append(agent_id)
        self._current = agent_id


@dataclass
class FakeMcpController:
    """Spy McpController that records toggle_server calls."""

    _servers: list[tuple[str, bool]] = field(
        default_factory=lambda: [("notes", True), ("search", False)]
    )
    toggled: list[tuple[str, bool]] = field(default_factory=list)

    def list_servers(self) -> list[tuple[str, bool]]:
        return list(self._servers)

    def toggle_server(self, name: str) -> bool:
        for i, (n, enabled) in enumerate(self._servers):
            if n == name:
                new_state = not enabled
                self._servers[i] = (n, new_state)
                self.toggled.append((name, new_state))
                return new_state
        # Unknown server: treat as now-enabled.
        self._servers.append((name, True))
        self.toggled.append((name, True))
        return True


@dataclass
class FakeSessionLifecycle:
    """Spy SessionLifecycle that records new_session calls."""

    _counter: int = 0
    sessions: list[str] = field(default_factory=list)

    def new_session(self) -> str:
        self._counter += 1
        ref = f"session-{self._counter}"
        self.sessions.append(ref)
        return ref


# A fake runtime object carrying one or all controllers.
class FakeRuntime:
    def __init__(
        self,
        *,
        model_selector: object = None,
        agent_selector: object = None,
        mcp_controller: object = None,
        session_lifecycle: object = None,
    ) -> None:
        if model_selector is not None:
            self.model_selector = model_selector
        if agent_selector is not None:
            self.agent_selector = agent_selector
        if mcp_controller is not None:
            self.mcp_controller = mcp_controller
        if session_lifecycle is not None:
            self.session_lifecycle = session_lifecycle


# ---------------------------------------------------------------------------
# Protocol isinstance checks
# ---------------------------------------------------------------------------


class TestProtocols:
    def test_fake_model_selector_is_model_selector(self) -> None:
        assert isinstance(FakeModelSelector(), ModelSelector)

    def test_fake_agent_selector_is_agent_selector(self) -> None:
        assert isinstance(FakeAgentSelector(), AgentSelector)

    def test_fake_mcp_controller_is_mcp_controller(self) -> None:
        assert isinstance(FakeMcpController(), McpController)

    def test_fake_session_lifecycle_is_session_lifecycle(self) -> None:
        assert isinstance(FakeSessionLifecycle(), SessionLifecycle)

    def test_unrelated_object_is_not_model_selector(self) -> None:
        assert not isinstance(object(), ModelSelector)

    def test_none_is_not_model_selector(self) -> None:
        assert not isinstance(None, ModelSelector)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


class TestLookupHelpers:
    def test_model_selector_none_when_runtime_is_none(self) -> None:
        ctx = _ctx(runtime=None)
        assert _model_selector(ctx) is None

    def test_model_selector_none_when_attribute_missing(self) -> None:
        ctx = _ctx(runtime=FakeRuntime())
        assert _model_selector(ctx) is None

    def test_model_selector_returns_instance_when_present(self) -> None:
        fake = FakeModelSelector()
        ctx = _ctx(runtime=FakeRuntime(model_selector=fake))
        assert _model_selector(ctx) is fake

    def test_agent_selector_none_when_runtime_none(self) -> None:
        assert _agent_selector(_ctx()) is None

    def test_agent_selector_returns_instance_when_present(self) -> None:
        fake = FakeAgentSelector()
        ctx = _ctx(runtime=FakeRuntime(agent_selector=fake))
        assert _agent_selector(ctx) is fake

    def test_mcp_controller_none_when_runtime_none(self) -> None:
        assert _mcp_controller(_ctx()) is None

    def test_mcp_controller_returns_instance_when_present(self) -> None:
        fake = FakeMcpController()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        assert _mcp_controller(ctx) is fake

    def test_session_lifecycle_none_when_runtime_none(self) -> None:
        assert _session_lifecycle(_ctx()) is None

    def test_session_lifecycle_returns_instance_when_present(self) -> None:
        fake = FakeSessionLifecycle()
        ctx = _ctx(runtime=FakeRuntime(session_lifecycle=fake))
        assert _session_lifecycle(ctx) is fake

    def test_wrong_type_on_attribute_returns_none(self) -> None:
        """A non-protocol object on the attribute slot should return None."""

        class NotAModelSelector:
            pass

        ctx = _ctx(runtime=FakeRuntime(model_selector=NotAModelSelector()))
        assert _model_selector(ctx) is None


# ---------------------------------------------------------------------------
# /model command
# ---------------------------------------------------------------------------


class TestModelCommand:
    def _cmd(self) -> ModelCommand:
        return ModelCommand(name="model", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        cmd = self._cmd()
        result = asyncio.run(cmd.call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_no_controller_no_crash(self) -> None:
        cmd = self._cmd()
        result = asyncio.run(cmd.call("gpt-4o", _ctx()))
        assert isinstance(result, Skip)

    def test_no_arg_lists_current_and_available(self) -> None:
        fake = FakeModelSelector()
        ctx = _ctx(runtime=FakeRuntime(model_selector=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "claude-3-5" in result.text
        assert "gpt-4o" in result.text
        assert "current" in result.text

    def test_with_arg_calls_select_model(self) -> None:
        fake = FakeModelSelector()
        ctx = _ctx(runtime=FakeRuntime(model_selector=fake))
        result = asyncio.run(self._cmd().call("gpt-4o", ctx))
        assert isinstance(result, Text)
        assert "selected" in result.text
        assert "gpt-4o" in result.text
        assert fake.selected == ["gpt-4o"]

    def test_with_arg_records_correct_model_id(self) -> None:
        fake = FakeModelSelector()
        ctx = _ctx(runtime=FakeRuntime(model_selector=fake))
        asyncio.run(self._cmd().call("my-custom-model", ctx))
        assert fake.selected[-1] == "my-custom-model"

    def test_no_arg_empty_string_triggers_list(self) -> None:
        fake = FakeModelSelector()
        ctx = _ctx(runtime=FakeRuntime(model_selector=fake))
        result = asyncio.run(self._cmd().call("", ctx))
        assert isinstance(result, Text)
        assert "current" in result.text

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /agent command
# ---------------------------------------------------------------------------


class TestAgentCommand:
    def _cmd(self) -> AgentCommand:
        return AgentCommand(name="agent", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_no_arg_lists_current_and_available(self) -> None:
        fake = FakeAgentSelector()
        ctx = _ctx(runtime=FakeRuntime(agent_selector=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "researcher" in result.text
        assert "current" in result.text

    def test_with_arg_calls_select_agent(self) -> None:
        fake = FakeAgentSelector()
        ctx = _ctx(runtime=FakeRuntime(agent_selector=fake))
        result = asyncio.run(self._cmd().call("coder", ctx))
        assert isinstance(result, Text)
        assert "selected" in result.text
        assert "coder" in result.text
        assert fake.selected == ["coder"]

    def test_with_arg_records_correct_agent_id(self) -> None:
        fake = FakeAgentSelector()
        ctx = _ctx(runtime=FakeRuntime(agent_selector=fake))
        asyncio.run(self._cmd().call("my-agent", ctx))
        assert fake.selected[-1] == "my-agent"

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /mcp command
# ---------------------------------------------------------------------------


class TestMcpCommand:
    def _cmd(self) -> McpCommand:
        return McpCommand(name="mcp", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_no_arg_lists_servers(self) -> None:
        fake = FakeMcpController()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "notes" in result.text
        assert "search" in result.text

    def test_no_arg_shows_enabled_state(self) -> None:
        fake = FakeMcpController()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        # notes=on, search=off by default
        assert "on" in result.text
        assert "off" in result.text

    def test_with_arg_toggles_server(self) -> None:
        fake = FakeMcpController()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        result = asyncio.run(self._cmd().call("notes", ctx))
        assert isinstance(result, Text)
        # notes was on → now off
        assert "notes" in result.text
        assert "disabled" in result.text
        assert fake.toggled == [("notes", False)]

    def test_with_arg_toggle_off_to_on(self) -> None:
        fake = FakeMcpController()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        result = asyncio.run(self._cmd().call("search", ctx))
        assert isinstance(result, Text)
        # search was off → now on
        assert "search" in result.text
        assert "enabled" in result.text
        assert fake.toggled == [("search", True)]

    def test_no_servers_configured(self) -> None:
        fake = FakeMcpController()
        fake._servers = []
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "no servers" in result.text

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /new command
# ---------------------------------------------------------------------------


class TestNewSessionCommand:
    def _cmd(self) -> NewSessionCommand:
        return NewSessionCommand(name="new", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_creates_new_session_and_returns_ref(self) -> None:
        fake = FakeSessionLifecycle()
        ctx = _ctx(runtime=FakeRuntime(session_lifecycle=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "session-1" in result.text
        assert fake.sessions == ["session-1"]

    def test_successive_calls_produce_distinct_refs(self) -> None:
        fake = FakeSessionLifecycle()
        ctx = _ctx(runtime=FakeRuntime(session_lifecycle=fake))
        r1 = asyncio.run(self._cmd().call(None, ctx))
        r2 = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(r1, Text)
        assert isinstance(r2, Text)
        assert r1.text != r2.text
        assert len(fake.sessions) == 2

    def test_args_ignored_gracefully(self) -> None:
        fake = FakeSessionLifecycle()
        ctx = _ctx(runtime=FakeRuntime(session_lifecycle=fake))
        # /new takes no args; extra arg should not crash
        result = asyncio.run(self._cmd().call("extra-arg", ctx))
        assert isinstance(result, Text)

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# control_commands() factory
# ---------------------------------------------------------------------------


class TestControlCommandsFactory:
    def test_returns_four_commands(self) -> None:
        cmds = control_commands()
        assert len(cmds) == 4

    def test_names_are_correct(self) -> None:
        names = {c.name for c in control_commands()}
        assert names == {"model", "agent", "mcp", "new"}

    def test_all_local_commands(self) -> None:
        for cmd in control_commands():
            assert isinstance(cmd, LocalCommand)

    def test_all_surface_both(self) -> None:
        for cmd in control_commands():
            assert cmd.surface.tui and cmd.surface.headless

    def test_returns_fresh_instances(self) -> None:
        a = control_commands()
        b = control_commands()
        for ca, cb in zip(a, b):
            assert ca is not cb


# ---------------------------------------------------------------------------
# register_control_commands — gating / visibility
# ---------------------------------------------------------------------------


class TestRegisterControlCommands:
    def _fresh_registry(self) -> CommandRegistryImpl:
        reg = CommandRegistryImpl()
        register_control_commands(reg)
        return reg

    def _ctx_no_controllers(self) -> CommandContext:
        return _ctx(runtime=None)

    def _ctx_all_controllers(self) -> CommandContext:
        return _ctx(
            runtime=FakeRuntime(
                model_selector=FakeModelSelector(),
                agent_selector=FakeAgentSelector(),
                mcp_controller=FakeMcpController(),
                session_lifecycle=FakeSessionLifecycle(),
            )
        )

    # --- default-off: hidden from list_for without controllers ---

    def test_model_hidden_from_tui_list_when_no_controller(self) -> None:
        reg = self._fresh_registry()
        ctx = self._ctx_no_controllers()
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" not in names

    def test_agent_hidden_from_tui_list_when_no_controller(self) -> None:
        reg = self._fresh_registry()
        names = {c.name for c in reg.list_for(TUI_ONLY, self._ctx_no_controllers())}
        assert "agent" not in names

    def test_mcp_hidden_from_tui_list_when_no_controller(self) -> None:
        reg = self._fresh_registry()
        names = {c.name for c in reg.list_for(TUI_ONLY, self._ctx_no_controllers())}
        assert "mcp" not in names

    def test_new_hidden_from_tui_list_when_no_controller(self) -> None:
        reg = self._fresh_registry()
        names = {c.name for c in reg.list_for(TUI_ONLY, self._ctx_no_controllers())}
        assert "new" not in names

    def test_all_four_hidden_from_headless_list_when_no_controller(self) -> None:
        reg = self._fresh_registry()
        ctx = self._ctx_no_controllers()
        names = {c.name for c in reg.list_for(HEADLESS_ONLY, ctx)}
        for name in ("model", "agent", "mcp", "new"):
            assert name not in names

    # --- visible when controllers are wired ---

    def test_model_visible_in_tui_list_when_controller_present(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(model_selector=FakeModelSelector()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" in names

    def test_agent_visible_in_tui_list_when_controller_present(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(agent_selector=FakeAgentSelector()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "agent" in names

    def test_mcp_visible_in_tui_list_when_controller_present(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(mcp_controller=FakeMcpController()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "mcp" in names

    def test_new_visible_in_tui_list_when_controller_present(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_lifecycle=FakeSessionLifecycle()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "new" in names

    def test_all_four_visible_in_headless_when_all_controllers_present(self) -> None:
        reg = self._fresh_registry()
        ctx = self._ctx_all_controllers()
        names = {c.name for c in reg.list_for(HEADLESS_ONLY, ctx)}
        for name in ("model", "agent", "mcp", "new"):
            assert name in names

    def test_all_four_visible_in_tui_when_all_controllers_present(self) -> None:
        reg = self._fresh_registry()
        ctx = self._ctx_all_controllers()
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        for name in ("model", "agent", "mcp", "new"):
            assert name in names

    # --- lookup always finds the command regardless of predicate ---

    def test_lookup_model_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("model") is not None

    def test_lookup_agent_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("agent") is not None

    def test_lookup_mcp_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("mcp") is not None

    def test_lookup_new_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("new") is not None

    def test_lookup_returns_correct_type(self) -> None:
        reg = self._fresh_registry()
        assert isinstance(reg.lookup("model"), ModelCommand)
        assert isinstance(reg.lookup("agent"), AgentCommand)
        assert isinstance(reg.lookup("mcp"), McpCommand)
        assert isinstance(reg.lookup("new"), NewSessionCommand)

    # --- partial wiring: only matching commands become visible ---

    def test_only_model_visible_when_only_model_controller_wired(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(model_selector=FakeModelSelector()))
        names = {c.name for c in reg.list_for(BOTH, ctx)}
        assert "model" in names
        assert "agent" not in names
        assert "mcp" not in names
        assert "new" not in names

    # --- list_for with ctx=None uses throwaway context (no crash) ---

    def test_list_for_without_ctx_does_not_crash(self) -> None:
        reg = self._fresh_registry()
        result = reg.list_for(TUI_ONLY)
        # No controllers in throwaway ctx, so control commands are hidden.
        names = {c.name for c in result}
        for name in ("model", "agent", "mcp", "new"):
            assert name not in names


# ---------------------------------------------------------------------------
# build_registry integration — control commands present + default-off
# ---------------------------------------------------------------------------


class TestBuildRegistryIntegration:
    def _build(self, cwd: str = "/tmp/test-control-build") -> object:
        from magi_agent.cli.commands.discovery import build_registry

        return build_registry(cwd)

    def test_model_found_via_lookup(self) -> None:
        reg = self._build()
        assert reg.lookup("model") is not None

    def test_agent_found_via_lookup(self) -> None:
        reg = self._build()
        assert reg.lookup("agent") is not None

    def test_mcp_found_via_lookup(self) -> None:
        reg = self._build()
        assert reg.lookup("mcp") is not None

    def test_new_found_via_lookup(self) -> None:
        reg = self._build()
        assert reg.lookup("new") is not None

    def test_model_hidden_from_list_for_without_controllers(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=None)
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" not in names

    def test_all_four_hidden_from_list_for_without_controllers(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=None)
        for surface in (TUI_ONLY, HEADLESS_ONLY, BOTH):
            names = {c.name for c in reg.list_for(surface, ctx)}
            for name in ("model", "agent", "mcp", "new"):
                assert name not in names, f"{name} should be hidden for {surface}"

    def test_model_visible_when_controller_wired(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(model_selector=FakeModelSelector()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "model" in names

    def test_all_four_visible_when_all_controllers_wired(self) -> None:
        reg = self._build()
        ctx = _ctx(
            runtime=FakeRuntime(
                model_selector=FakeModelSelector(),
                agent_selector=FakeAgentSelector(),
                mcp_controller=FakeMcpController(),
                session_lifecycle=FakeSessionLifecycle(),
            )
        )
        for surface in (TUI_ONLY, HEADLESS_ONLY):
            names = {c.name for c in reg.list_for(surface, ctx)}
            for name in ("model", "agent", "mcp", "new"):
                assert name in names, f"{name} should be visible for {surface}"

    def test_builtin_commands_still_present(self) -> None:
        """Ensure build_registry still includes the regular builtins."""
        reg = self._build()
        for name in ("status", "reset", "compact", "help"):
            assert reg.lookup(name) is not None, f"builtin {name} should still be present"
