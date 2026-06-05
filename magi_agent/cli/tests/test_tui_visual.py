"""Visual-layer tests: Magi-named tool renderers + the app shell (topbar/echo)."""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    CommandSurface,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
)
from magi_agent.cli.contracts import EngineResult
from magi_agent.cli.tui.app import MagiTuiApp
from magi_agent.cli.tui.tool_render import build_tool_renderers

TUI = CommandSurface(tui=True, headless=False)

_MAGI_TOOL_NAMES = (
    "FileRead",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Glob",
    "Grep",
    "Bash",
    "TodoWrite",
)


# ---------------------------------------------------------------------------
# Tool renderers
# ---------------------------------------------------------------------------
def test_magi_tool_names_have_dedicated_renderers() -> None:
    reg = build_tool_renderers()
    for name in _MAGI_TOOL_NAMES:
        renderer = reg.get(name)
        node = renderer.render_call({"path": "a/b.py", "command": "ls -la", "pattern": "*.py"})
        # A header, never a raw ``repr(dict)`` dump.
        assert "{" not in node.text and "}" not in node.text


def test_fallback_never_dumps_raw_dict() -> None:
    reg = build_tool_renderers()
    renderer = reg.get("SomeUnregisteredTool")
    node = renderer.render_call({"path": "x/y.py", "secret": "z"})
    assert "{" not in node.text
    assert "secret" not in node.text


def test_file_read_result_preview_truncates_large_output() -> None:
    reg = build_tool_renderers()
    renderer = reg.get("FileRead")
    big = "\n".join(f"line {i}" for i in range(50))
    node = renderer.render_result({"output": {"content": big}})
    assert "more lines" in node.text  # truncated with an overflow note


def test_file_read_call_shows_path() -> None:
    reg = build_tool_renderers()
    node = reg.get("FileRead").render_call({"path": "pyproject.toml"})
    assert "pyproject.toml" in node.text


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------
class _FakeRegistry:
    def __init__(self, names):
        self._c = [LocalCommand(name=n, surface=TUI) for n in names]

    def lookup(self, name):
        return next((c for c in self._c if c.name == name), None)

    def list_for(self, surface):
        return list(self._c)


class _AllowGate(PermissionGate):
    async def check(self, req):
        return PermissionDecision(kind="allow")


class _FakeEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        tid = getattr(turn_input, "turn_id", "t1")
        yield RuntimeEvent(type="token", payload={"delta": "ok"}, turn_id=tid)
        yield EngineResult(terminal=Terminal.completed, turn_id=tid)


def _app(**kw) -> MagiTuiApp:
    return MagiTuiApp(
        engine=_FakeEngine(),
        gate=_AllowGate(),
        commands=_FakeRegistry(["compact", "reset"]),
        renderers=ToolRendererRegistry(),
        **kw,
    )


def test_topbar_shows_model_and_cwd() -> None:
    app = _app(model="claude-sonnet-4-6", cwd="/tmp/my-project")
    bar = app._topbar_text()
    assert "Magi" in bar
    assert "claude-sonnet-4-6" in bar
    assert "my-project" in bar


def test_topbar_handles_missing_model() -> None:
    app = _app(cwd="/tmp/x")
    assert "no model" in app._topbar_text()


def test_user_prompt_is_echoed_into_transcript() -> None:
    async def _run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            app.start_turn("summarize the repo please")
            await pilot.pause()
        joined = "\n".join(app.controller.committed_blocks_snapshot())
        assert "summarize the repo please" in joined

    asyncio.run(_run())


def test_welcome_renders_branded_state() -> None:
    async def _run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
        joined = "\n".join(app.controller.committed_blocks_snapshot())
        assert "Welcome to Magi" in joined

    asyncio.run(_run())
