from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

# cua-driver 0.5.7 get_window_state markdown lines look like:
#   - [0] AXWindow "Safari" [actions=[raise]]
#     - [6] AXMenuItem [actions=[cancel,press,pick]]   (label optional)
# i.e. `[N]` (not `[element_index N]`), role token, optional quoted label.
_ELEMENT_RE = re.compile(r'^\s*-\s*\[(\d+)\]\s+(\S+)(?:\s+"([^"]*)")?', re.MULTILINE)


def _first_json_object(text: str) -> str | None:
    """Return the first brace-balanced ``{...}`` substring, or None.

    A greedy regex would over-capture trailing prose braces; this scans for the
    first balanced object so a JSON action followed by commentary still parses.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


@dataclass(frozen=True)
class UIElement:
    index: int
    role: str
    label: str


def parse_window_state(markdown: str) -> list[UIElement]:
    """Parse cua-driver ``get_window_state`` markdown into UI elements.

    Each actionable element is one (indented) line: ``- [N] AXRole "label"?``
    followed by ``[id=…: actions=[…]]`` metadata. The label is optional.
    """
    out: list[UIElement] = []
    for match in _ELEMENT_RE.finditer(markdown):
        out.append(
            UIElement(
                index=int(match.group(1)),
                role=match.group(2),
                label=match.group(3) or "",
            )
        )
    return out


def parse_action(text: str) -> dict[str, object]:
    """Extract the first brace-balanced JSON object. Must carry ``action``."""
    blob = _first_json_object(text)
    if blob is None:
        raise ValueError("no JSON object found in model reply")
    try:
        loaded = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON action: {exc}") from exc
    if not isinstance(loaded, dict) or "action" not in loaded:
        raise ValueError("action JSON must be an object with an 'action' key")
    return loaded


def action_to_cua_call(
    action: Mapping[str, object], *, pid: int, window_id: int
) -> tuple[str, dict[str, object]]:
    """Map our action enum to a cua-driver MCP (tool_name, args)."""
    kind = str(action.get("action"))
    if kind == "click":
        args: dict[str, object] = {"pid": pid, "window_id": window_id}
        if "element_index" in action:
            args["element_index"] = action["element_index"]
        else:
            args["x"] = action.get("x")
            args["y"] = action.get("y")
        return "click", args
    if kind == "type":
        args = {"pid": pid, "window_id": window_id, "text": str(action.get("text", ""))}
        if "element_index" in action:
            args["element_index"] = action["element_index"]
        return "type_text", args
    if kind == "key":
        raw_keys = action.get("keys", [])
        keys = list(raw_keys) if isinstance(raw_keys, (list, tuple)) else []
        return "hotkey", {
            "pid": pid,
            "keys": keys,
            "window_id": window_id,
        }
    if kind == "scroll":
        amount = action.get("amount", 1)
        return "scroll", {
            "pid": pid,
            "direction": str(action.get("direction", "down")),
            "amount": int(amount) if isinstance(amount, (int, float, str)) else 1,
            "window_id": window_id,
        }
    raise ValueError(f"unknown action: {kind!r}")
