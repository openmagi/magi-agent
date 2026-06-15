"""Render-layer tests for the ``full_output`` cap-override chokepoint.

The TUI flat path truncates a tool result to 8 lines / 1200 chars via
``_preview`` (the SOLE funnel reached by every renderer's ``render_result`` ->
``_result_node`` -> ``_preview``). The expand affordance lifts that clamp by
entering ``full_output()`` around the synchronous render call, so the card body
can carry the whole ~8 KB payload while the flat path stays byte-identical.

These tests are pure render-layer (no Textual/app harness needed).
"""

from __future__ import annotations


def _big_text(n: int = 12) -> str:
    return "\n".join(f"L{i}" for i in range(1, n + 1))


def test_preview_truncates_by_default() -> None:
    from magi_agent.cli.tui.tool_render import _preview

    out = _preview(_big_text())
    assert "… (+" in out
    # 8 kept lines + the "… (+N more lines)" marker line == 9 lines.
    assert len(out.split("\n")) <= 9
    assert "L12" not in out


def test_full_output_context_lifts_cap() -> None:
    from magi_agent.cli.tui.tool_render import _preview, full_output

    big = _big_text()
    with full_output():
        out = _preview(big)
        assert "L12" in out
        assert "… (+" not in out
    # Flag restored on exit: truncation resumes.
    after = _preview(big)
    assert "L12" not in after
    assert "… (+" in after


def test_result_node_full_output_carries_all_lines() -> None:
    from magi_agent.cli.tui.tool_render import ToolCardRenderer, full_output

    renderer = ToolCardRenderer("X")
    with full_output():
        node = renderer.render_result({"output": {"stdout": _big_text()}})
    assert "L12" in node.text
