"""Tests for the PR-E3 per-tool renderers (``cli/tui/tool_render.py``).

Verifies: Protocol conformance (invariant #1), partial-input safety for
``render_call`` (CC ``Tool.ts:605``), search-fidelity ``extract_search_text ==
node.text`` (invariant #3), the diff-bearing Edit renderer, and the registry's
per-name dispatch + unknown fallback.

Plain pytest — no live ``App`` required (the renderers are pure functions over
Rich renderables).
"""

from __future__ import annotations

from magi_agent.cli.contracts import RenderNode, ToolRenderer
from magi_agent.cli.tui import tool_render


# ---------------------------------------------------------------------------
# Protocol conformance (invariant #1)
# ---------------------------------------------------------------------------
def test_renderers_conform_to_protocol() -> None:
    for name in ("Edit", "Bash", "Read"):
        renderer = tool_render.build_tool_renderers().get(name)
        assert isinstance(renderer, ToolRenderer)


# ---------------------------------------------------------------------------
# Partial-input safety
# ---------------------------------------------------------------------------
def test_edit_render_call_accepts_partial_input() -> None:
    renderer = tool_render.EditRenderer()
    # Only file_path present — old_string/new_string still streaming.
    node = renderer.render_call({"file_path": "foo.py"})
    assert isinstance(node, RenderNode)
    assert "foo.py" in node.text
    # Completely empty dict must not KeyError.
    renderer.render_call({})
    # Non-dict partial input must not crash.
    renderer.render_call("foo")


def test_bash_render_call_accepts_partial_input() -> None:
    renderer = tool_render.BashRenderer()
    node = renderer.render_call({})
    assert isinstance(node, RenderNode)
    node2 = renderer.render_call({"command": "ls -la"})
    assert "ls -la" in node2.text


def test_read_render_call_accepts_partial_input() -> None:
    renderer = tool_render.ReadRenderer()
    node = renderer.render_call({})
    assert isinstance(node, RenderNode)
    node2 = renderer.render_call({"file_path": "/tmp/x.txt"})
    assert "/tmp/x.txt" in node2.text


# ---------------------------------------------------------------------------
# Search-fidelity (invariant #3): extract_search_text == node.text
# ---------------------------------------------------------------------------
def test_search_fidelity_edit() -> None:
    renderer = tool_render.EditRenderer()
    node = renderer.render_call(
        {"file_path": "foo.py", "old_string": "a", "new_string": "b"}
    )
    assert renderer.extract_search_text(node) == node.text


def test_search_fidelity_bash() -> None:
    renderer = tool_render.BashRenderer()
    node = renderer.render_call({"command": "echo hi"})
    assert renderer.extract_search_text(node) == node.text
    rnode = renderer.render_result({"stdout": "hi\n"})
    assert renderer.extract_search_text(rnode) == rnode.text


def test_search_fidelity_read() -> None:
    renderer = tool_render.ReadRenderer()
    node = renderer.render_call({"file_path": "f.txt"})
    assert renderer.extract_search_text(node) == node.text
    rnode = renderer.render_result({"content": "line1\nline2\n"})
    assert renderer.extract_search_text(rnode) == rnode.text


# ---------------------------------------------------------------------------
# Edit renders a diff
# ---------------------------------------------------------------------------
def test_edit_render_call_renders_diff() -> None:
    from rich.text import Text

    renderer = tool_render.EditRenderer()
    node = renderer.render_call(
        {"file_path": "x.py", "old_string": "foo bar", "new_string": "foo baz"}
    )
    # A diff has a Rich renderable for the TUI.
    assert node.rich is not None or isinstance(node.rich, Text)
    # The plain projection mentions both sides.
    assert "bar" in node.text
    assert "baz" in node.text


def test_rejected_renders_dim_diff() -> None:
    renderer = tool_render.EditRenderer()
    node = renderer.render_rejected(
        {"file_path": "x.py", "old_string": "foo bar", "new_string": "foo baz"}
    )
    assert isinstance(node, RenderNode)
    assert renderer.extract_search_text(node) == node.text


# ---------------------------------------------------------------------------
# Registry dispatch + fallback
# ---------------------------------------------------------------------------
def test_registry_returns_correct_renderer_per_name() -> None:
    registry = tool_render.build_tool_renderers()
    assert isinstance(registry.get("Edit"), tool_render.EditRenderer)
    assert isinstance(registry.get("Bash"), tool_render.BashRenderer)
    assert isinstance(registry.get("Read"), tool_render.ReadRenderer)


def test_registry_unknown_tool_returns_fallback() -> None:
    registry = tool_render.build_tool_renderers()
    fallback = registry.get("TotallyUnknownTool")
    # Fallback still conforms to the Protocol and stringifies input.
    assert isinstance(fallback, ToolRenderer)
    node = fallback.render_call({"a": 1})
    assert isinstance(node, RenderNode)


def test_register_default_renderers_into_existing_registry() -> None:
    from magi_agent.cli.contracts import ToolRendererRegistry

    registry = ToolRendererRegistry()
    tool_render.register_default_renderers(registry)
    assert isinstance(registry.get("Edit"), tool_render.EditRenderer)
