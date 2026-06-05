"""Per-tool renderers conforming to the frozen ``ToolRenderer`` Protocol.

There is no central render switch — each tool ships its own renderer and a small
factory registers them into a ``ToolRendererRegistry`` (CC ``Tool.ts:524-653``;
see ``docs/architecture/claude-code-cli/07-message-diff-display-components.md``
§C). Adding a tool's display means adding a renderer here, never editing a
dispatcher.

Visual model (Claude Code / OpenCode parity): a tool renders as a one-line
**call header** — ``● ToolName(primary-arg)`` with a colored status dot — and an
indented, dimmed **result preview** (``└ …``), truncated so a large file/`bash`
output never floods the transcript. Edits render as a real red/green diff.

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

from rich.text import Text

from magi_agent.cli.contracts import (
    RenderNode,
    ToolRendererRegistry,
)
from magi_agent.cli.render import diff as diffmod

__all__ = [
    "EditRenderer",
    "BashRenderer",
    "ReadRenderer",
    "ToolCardRenderer",
    "build_tool_renderers",
    "register_default_renderers",
]

# Result preview limits — keep the transcript scannable.
_PREVIEW_MAX_LINES = 8
_PREVIEW_MAX_CHARS = 1200

# Status-dot colors.
_DOT_OK = "bold #4ec9b0"      # teal
_DOT_REJECT = "bold #f14c4c"  # red
_NAME_STYLE = "bold #569cd6"  # blue
_ARG_STYLE = "#9cdcfe"        # light blue
_RESULT_STYLE = "dim"
_GUTTER_STYLE = "dim #569cd6"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_dict(partial_input: object) -> dict:
    """Coerce arbitrary partial input into a dict without crashing."""

    if isinstance(partial_input, dict):
        return partial_input
    return {}


def _search_text(node: object) -> str:
    """Shared ``extract_search_text``: the displayed ``RenderNode.text``."""

    if isinstance(node, RenderNode):
        return node.text
    return str(node)


def _first_str(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _result_text(result: object) -> str:
    """Best-effort extraction of human-readable text from a tool result.

    Magi tool results are ``ToolResult.model_dump(by_alias=True)`` →
    ``{"status", "output": {...}, "metadata": {...}}``. Dig the common content
    fields; fall back to a compact key=value rendering, then ``str``.
    """

    data = _as_dict(result)
    if not data:
        return "" if result is None else str(result)
    output = data.get("output")
    if isinstance(output, dict):
        for key in ("content", "stdout", "text", "message", "preview"):
            value = output.get(key)
            if isinstance(value, str) and value:
                return value
        return "\n".join(f"{k}: {v}" for k, v in output.items())
    if isinstance(output, str) and output:
        return output
    for key in ("stdout", "content", "message", "error_message"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _preview(text: str) -> str:
    text = text.strip("\n")
    if not text:
        return ""
    lines = text.split("\n")
    clipped = lines[:_PREVIEW_MAX_LINES]
    body = "\n".join(clipped)
    if len(lines) > _PREVIEW_MAX_LINES:
        body += f"\n… (+{len(lines) - _PREVIEW_MAX_LINES} more lines)"
    if len(body) > _PREVIEW_MAX_CHARS:
        body = body[:_PREVIEW_MAX_CHARS].rstrip() + " …"
    return body


def _call_node(name: str, arg: str, *, rejected: bool = False) -> RenderNode:
    head = f"{name}({arg})" if arg else name
    text = Text()
    text.append("● ", style=_DOT_REJECT if rejected else _DOT_OK)
    text.append(name, style=_NAME_STYLE)
    if arg:
        text.append("(", style="dim")
        text.append(arg, style=_ARG_STYLE)
        text.append(")", style="dim")
    if rejected:
        text.append("  rejected", style=_DOT_REJECT)
    return RenderNode(rich=text, text=(head + (" rejected" if rejected else "")))


def _result_node(result: object) -> RenderNode:
    preview = _preview(_result_text(result))
    if not preview:
        return RenderNode(rich=Text("  └ (done)", style=_RESULT_STYLE), text="(done)")
    text = Text()
    rows = preview.split("\n")
    for index, line in enumerate(rows):
        text.append("  └ " if index == 0 else "    ", style=_GUTTER_STYLE)
        text.append(line, style=_RESULT_STYLE)
        if index < len(rows) - 1:
            text.append("\n")
    return RenderNode(rich=text, text=preview)


# ---------------------------------------------------------------------------
# Generic card renderer (used for most tools)
# ---------------------------------------------------------------------------
class ToolCardRenderer:
    """A generic ``● Name(arg)`` header + dimmed result preview renderer."""

    def __init__(self, name: str, primary_keys: tuple[str, ...] = ()) -> None:
        self._name = name
        self._primary_keys = primary_keys

    def render_call(self, partial_input: object) -> RenderNode:
        return _call_node(self._name, _first_str(_as_dict(partial_input), self._primary_keys))

    def render_result(self, result: object) -> RenderNode:
        return _result_node(result)

    def render_progress(self, p: object) -> RenderNode:
        return _result_node(p)

    def render_rejected(self, r: object) -> RenderNode:
        return _call_node(self._name, _first_str(_as_dict(r), self._primary_keys), rejected=True)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)


# ---------------------------------------------------------------------------
# Bash — show the command on the call line
# ---------------------------------------------------------------------------
class BashRenderer:
    def render_call(self, partial_input: object) -> RenderNode:
        command = str(_as_dict(partial_input).get("command", "") or "")
        text = Text()
        text.append("● ", style=_DOT_OK)
        text.append("Bash ", style=_NAME_STYLE)
        if command:
            text.append("$ ", style="dim")
            text.append(command, style=_ARG_STYLE)
        return RenderNode(rich=text, text=(f"$ {command}" if command else "Bash"))

    def render_result(self, result: object) -> RenderNode:
        data = _as_dict(result)
        if data:
            stdout = str(data.get("stdout", "") or "")
            stderr = str(data.get("stderr", "") or "")
            joined = "".join(p for p in (stdout, stderr) if p) or _result_text(result)
        else:
            joined = str(result)
        return _result_node({"output": {"content": joined}})

    def render_progress(self, p: object) -> RenderNode:
        return _result_node(p)

    def render_rejected(self, r: object) -> RenderNode:
        command = str(_as_dict(r).get("command", "") or "")
        return _call_node("Bash", f"$ {command}" if command else "", rejected=True)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)


# ---------------------------------------------------------------------------
# Read — file path on the call line, content preview below
# ---------------------------------------------------------------------------
class ReadRenderer(ToolCardRenderer):
    def __init__(self) -> None:
        super().__init__("Read", ("path", "file_path"))


# ---------------------------------------------------------------------------
# Edit — header + red/green diff
# ---------------------------------------------------------------------------
class EditRenderer:
    """Renders an Edit as a header + diff (old_string -> new_string)."""

    def __init__(self, name: str = "Edit") -> None:
        self._name = name

    def render_call(self, partial_input: object) -> RenderNode:
        data = _as_dict(partial_input)
        file_path = _first_str(data, ("file_path", "path"))
        old = data.get("old_string")
        new = data.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return _call_node(self._name, file_path)
        return self._diff_node(file_path, old, new, file_path, dim=False)

    def render_result(self, result: object) -> RenderNode:
        return _result_node(result)

    def render_progress(self, p: object) -> RenderNode:
        return _result_node(p)

    def render_rejected(self, r: object) -> RenderNode:
        data = _as_dict(r)
        file_path = _first_str(data, ("file_path", "path"))
        old = data.get("old_string")
        new = data.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return _call_node(self._name, file_path, rejected=True)
        return self._diff_node(file_path, old, new, file_path, dim=True, rejected=True)

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)

    def _diff_node(
        self, file_path: str, old: str, new: str, file: str, *, dim: bool, rejected: bool = False
    ) -> RenderNode:
        call = _call_node(self._name, file_path, rejected=rejected)
        plain = diffmod.unified_diff_text(old, new, file=file or "file")
        text = f"{call.text}\n{plain}" if plain else call.text
        try:
            from rich.console import Group  # noqa: PLC0415

            rich_diff = diffmod.render_diff(old, new, file=file or "file", dim=dim)
            rich = Group(call.rich, rich_diff) if rich_diff is not None else call.rich
        except Exception:  # pragma: no cover - never fail a render
            rich = call.rich
        return RenderNode(rich=rich, text=text)


# ---------------------------------------------------------------------------
# Fallback — a nicely formatted card for any unregistered tool
# ---------------------------------------------------------------------------
class _NiceFallbackRenderer:
    """Default renderer: a generic card that never dumps a raw ``repr(dict)``."""

    def render_call(self, partial_input: object) -> RenderNode:
        data = _as_dict(partial_input)
        arg = _first_str(data, ("path", "file_path", "command", "pattern", "query", "name"))
        return _call_node("", arg) if arg else RenderNode(rich=Text("● ", style=_DOT_OK), text="")

    def render_result(self, result: object) -> RenderNode:
        return _result_node(result)

    def render_progress(self, p: object) -> RenderNode:
        return _result_node(p)

    def render_rejected(self, r: object) -> RenderNode:
        return RenderNode(rich=Text("  └ rejected", style=_DOT_REJECT), text="rejected")

    def extract_search_text(self, node: object) -> str:
        return _search_text(node)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def register_default_renderers(registry: ToolRendererRegistry) -> ToolRendererRegistry:
    """Register renderers for Magi's first-party tool names."""

    registry.register("FileRead", ReadRenderer())
    registry.register("Read", ReadRenderer())
    registry.register("FileWrite", ToolCardRenderer("FileWrite", ("path", "file_path")))
    registry.register("FileEdit", EditRenderer("FileEdit"))
    registry.register("Edit", EditRenderer("Edit"))
    registry.register("PatchApply", ToolCardRenderer("PatchApply", ("path", "file_path")))
    registry.register("Glob", ToolCardRenderer("Glob", ("pattern", "glob")))
    registry.register("Grep", ToolCardRenderer("Grep", ("pattern", "query")))
    registry.register("Bash", BashRenderer())
    registry.register("TodoWrite", ToolCardRenderer("TodoWrite", ()))
    return registry


def build_tool_renderers() -> ToolRendererRegistry:
    """Build a fresh registry with the default per-tool renderers registered."""

    return register_default_renderers(ToolRendererRegistry(fallback=_NiceFallbackRenderer()))
