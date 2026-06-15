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


def test_result_extracts_human_output_not_receipt_scaffold() -> None:
    """A full ToolResult dict shows its human ``output`` content, never the
    receipt scaffolding (artifactRefs / codingMutationReceipt / durationMs)."""

    result = {
        "status": "ok",
        "output": {"content": "real file body"},
        "artifactRefs": [],
        "codingMutationReceipt": None,
        "deliveryReceipts": [],
        "durationMs": 12,
    }
    node = tool_render.ReadRenderer().render_result(result)
    assert "real file body" in node.text
    assert "artifactRefs" not in node.text
    assert "codingMutationReceipt" not in node.text


def test_result_truncated_json_string_renders_done_not_raw_json() -> None:
    """A truncated/invalid ToolResult JSON STRING (the bridge clips large
    previews) must NOT dump verbatim — it collapses to ``(done)``."""

    truncated = '{"artifactRefs": [], "codingMutationReceipt": null, "durat...'
    node = tool_render.ReadRenderer().render_result(truncated)
    assert "artifactRefs" not in node.text
    assert node.text == "(done)"


def test_result_plain_string_passes_through() -> None:
    """A non-JSON string result (e.g. a short message) renders as-is."""

    node = tool_render.ReadRenderer().render_result("all good")
    assert "all good" in node.text


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
# Edit diff split= opt-in via MAGI_TUI_DIFF_SPLIT
# ---------------------------------------------------------------------------
def test_edit_renderer_uses_split_when_env_set(monkeypatch) -> None:
    from rich.table import Table

    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    monkeypatch.setenv("MAGI_TUI_DIFF_SPLIT", "1")
    renderer = tool_render.EditRenderer()
    node = renderer.render_call(
        {"file_path": "x.py", "old_string": "alpha\nbeta", "new_string": "alpha\ngamma"}
    )
    # The diff portion of the grouped renderable is a split Table.
    from rich.console import Group

    assert isinstance(node.rich, Group)
    body = node.rich.renderables[-1]
    assert isinstance(body, Table)
    # Search-fidelity preserved regardless of split.
    assert renderer.extract_search_text(node) == node.text


def test_edit_renderer_unified_by_default(monkeypatch) -> None:
    from rich.text import Text

    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    monkeypatch.delenv("MAGI_TUI_DIFF_SPLIT", raising=False)
    renderer = tool_render.EditRenderer()
    node = renderer.render_call(
        {"file_path": "x.py", "old_string": "a", "new_string": "b"}
    )
    from rich.console import Group

    assert isinstance(node.rich, Group)
    assert isinstance(node.rich.renderables[-1], Text)


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


# ---------------------------------------------------------------------------
# Generic-arg fallback: unknown tools still show a meaningful header
# ---------------------------------------------------------------------------
def test_card_renderer_generic_arg_fallback_for_unknown_keys() -> None:
    node = tool_render.ToolCardRenderer("SpawnAgent").render_call(
        {"prompt": "calc 1+1", "persona": "general"}
    )
    assert "SpawnAgent" in node.text
    assert "calc 1+1" in node.text


def test_card_renderer_string_input_shows_head_not_nothing() -> None:
    # A >400-char input_preview is truncated by the bridge into INVALID JSON,
    # which reaches the renderer as a raw string. Show its head, not "".
    truncated = '{"command": "echo hello world", "description": "long...'
    node = tool_render.ToolCardRenderer("Bash").render_call(truncated)
    assert "Bash" in node.text
    assert "echo hello world" in node.text


def test_registry_is_registered() -> None:
    registry = tool_render.build_tool_renderers()
    assert registry.is_registered("Bash")
    assert not registry.is_registered("SpawnAgent")


# ---------------------------------------------------------------------------
# CJK display-width truncation (cell-accurate, not codepoint-count)
# ---------------------------------------------------------------------------
def test_clip_respects_cell_width() -> None:
    """A long Hangul primary arg clips to ``_ARG_HEAD_MAX`` *cells*, not
    codepoints — a 60-char Korean pattern is 120 cells and would sail past the
    old ``len() <= 80`` gate. Search-fidelity (invariant #3) still holds."""

    from magi_agent.cli.render.width import display_width

    renderer = tool_render.ToolCardRenderer("Grep", ("pattern", "query"))
    node = renderer.render_call({"pattern": "프" * 60})
    # The displayed arg portion is the header minus the "Grep(" + ")" scaffold;
    # bound the whole header's width by the cap + the fixed scaffold width.
    arg = node.text[len("Grep(") : -len(")")]
    assert display_width(arg) <= tool_render._ARG_HEAD_MAX
    # Search-fidelity invariant: indexed text == displayed text.
    assert renderer.extract_search_text(node) == node.text


def test_preview_body_respects_cell_width() -> None:
    """A single long Hangul preview line is capped at ``_PREVIEW_MAX_CHARS`` in
    *cells*, not codepoints, and keeps the existing ``" …"`` (space + ellipsis)
    spelling at that site."""

    from magi_agent.cli.render.width import display_width

    body = tool_render._preview("가" * 2000)
    assert display_width(body) <= tool_render._PREVIEW_MAX_CHARS
    assert body.endswith(" …")
