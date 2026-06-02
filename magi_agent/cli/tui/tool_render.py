"""Per-tool renderers conforming to the frozen ``ToolRenderer`` Protocol.

There is no central render switch — each tool ships its own renderer and a small
factory registers them into a ``ToolRendererRegistry`` (CC ``Tool.ts:524-653``;
see ``docs/architecture/claude-code-cli/07-message-diff-display-components.md``
§C). Adding a tool's display means adding a renderer here, never editing a
dispatcher.

Hard rules (from the spec):

* ``render_call`` MUST accept PARTIAL streaming input — the header renders from
  whatever JSON/dict has arrived (CC ``Tool.ts:605``). Never ``KeyError`` on a
  missing key, never assume a dict.
* **Search-fidelity**: ``extract_search_text(node)`` returns exactly the text
  that is displayed (``RenderNode.text``). Phantom indexed-but-unshown text is a
  bug.

``rich`` may be imported here (this is a TUI-surface module). The diff engine in
``cli/render/diff.py`` does the heavy lifting for Edit.
"""

from __future__ import annotations

from magi_agent.cli.contracts import (
    RenderNode,
    ToolRendererRegistry,
)
from magi_agent.cli.render import diff as diffmod

__all__ = [
    "EditRenderer",
    "BashRenderer",
    "ReadRenderer",
    "build_tool_renderers",
    "register_default_renderers",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_dict(partial_input: object) -> dict:
    """Coerce arbitrary partial input into a dict without crashing.

    Streaming input may be an incomplete dict, a JSON-ish string, or ``None``;
    renderers must never ``KeyError`` or ``AttributeError`` on it.
    """

    if isinstance(partial_input, dict):
        return partial_input
    return {}


def _search_text(node: object) -> str:
    """Shared ``extract_search_text``: the displayed ``RenderNode.text``."""

    if isinstance(node, RenderNode):
        return node.text
    return str(node)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
class EditRenderer:
    """Renders an Edit as a header + diff (old_string -> new_string)."""

    def render_call(self, partial_input: object) -> RenderNode:
        data = _as_dict(partial_input)
        file_path = str(data.get("file_path", "") or "")
        old = data.get("old_string")
        new = data.get("new_string")
        header = f"Edit({file_path})" if file_path else "Edit"

        # Partial input: header-only until both strings have streamed in.
        if not isinstance(old, str) or not isinstance(new, str):
            return RenderNode(rich=None, text=header)

        return self._diff_node(header, old, new, file_path, dim=False)

    def render_result(self, result: object) -> RenderNode:
        data = _as_dict(result)
        text = str(data.get("message", "applied")) if data else str(result)
        return RenderNode(rich=None, text=text)

    def render_progress(self, p: object) -> RenderNode:
        return RenderNode(rich=None, text=str(p))

    def render_rejected(self, r: object) -> RenderNode:
        data = _as_dict(r)
        file_path = str(data.get("file_path", "") or "")
        old = data.get("old_string")
        new = data.get("new_string")
        header = f"Edit({file_path}) rejected" if file_path else "Edit rejected"
        if not isinstance(old, str) or not isinstance(new, str):
            return RenderNode(rich=None, text=header)
        return self._diff_node(header, old, new, file_path, dim=True)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)

    @staticmethod
    def _diff_node(
        header: str, old: str, new: str, file_path: str, *, dim: bool
    ) -> RenderNode:
        plain = diffmod.unified_diff_text(old, new, file=file_path or "file")
        text = f"{header}\n{plain}" if plain else header
        try:
            rich_diff = diffmod.render_diff(
                old, new, file=file_path or "file", dim=dim
            )
        except Exception:  # pragma: no cover - never fail a render
            rich_diff = None
        return RenderNode(rich=rich_diff, text=text)


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------
class BashRenderer:
    """Renders a Bash call as the command + (result) output."""

    def render_call(self, partial_input: object) -> RenderNode:
        data = _as_dict(partial_input)
        command = str(data.get("command", "") or "")
        text = f"$ {command}" if command else "Bash"
        return RenderNode(rich=None, text=text)

    def render_result(self, result: object) -> RenderNode:
        data = _as_dict(result)
        if data:
            stdout = str(data.get("stdout", "") or "")
            stderr = str(data.get("stderr", "") or "")
            parts = [p for p in (stdout, stderr) if p]
            text = "".join(parts) if parts else ""
        else:
            text = str(result)
        return RenderNode(rich=None, text=text)

    def render_progress(self, p: object) -> RenderNode:
        return RenderNode(rich=None, text=str(p))

    def render_rejected(self, r: object) -> RenderNode:
        data = _as_dict(r)
        command = str(data.get("command", "") or "")
        text = f"$ {command} (rejected)" if command else "Bash rejected"
        return RenderNode(rich=None, text=text)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
class ReadRenderer:
    """Renders a Read call as the file path + (result) preview."""

    def render_call(self, partial_input: object) -> RenderNode:
        data = _as_dict(partial_input)
        file_path = str(data.get("file_path", "") or "")
        text = f"Read({file_path})" if file_path else "Read"
        return RenderNode(rich=None, text=text)

    def render_result(self, result: object) -> RenderNode:
        data = _as_dict(result)
        content = str(data.get("content", "") or "") if data else str(result)
        return RenderNode(rich=None, text=content)

    def render_progress(self, p: object) -> RenderNode:
        return RenderNode(rich=None, text=str(p))

    def render_rejected(self, r: object) -> RenderNode:
        data = _as_dict(r)
        file_path = str(data.get("file_path", "") or "")
        text = f"Read({file_path}) rejected" if file_path else "Read rejected"
        return RenderNode(rich=None, text=text)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def register_default_renderers(registry: ToolRendererRegistry) -> ToolRendererRegistry:
    """Register Edit/Bash/Read renderers into ``registry`` and return it."""

    registry.register("Edit", EditRenderer())
    registry.register("Bash", BashRenderer())
    registry.register("Read", ReadRenderer())
    return registry


def build_tool_renderers() -> ToolRendererRegistry:
    """Build a fresh registry with the default per-tool renderers registered."""

    return register_default_renderers(ToolRendererRegistry())
