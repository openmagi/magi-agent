from __future__ import annotations

from .context import ToolContext
from .registry import ToolRegistry
from .result import ToolResult


TODO_WRITE_TOOL_NAME = "TodoWrite"

_VALID_TODO_STATUSES = ("pending", "in_progress", "completed")


def _normalize_todos(raw: object) -> list[dict[str, object]]:
    """Coerce caller-supplied ``todos`` into a list of dicts.

    Validation is intentionally light (YAGNI): the model sends a full task list
    each call. We accept a list of mapping-like items, keep ``content`` and a
    recognized ``status`` (defaulting to ``pending``), and drop anything that is
    not a mapping.
    """

    if not isinstance(raw, list):
        return []
    todos: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        status = item.get("status")
        if status not in _VALID_TODO_STATUSES:
            status = "pending"
        todos.append({"content": content, "status": status})
    return todos


class TodoWriteHandlerSet:
    """Per-session storage for the agent's ``TodoWrite`` task list.

    A single instance is created once per CLI session (the tool registry is
    built once per session in the wiring layer), so the in-memory ``_todos``
    map survives across ``TodoWrite`` calls within that session. Each call
    REPLACES the stored list for its session (Claude Code semantics: the model
    always sends the full list).
    """

    def __init__(self) -> None:
        self._todos: dict[str, list[dict[str, object]]] = {}

    def bind(self, registry: ToolRegistry) -> tuple[str, ...]:
        registration = registry.resolve_registration(TODO_WRITE_TOOL_NAME)
        if registration is None or registration.handler is not None:
            return ()
        registry.bind_handler(
            TODO_WRITE_TOOL_NAME,
            self._handle,
            enabled_by_registry_policy=True,
        )
        return (TODO_WRITE_TOOL_NAME,)

    def todos_for(self, session_id: str | None) -> list[dict[str, object]]:
        return list(self._todos.get(session_id or "local", []))

    def _handle(self, arguments: dict[str, object], context: ToolContext) -> ToolResult:
        todos = _normalize_todos(arguments.get("todos"))
        key = context.session_id or "local"
        self._todos[key] = todos
        return ToolResult(
            status="ok",
            output={"todos": todos},
            metadata={"toolName": TODO_WRITE_TOOL_NAME, "todos": todos},
        )


def bind_todo_write_handler(registry: ToolRegistry) -> TodoWriteHandlerSet:
    """Bind a fresh :class:`TodoWriteHandlerSet` to ``registry`` and return it.

    The returned handler set owns the per-session todo state for callers that
    want to inspect it (e.g. a future TUI/web panel).
    """

    handler_set = TodoWriteHandlerSet()
    handler_set.bind(registry)
    return handler_set


__all__ = [
    "TODO_WRITE_TOOL_NAME",
    "TodoWriteHandlerSet",
    "bind_todo_write_handler",
]
