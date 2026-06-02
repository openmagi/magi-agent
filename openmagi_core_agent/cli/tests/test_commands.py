"""Tests for the Stream D command registry (PR-D1).

Plain pytest + ``asyncio.run`` (no ``pytest_asyncio`` in this repo — Stream A's
tests follow the same convention). Everything is mocked: no model / network is
ever touched.
"""

from __future__ import annotations

import asyncio

import pytest

from openmagi_core_agent.cli.commands import (
    CommandRegistryImpl,
    build_registry,
    builtin_commands,
    discover_commands,
    dispatch,
    get_registry,
    markdown_commands,
)
from openmagi_core_agent.cli.commands import builtins as builtins_mod
from openmagi_core_agent.cli.commands.discovery import (
    DiscoverySources,
    MarkdownPromptCommand,
)
from openmagi_core_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    Compact,
    ContentBlock,
    LocalCommand,
    PromptCommand,
    Skip,
    Text,
    WidgetCommand,
    WidgetDone,
)

# Convenience single-mode surfaces (callers always pass single-mode masks).
TUI = CommandSurface(tui=True, headless=False)
HEADLESS = CommandSurface(tui=False, headless=True)


def _ctx() -> CommandContext:
    return CommandContext(cwd="/tmp/test-cwd")


# ---------------------------------------------------------------------------
# Concrete command subclasses used across tests
# ---------------------------------------------------------------------------
class _PromptCmd(PromptCommand):
    async def build_prompt(self, args, ctx) -> list[ContentBlock]:  # type: ignore[override]
        return [ContentBlock(type="text", text=f"prompt:{args}")]


class _LocalCmd(LocalCommand):
    async def call(self, args, ctx) -> Text:  # type: ignore[override]
        return Text(text=f"local:{args}")


class _WidgetCmd(WidgetCommand):
    """Widget that resolves immediately by calling on_done once."""

    async def call(self, on_done: WidgetDone, ctx, args) -> object:  # type: ignore[override]
        on_done({"picked": args}, display="rendered")
        return "view"


class _DoubleWidgetCmd(WidgetCommand):
    """Widget that (incorrectly) calls on_done twice — guard must hold."""

    async def call(self, on_done: WidgetDone, ctx, args) -> object:  # type: ignore[override]
        on_done({"first": True})
        on_done({"second": True})  # must be ignored by the guard
        return "view"


class _DeferredWidgetCmd(WidgetCommand):
    """Widget that returns without ever calling on_done (deferred/mounted view)."""

    async def call(self, on_done: WidgetDone, ctx, args) -> object:  # type: ignore[override]
        # Intentionally never resolves on_done — Stream E mounts a real view;
        # the D1 lean path must fall back to Skip() rather than block forever.
        return "deferred-view"


# ---------------------------------------------------------------------------
# Surface mask filtering
# ---------------------------------------------------------------------------
def test_surface_mask_tui_only() -> None:
    reg = CommandRegistryImpl()
    cmd = _PromptCmd(name="tuiword", surface=TUI)
    reg.register(cmd)

    assert reg.list_for(TUI) == [cmd]
    assert reg.list_for(HEADLESS) == []


def test_surface_mask_headless_only() -> None:
    reg = CommandRegistryImpl()
    cmd = _LocalCmd(name="hlword", surface=HEADLESS)
    reg.register(cmd)

    assert reg.list_for(HEADLESS) == [cmd]
    assert reg.list_for(TUI) == []


def test_surface_mask_both() -> None:
    reg = CommandRegistryImpl()
    both = CommandSurface(tui=True, headless=True)
    cmd = _LocalCmd(name="anywhere", surface=both)
    reg.register(cmd)

    assert reg.list_for(TUI) == [cmd]
    assert reg.list_for(HEADLESS) == [cmd]


def test_lookup_exact_and_missing() -> None:
    reg = CommandRegistryImpl()
    cmd = _LocalCmd(name="known", surface=TUI)
    reg.register(cmd)

    assert reg.lookup("known") is cmd
    assert reg.lookup("unknown") is None


def test_first_wins_on_duplicate_name() -> None:
    reg = CommandRegistryImpl()
    first = _LocalCmd(name="dup", surface=TUI)
    second = _LocalCmd(name="dup", surface=TUI)
    reg.register(first)
    reg.register(second)
    assert reg.lookup("dup") is first


# ---------------------------------------------------------------------------
# is_enabled re-evaluated each call (no caching)
# ---------------------------------------------------------------------------
def test_is_enabled_refiltered_each_call() -> None:
    reg = CommandRegistryImpl()
    cmd = _LocalCmd(name="gated", surface=TUI)
    flag = {"on": True}
    reg.register(cmd, is_enabled=lambda _ctx: flag["on"])

    assert reg.list_for(TUI) == [cmd]
    flag["on"] = False
    assert reg.list_for(TUI) == []  # disappears live
    flag["on"] = True
    assert reg.list_for(TUI) == [cmd]  # reappears live


def test_default_predicate_always_true() -> None:
    reg = CommandRegistryImpl()
    cmd = _LocalCmd(name="plain", surface=TUI)
    reg.register(cmd)
    assert reg.list_for(TUI) == [cmd]


# ---------------------------------------------------------------------------
# Dispatch behavior
# ---------------------------------------------------------------------------
def test_dispatch_prompt_yields_content_blocks() -> None:
    reg = CommandRegistryImpl()
    reg.register(_PromptCmd(name="p", surface=TUI))
    out = asyncio.run(dispatch(reg, "p", "hi", _ctx(), surface=TUI))
    assert isinstance(out, list)
    assert all(isinstance(b, ContentBlock) for b in out)
    assert out[0].text == "prompt:hi"


def test_dispatch_local_returns_local_result() -> None:
    reg = CommandRegistryImpl()
    reg.register(_LocalCmd(name="l", surface=HEADLESS))
    out = asyncio.run(dispatch(reg, "l", "x", _ctx(), surface=HEADLESS))
    assert isinstance(out, Text)
    assert out.text == "local:x"


def test_dispatch_unknown_returns_skip() -> None:
    reg = CommandRegistryImpl()
    out = asyncio.run(dispatch(reg, "nope", None, _ctx(), surface=TUI))
    assert isinstance(out, Skip)


def test_dispatch_widget_rejected_in_headless() -> None:
    reg = CommandRegistryImpl()
    reg.register(_WidgetCmd(name="w", surface=CommandSurface(tui=True, headless=True)))
    with pytest.raises(PermissionError):
        asyncio.run(dispatch(reg, "w", None, _ctx(), surface=HEADLESS))


def test_dispatch_widget_resolves_in_tui() -> None:
    reg = CommandRegistryImpl()
    reg.register(_WidgetCmd(name="w", surface=TUI))
    result = asyncio.run(dispatch(reg, "w", "arg", _ctx(), surface=TUI))
    assert result == {"picked": "arg"}


def test_dispatch_widget_on_done_guard_single_resolution() -> None:
    reg = CommandRegistryImpl()
    reg.register(_DoubleWidgetCmd(name="w", surface=TUI))
    # The first on_done wins; the second is ignored, no crash / no double-resolve.
    result = asyncio.run(dispatch(reg, "w", None, _ctx(), surface=TUI))
    assert result == {"first": True}


def test_dispatch_widget_without_on_done_returns_skip() -> None:
    reg = CommandRegistryImpl()
    reg.register(_DeferredWidgetCmd(name="w", surface=TUI))
    # Widget returned without resolving on_done -> documented deferred-mount
    # fallback: dispatch returns Skip() instead of blocking on the Future.
    result = asyncio.run(dispatch(reg, "w", "arg", _ctx(), surface=TUI))
    assert isinstance(result, Skip)


# ---------------------------------------------------------------------------
# Per-cwd memoization
# ---------------------------------------------------------------------------
def test_get_registry_memoized_per_cwd() -> None:
    a1 = get_registry("/cwd/a")
    a2 = get_registry("/cwd/a")
    b1 = get_registry("/cwd/b")
    assert a1 is a2  # same cwd -> same instance
    assert a1 is not b1  # different cwd -> different instance


def test_get_registry_returns_registry_protocol() -> None:
    reg = get_registry("/cwd/proto")
    assert isinstance(reg, CommandRegistryImpl)
    # structurally a CommandRegistry
    assert hasattr(reg, "lookup")
    assert hasattr(reg, "list_for")


# ===========================================================================
# PR-D2: discovery (precedence/shadowing + markdown) + builtins
# ===========================================================================
BOTH = CommandSurface(tui=True, headless=True)


# ---------------------------------------------------------------------------
# Precedence / shadowing in discover_commands
# ---------------------------------------------------------------------------
def test_discovery_earlier_source_shadows_later() -> None:
    """A same-named command in a higher-precedence source wins (shadows)."""

    higher = _PromptCmd(name="dup", surface=BOTH)
    lower = _LocalCmd(name="dup", surface=BOTH)
    # bundled (tier 1) shadows workflow (tier 4); also pre-fill builtins/skill
    # so on-disk scan + builtin factory don't add anything.
    sources = DiscoverySources(
        bundled=[higher],
        workflow=[lower],
        skill_dir=[_LocalCmd(name="_skill_placeholder", surface=BOTH)],
        builtins=[_LocalCmd(name="_builtin_placeholder", surface=BOTH)],
    )
    merged = discover_commands("/tmp/no-such-cwd", sources=sources)
    by_name = {c.name: c for c in merged}
    assert by_name["dup"] is higher  # earlier source wins
    # exactly one entry for the shadowed name
    assert [c for c in merged if c.name == "dup"] == [higher]


def test_discovery_dedup_is_explicit_not_just_registry() -> None:
    """The merged list itself is deduped (not relying on registry first-wins)."""

    a = _LocalCmd(name="x", surface=BOTH)
    b = _LocalCmd(name="x", surface=BOTH)
    sources = DiscoverySources(
        bundled=[a],
        plugin=[b],
        skill_dir=[_LocalCmd(name="_s", surface=BOTH)],
        builtins=[_LocalCmd(name="_b", surface=BOTH)],
    )
    merged = discover_commands("/tmp/no-such-cwd", sources=sources)
    assert sum(1 for c in merged if c.name == "x") == 1


def test_discovery_does_not_mutate_reused_sources_across_cwds(tmp_path) -> None:
    """Reusing ONE DiscoverySources across two cwds must re-scan each cwd.

    Regression: ``discover_commands`` used to write the scanned markdown back
    onto ``sources.skill_dir`` (and builtins onto ``sources.builtins``). A
    caller reusing the same ``DiscoverySources`` instance would then see the
    first cwd's populated ``skill_dir`` on the second call, skip re-scanning,
    and silently return the FIRST cwd's commands for the second cwd. Discovery
    must compute into locals and leave the input untouched.
    """

    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    for sub, name in ((cwd_a, "alpha"), (cwd_b, "beta")):
        commands_dir = sub / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / f"{name}.md").write_text(f"{name} body", encoding="utf-8")

    # ONE instance reused across both calls.
    sources = DiscoverySources()

    merged_a = discover_commands(str(cwd_a), sources=sources)
    names_a = {c.name for c in merged_a}
    merged_b = discover_commands(str(cwd_b), sources=sources)
    names_b = {c.name for c in merged_b}

    # Each call returns ITS OWN cwd's markdown command, not the first cwd's.
    assert "alpha" in names_a and "beta" not in names_a
    assert "beta" in names_b and "alpha" not in names_b
    # The shared input was not mutated by either call.
    assert sources.skill_dir == []
    assert sources.builtins == []


# ---------------------------------------------------------------------------
# Markdown command discovery
# ---------------------------------------------------------------------------
def test_markdown_file_loads_as_prompt_command(tmp_path) -> None:
    commands_dir = tmp_path / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    body = "Do the foo thing.\n\nWith detail."
    (commands_dir / "foo.md").write_text(body, encoding="utf-8")

    discovered = discover_commands(str(tmp_path))
    by_name = {c.name: c for c in discovered}
    assert "foo" in by_name
    foo = by_name["foo"]
    assert isinstance(foo, PromptCommand)
    assert isinstance(foo, MarkdownPromptCommand)

    blocks = asyncio.run(foo.build_prompt("ignored-args", _ctx()))
    assert len(blocks) == 1
    assert isinstance(blocks[0], ContentBlock)
    assert blocks[0].text == body


def test_markdown_commands_empty_when_no_dir(tmp_path) -> None:
    assert markdown_commands(str(tmp_path)) == []


def test_markdown_dispatch_yields_content_blocks(tmp_path) -> None:
    commands_dir = tmp_path / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "bar.md").write_text("bar prompt body", encoding="utf-8")

    reg = build_registry(str(tmp_path))
    out = asyncio.run(dispatch(reg, "bar", None, _ctx(), surface=HEADLESS))
    assert isinstance(out, list)
    assert out[0].text == "bar prompt body"


# ---------------------------------------------------------------------------
# Builtins present + runnable headless (no model call)
# ---------------------------------------------------------------------------
def test_builtins_present_in_discovery() -> None:
    discovered = discover_commands("/tmp/no-such-cwd")
    names = {c.name for c in discovered}
    assert {"status", "reset", "compact", "help"} <= names


def test_builtin_commands_factory_returns_four_local() -> None:
    cmds = builtin_commands()
    assert {c.name for c in cmds} == {"status", "reset", "compact", "help"}
    assert all(isinstance(c, LocalCommand) for c in cmds)
    assert all(c.surface == BOTH for c in cmds)


def test_builtins_runnable_headless_result_types() -> None:
    reg = build_registry("/tmp/no-such-cwd")
    ctx = _ctx()

    compact_out = asyncio.run(dispatch(reg, "compact", None, ctx, surface=HEADLESS))
    assert isinstance(compact_out, Compact)

    status_out = asyncio.run(dispatch(reg, "status", None, ctx, surface=HEADLESS))
    assert isinstance(status_out, Text)

    help_out = asyncio.run(dispatch(reg, "help", None, ctx, surface=HEADLESS))
    assert isinstance(help_out, Text)
    # help lists the builtin names without calling the boundary
    assert "status" in help_out.text and "compact" in help_out.text

    reset_out = asyncio.run(dispatch(reg, "reset", None, ctx, surface=HEADLESS))
    assert isinstance(reset_out, Text)


# ---------------------------------------------------------------------------
# status / reset delegate to the boundary (delegation, not reimplementation)
# ---------------------------------------------------------------------------
def test_status_and_reset_delegate_to_boundary(monkeypatch) -> None:
    """Spy on SlashControlBoundary.project to prove status/reset delegate."""

    calls: list[str] = []
    real_project = builtins_mod.SlashControlBoundary.project

    def _spy(self, request):  # type: ignore[no-untyped-def]
        calls.append(request.text)
        return real_project(self, request)

    monkeypatch.setattr(builtins_mod.SlashControlBoundary, "project", _spy)

    ctx = _ctx()
    status_out = asyncio.run(
        dispatch(build_registry("/tmp/d2"), "status", None, ctx, surface=HEADLESS)
    )
    reset_out = asyncio.run(
        dispatch(build_registry("/tmp/d2"), "reset", None, ctx, surface=HEADLESS)
    )

    # Both consulted the boundary with the expected slash text.
    assert any(t.startswith("/status") for t in calls)
    assert any(t.startswith("/reset") for t in calls)
    # status Text reflects the boundary's command_intent projection.
    assert isinstance(status_out, Text)
    assert "command_intent" in status_out.text
    assert isinstance(reset_out, Text)
    assert "command_intent" in reset_out.text


def test_help_does_not_call_boundary(monkeypatch) -> None:
    """help is NOT a boundary command — it must not call project()."""

    calls: list[str] = []

    def _spy(self, request):  # type: ignore[no-untyped-def]
        calls.append(request.text)
        raise AssertionError("help must not call the boundary")

    monkeypatch.setattr(builtins_mod.SlashControlBoundary, "project", _spy)
    out = asyncio.run(
        dispatch(build_registry("/tmp/d2h"), "help", None, _ctx(), surface=HEADLESS)
    )
    assert isinstance(out, Text)
    assert calls == []


# ---------------------------------------------------------------------------
# install_discovery wires build_registry as the default builder
# ---------------------------------------------------------------------------
def test_install_discovery_wires_builder_and_no_import_side_effects(tmp_path) -> None:
    from openmagi_core_agent.cli.commands import (
        install_discovery,
        set_registry_builder,
    )
    from openmagi_core_agent.cli.commands import registry as registry_mod

    commands_dir = tmp_path / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "baz.md").write_text("baz body", encoding="utf-8")

    try:
        install_discovery()
        reg = get_registry(str(tmp_path))
        assert reg.lookup("baz") is not None
        assert reg.lookup("status") is not None
    finally:
        # Restore the default empty builder so other tests are unaffected.
        set_registry_builder(registry_mod._default_builder)
