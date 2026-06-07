"""Tests for PR5 — session-history command seams (/fork /undo /redo /share /unshare).

Covers:
- Each command with a FAKE controller wired: action routes through the Protocol
  (spy/fake records the call) and the right ``Text`` is returned.
- Each command with NO controller: returns ``Skip()`` without crashing.
- Gating matrix:
    - All five HIDDEN from ``list_for`` with a bare context (default-off).
    - ``/fork``, ``/share``: visible when their controller is wired.
    - ``/undo``: visible ONLY when revert present AND ``can_undo()`` True;
      hidden when ``can_undo()`` False.
    - ``/redo``: visible ONLY when revert present AND ``can_redo()`` True;
      hidden when ``can_redo()`` False.
    - ``/unshare``: visible ONLY when share present AND ``shared_url()`` not
      None; hidden when ``shared_url()`` returns None.
    - ``lookup`` finds all five regardless of gating.
- ``build_registry`` integration: all five present via lookup, hidden by
  default; control commands + builtins still present.

Plain pytest + asyncio.run — no pytest-asyncio, matching existing convention.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from magi_agent.cli.commands.session_history import (
    ForkCommand,
    RedoCommand,
    SessionForker,
    SessionRevert,
    SessionShareProvider,
    ShareCommand,
    UndoCommand,
    UnshareCommand,
    _session_forker,
    _session_revert,
    _session_share,
    register_session_history_commands,
    session_history_commands,
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
    return CommandContext(cwd="/tmp/test-session-history", runtime=runtime)


# ---------------------------------------------------------------------------
# Fake controllers (spy implementations of the Protocols)
# ---------------------------------------------------------------------------


@dataclass
class FakeSessionForker:
    """Spy SessionForker that records fork_from calls."""

    _counter: int = 0
    forks: list[tuple[str | None, str]] = field(default_factory=list)

    def fork_from(self, message_ref: str | None) -> str:
        self._counter += 1
        new_ref = f"session-fork-{self._counter}"
        self.forks.append((message_ref, new_ref))
        return new_ref


@dataclass
class FakeSessionRevert:
    """Spy SessionRevert with configurable can_undo/can_redo state."""

    _can_undo: bool = True
    _can_redo: bool = False
    undos: int = 0
    redos: int = 0

    def can_undo(self) -> bool:
        return self._can_undo

    def undo(self) -> bool:
        if not self._can_undo:
            return False
        self.undos += 1
        return True

    def can_redo(self) -> bool:
        return self._can_redo

    def redo(self) -> bool:
        if not self._can_redo:
            return False
        self.redos += 1
        return True


@dataclass
class FakeSessionShareProvider:
    """Spy SessionShareProvider with configurable shared state."""

    _url: str | None = None
    shares: int = 0
    unshares: int = 0
    _next_url: str = "https://magi.example.com/share/abc123"

    def shared_url(self) -> str | None:
        return self._url

    def share(self) -> str:
        self.shares += 1
        self._url = self._next_url
        return self._url

    def unshare(self) -> None:
        self.unshares += 1
        self._url = None


class FakeRuntime:
    """Minimal runtime object carrying zero or more session-history controllers."""

    def __init__(
        self,
        *,
        session_forker: object = None,
        session_revert: object = None,
        session_share: object = None,
    ) -> None:
        if session_forker is not None:
            self.session_forker = session_forker
        if session_revert is not None:
            self.session_revert = session_revert
        if session_share is not None:
            self.session_share = session_share


# ---------------------------------------------------------------------------
# Protocol isinstance checks
# ---------------------------------------------------------------------------


class TestProtocols:
    def test_fake_forker_is_session_forker(self) -> None:
        assert isinstance(FakeSessionForker(), SessionForker)

    def test_fake_revert_is_session_revert(self) -> None:
        assert isinstance(FakeSessionRevert(), SessionRevert)

    def test_fake_share_is_session_share_provider(self) -> None:
        assert isinstance(FakeSessionShareProvider(), SessionShareProvider)

    def test_unrelated_object_is_not_session_forker(self) -> None:
        assert not isinstance(object(), SessionForker)

    def test_unrelated_object_is_not_session_revert(self) -> None:
        assert not isinstance(object(), SessionRevert)

    def test_unrelated_object_is_not_session_share_provider(self) -> None:
        assert not isinstance(object(), SessionShareProvider)

    def test_none_is_not_session_forker(self) -> None:
        assert not isinstance(None, SessionForker)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


class TestLookupHelpers:
    def test_forker_none_when_runtime_is_none(self) -> None:
        assert _session_forker(_ctx(runtime=None)) is None

    def test_forker_none_when_attribute_missing(self) -> None:
        assert _session_forker(_ctx(runtime=FakeRuntime())) is None

    def test_forker_returns_instance_when_present(self) -> None:
        fake = FakeSessionForker()
        ctx = _ctx(runtime=FakeRuntime(session_forker=fake))
        assert _session_forker(ctx) is fake

    def test_forker_none_when_wrong_type(self) -> None:
        class NotAForker:
            pass

        ctx = _ctx(runtime=FakeRuntime(session_forker=NotAForker()))
        assert _session_forker(ctx) is None

    def test_revert_none_when_runtime_is_none(self) -> None:
        assert _session_revert(_ctx(runtime=None)) is None

    def test_revert_none_when_attribute_missing(self) -> None:
        assert _session_revert(_ctx(runtime=FakeRuntime())) is None

    def test_revert_returns_instance_when_present(self) -> None:
        fake = FakeSessionRevert()
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        assert _session_revert(ctx) is fake

    def test_share_none_when_runtime_is_none(self) -> None:
        assert _session_share(_ctx(runtime=None)) is None

    def test_share_none_when_attribute_missing(self) -> None:
        assert _session_share(_ctx(runtime=FakeRuntime())) is None

    def test_share_returns_instance_when_present(self) -> None:
        fake = FakeSessionShareProvider()
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        assert _session_share(ctx) is fake

    def test_share_none_when_wrong_type(self) -> None:
        class NotAShare:
            pass

        ctx = _ctx(runtime=FakeRuntime(session_share=NotAShare()))
        assert _session_share(ctx) is None


# ---------------------------------------------------------------------------
# /fork command
# ---------------------------------------------------------------------------


class TestForkCommand:
    def _cmd(self) -> ForkCommand:
        return ForkCommand(name="fork", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_no_controller_no_crash_with_arg(self) -> None:
        result = asyncio.run(self._cmd().call("msg-abc", _ctx()))
        assert isinstance(result, Skip)

    def test_no_arg_forks_from_head(self) -> None:
        fake = FakeSessionForker()
        ctx = _ctx(runtime=FakeRuntime(session_forker=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "forked session:" in result.text
        assert fake.forks[0][0] is None  # message_ref was None

    def test_with_arg_forks_from_message_ref(self) -> None:
        fake = FakeSessionForker()
        ctx = _ctx(runtime=FakeRuntime(session_forker=fake))
        result = asyncio.run(self._cmd().call("msg-abc", ctx))
        assert isinstance(result, Text)
        assert "forked session:" in result.text
        assert fake.forks[0][0] == "msg-abc"

    def test_returns_new_session_ref_in_text(self) -> None:
        fake = FakeSessionForker()
        ctx = _ctx(runtime=FakeRuntime(session_forker=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "session-fork-1" in result.text

    def test_successive_forks_produce_distinct_refs(self) -> None:
        fake = FakeSessionForker()
        ctx = _ctx(runtime=FakeRuntime(session_forker=fake))
        r1 = asyncio.run(self._cmd().call(None, ctx))
        r2 = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(r1, Text)
        assert isinstance(r2, Text)
        assert r1.text != r2.text

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /undo command
# ---------------------------------------------------------------------------


class TestUndoCommand:
    def _cmd(self) -> UndoCommand:
        return UndoCommand(name="undo", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_with_controller_undo_succeeds(self) -> None:
        fake = FakeSessionRevert(_can_undo=True)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "ok" in result.text
        assert fake.undos == 1

    def test_with_controller_undo_nothing_to_undo(self) -> None:
        fake = FakeSessionRevert(_can_undo=False)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "nothing to undo" in result.text
        assert fake.undos == 0

    def test_args_ignored_gracefully(self) -> None:
        fake = FakeSessionRevert(_can_undo=True)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call("extra-arg", ctx))
        assert isinstance(result, Text)

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /redo command
# ---------------------------------------------------------------------------


class TestRedoCommand:
    def _cmd(self) -> RedoCommand:
        return RedoCommand(name="redo", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_with_controller_redo_succeeds(self) -> None:
        fake = FakeSessionRevert(_can_redo=True)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "ok" in result.text
        assert fake.redos == 1

    def test_with_controller_redo_nothing_to_redo(self) -> None:
        fake = FakeSessionRevert(_can_redo=False)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "nothing to redo" in result.text
        assert fake.redos == 0

    def test_args_ignored_gracefully(self) -> None:
        fake = FakeSessionRevert(_can_redo=True)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        result = asyncio.run(self._cmd().call("extra", ctx))
        assert isinstance(result, Text)

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /share command
# ---------------------------------------------------------------------------


class TestShareCommand:
    def _cmd(self) -> ShareCommand:
        return ShareCommand(name="share", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_share_returns_url_in_text(self) -> None:
        fake = FakeSessionShareProvider(_next_url="https://magi.example.com/share/xyz")
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "session shared:" in result.text
        assert "https://magi.example.com/share/xyz" in result.text

    def test_share_calls_provider_share(self) -> None:
        fake = FakeSessionShareProvider()
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        asyncio.run(self._cmd().call(None, ctx))
        assert fake.shares == 1

    def test_args_ignored_gracefully(self) -> None:
        fake = FakeSessionShareProvider()
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        result = asyncio.run(self._cmd().call("extra", ctx))
        assert isinstance(result, Text)

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# /unshare command
# ---------------------------------------------------------------------------


class TestUnshareCommand:
    def _cmd(self) -> UnshareCommand:
        return UnshareCommand(name="unshare", surface=BOTH)

    def test_no_controller_returns_skip(self) -> None:
        result = asyncio.run(self._cmd().call(None, _ctx()))
        assert isinstance(result, Skip)

    def test_unshare_calls_provider_unshare(self) -> None:
        fake = FakeSessionShareProvider(_url="https://magi.example.com/share/abc")
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        result = asyncio.run(self._cmd().call(None, ctx))
        assert isinstance(result, Text)
        assert "session unshared" in result.text
        assert fake.unshares == 1

    def test_args_ignored_gracefully(self) -> None:
        fake = FakeSessionShareProvider(_url="https://magi.example.com/share/abc")
        ctx = _ctx(runtime=FakeRuntime(session_share=fake))
        result = asyncio.run(self._cmd().call("extra", ctx))
        assert isinstance(result, Text)

    def test_is_local_command(self) -> None:
        assert isinstance(self._cmd(), LocalCommand)

    def test_surface_is_both(self) -> None:
        cmd = self._cmd()
        assert cmd.surface.tui and cmd.surface.headless


# ---------------------------------------------------------------------------
# session_history_commands() factory
# ---------------------------------------------------------------------------


class TestSessionHistoryCommandsFactory:
    def test_returns_five_commands(self) -> None:
        cmds = session_history_commands()
        assert len(cmds) == 5

    def test_names_are_correct(self) -> None:
        names = {c.name for c in session_history_commands()}
        assert names == {"fork", "undo", "redo", "share", "unshare"}

    def test_all_local_commands(self) -> None:
        for cmd in session_history_commands():
            assert isinstance(cmd, LocalCommand)

    def test_all_surface_both(self) -> None:
        for cmd in session_history_commands():
            assert cmd.surface.tui and cmd.surface.headless

    def test_returns_fresh_instances(self) -> None:
        a = session_history_commands()
        b = session_history_commands()
        for ca, cb in zip(a, b):
            assert ca is not cb


# ---------------------------------------------------------------------------
# register_session_history_commands — gating matrix
# ---------------------------------------------------------------------------


class TestRegisterSessionHistoryCommands:
    def _fresh_registry(self) -> CommandRegistryImpl:
        reg = CommandRegistryImpl()
        register_session_history_commands(reg)
        return reg

    def _ctx_bare(self) -> CommandContext:
        """No controllers at all."""
        return _ctx(runtime=None)

    # --- default-off: ALL five hidden from list_for with bare context ---

    def test_all_five_hidden_from_tui_list_bare_context(self) -> None:
        reg = self._fresh_registry()
        names = {c.name for c in reg.list_for(TUI_ONLY, self._ctx_bare())}
        for name in ("fork", "undo", "redo", "share", "unshare"):
            assert name not in names, f"{name} should be hidden by default"

    def test_all_five_hidden_from_headless_list_bare_context(self) -> None:
        reg = self._fresh_registry()
        names = {c.name for c in reg.list_for(HEADLESS_ONLY, self._ctx_bare())}
        for name in ("fork", "undo", "redo", "share", "unshare"):
            assert name not in names, f"{name} should be hidden by default"

    # --- lookup always finds all five regardless of predicate ---

    def test_lookup_fork_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("fork") is not None

    def test_lookup_undo_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("undo") is not None

    def test_lookup_redo_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("redo") is not None

    def test_lookup_share_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("share") is not None

    def test_lookup_unshare_when_hidden(self) -> None:
        reg = self._fresh_registry()
        assert reg.lookup("unshare") is not None

    def test_lookup_returns_correct_types(self) -> None:
        reg = self._fresh_registry()
        assert isinstance(reg.lookup("fork"), ForkCommand)
        assert isinstance(reg.lookup("undo"), UndoCommand)
        assert isinstance(reg.lookup("redo"), RedoCommand)
        assert isinstance(reg.lookup("share"), ShareCommand)
        assert isinstance(reg.lookup("unshare"), UnshareCommand)

    # --- /fork visible when forker wired ---

    def test_fork_visible_when_forker_wired(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_forker=FakeSessionForker()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "fork" in names

    def test_fork_visible_in_headless_when_forker_wired(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_forker=FakeSessionForker()))
        names = {c.name for c in reg.list_for(HEADLESS_ONLY, ctx)}
        assert "fork" in names

    # --- /undo visible only when revert present AND can_undo() True ---

    def test_undo_visible_when_revert_present_and_can_undo_true(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_undo=True)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" in names

    def test_undo_hidden_when_revert_present_but_can_undo_false(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_undo=False)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" not in names

    def test_undo_hidden_when_no_revert(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime())
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" not in names

    # --- /redo visible only when revert present AND can_redo() True ---

    def test_redo_visible_when_revert_present_and_can_redo_true(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_redo=True)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "redo" in names

    def test_redo_hidden_when_revert_present_but_can_redo_false(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_redo=False)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "redo" not in names

    def test_redo_hidden_when_no_revert(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime())
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "redo" not in names

    # --- /share visible when share provider wired ---

    def test_share_visible_when_provider_wired(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_share=FakeSessionShareProvider()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "share" in names

    def test_share_visible_in_headless_when_provider_wired(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime(session_share=FakeSessionShareProvider()))
        names = {c.name for c in reg.list_for(HEADLESS_ONLY, ctx)}
        assert "share" in names

    # --- /unshare visible only when share provider wired AND shared_url() not None ---

    def test_unshare_visible_when_provider_wired_and_url_set(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(
            runtime=FakeRuntime(
                session_share=FakeSessionShareProvider(
                    _url="https://magi.example.com/share/abc"
                )
            )
        )
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "unshare" in names

    def test_unshare_hidden_when_provider_wired_but_url_is_none(self) -> None:
        reg = self._fresh_registry()
        # Provider present but shared_url() returns None (not currently shared).
        ctx = _ctx(runtime=FakeRuntime(session_share=FakeSessionShareProvider(_url=None)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "unshare" not in names

    def test_unshare_hidden_when_no_provider(self) -> None:
        reg = self._fresh_registry()
        ctx = _ctx(runtime=FakeRuntime())
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "unshare" not in names

    # --- undo/redo both hidden when revert present but both can_* False ---

    def test_neither_undo_nor_redo_visible_when_both_can_flags_false(self) -> None:
        reg = self._fresh_registry()
        fake = FakeSessionRevert(_can_undo=False, _can_redo=False)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" not in names
        assert "redo" not in names

    def test_both_undo_and_redo_visible_when_both_can_flags_true(self) -> None:
        reg = self._fresh_registry()
        fake = FakeSessionRevert(_can_undo=True, _can_redo=True)
        ctx = _ctx(runtime=FakeRuntime(session_revert=fake))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" in names
        assert "redo" in names

    # --- list_for without ctx does not crash ---

    def test_list_for_without_ctx_does_not_crash(self) -> None:
        reg = self._fresh_registry()
        result = reg.list_for(TUI_ONLY)
        names = {c.name for c in result}
        for name in ("fork", "undo", "redo", "share", "unshare"):
            assert name not in names


# ---------------------------------------------------------------------------
# build_registry integration — session-history seams present + default-off
# ---------------------------------------------------------------------------


class TestBuildRegistryIntegration:
    def _build(self, cwd: str = "/tmp/test-session-history-build") -> object:
        from magi_agent.cli.commands.discovery import build_registry

        return build_registry(cwd)

    def test_fork_found_via_lookup(self) -> None:
        assert self._build().lookup("fork") is not None

    def test_undo_found_via_lookup(self) -> None:
        assert self._build().lookup("undo") is not None

    def test_redo_found_via_lookup(self) -> None:
        assert self._build().lookup("redo") is not None

    def test_share_found_via_lookup(self) -> None:
        assert self._build().lookup("share") is not None

    def test_unshare_found_via_lookup(self) -> None:
        assert self._build().lookup("unshare") is not None

    def test_all_five_hidden_from_list_for_without_controllers(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=None)
        for surface in (TUI_ONLY, HEADLESS_ONLY, BOTH):
            names = {c.name for c in reg.list_for(surface, ctx)}
            for name in ("fork", "undo", "redo", "share", "unshare"):
                assert name not in names, (
                    f"{name} should be hidden for {surface} without controllers"
                )

    def test_fork_visible_when_forker_wired(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(session_forker=FakeSessionForker()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "fork" in names

    def test_share_visible_when_provider_wired(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(session_share=FakeSessionShareProvider()))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "share" in names

    def test_undo_visible_when_can_undo_true(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_undo=True)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" in names

    def test_undo_hidden_when_can_undo_false(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(session_revert=FakeSessionRevert(_can_undo=False)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "undo" not in names

    def test_unshare_visible_when_url_set(self) -> None:
        reg = self._build()
        ctx = _ctx(
            runtime=FakeRuntime(
                session_share=FakeSessionShareProvider(
                    _url="https://magi.example.com/share/xyz"
                )
            )
        )
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "unshare" in names

    def test_unshare_hidden_when_url_none(self) -> None:
        reg = self._build()
        ctx = _ctx(runtime=FakeRuntime(session_share=FakeSessionShareProvider(_url=None)))
        names = {c.name for c in reg.list_for(TUI_ONLY, ctx)}
        assert "unshare" not in names

    # --- control commands still present ---

    def test_control_commands_still_present(self) -> None:
        reg = self._build()
        for name in ("model", "agent", "mcp", "new"):
            assert reg.lookup(name) is not None, (
                f"control command {name} should still be present"
            )

    # --- builtins still present ---

    def test_builtin_commands_still_present(self) -> None:
        reg = self._build()
        for name in ("status", "reset", "compact", "help"):
            assert reg.lookup(name) is not None, (
                f"builtin {name} should still be present"
            )
