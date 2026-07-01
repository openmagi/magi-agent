from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .context import ToolContext
from .registry import ToolRegistry
from .result import ToolResult

if TYPE_CHECKING:
    from magi_agent.runtime.plan_ledger import TodoItem


TODO_WRITE_TOOL_NAME = "TodoWrite"

_VALID_TODO_STATUSES = ("pending", "in_progress", "completed")

# Attribute under which ``bind_todo_write_handler`` stashes the bound handler
# set on the registry, so the per-turn wiring can retrieve it WITHOUT changing
# ``_build_core_tool_registry``'s return type (Design: WS3 PR3a, section 5.1).
_REGISTRY_HANDLER_SET_ATTR = "_todo_write_handler_set"


class PlanLedgerSink(Protocol):
    """Durable plan-ledger sink the handler set appends to / restores from.

    Structurally satisfied by ``runtime.plan_ledger.PlanLedgerStore``; typed as
    a Protocol so ``todo_toolhost`` never imports ``plan_ledger`` at module load
    (cold-start safety, Design: WS3 PR3a).
    """

    def append(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        todos: list[dict[str, object]],
    ) -> None: ...

    def restore(self, session_id: str | None) -> "tuple[TodoItem, ...]": ...


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
        if not isinstance(content, str):
            # The live path is schema-gated to a string; coerce on the direct
            # path so a stray non-string never reaches the stored list / UI.
            content = "" if content is None else str(content)
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
        self._ledger_sink: PlanLedgerSink | None = None

    @property
    def ledger_sink(self) -> PlanLedgerSink | None:
        return self._ledger_sink

    def set_ledger_sink(self, sink: PlanLedgerSink | None) -> None:
        """Attach (or detach) the durable plan-ledger sink.

        A post-construction setter (NOT a ctor param) because
        ``bind_todo_write_handler`` runs before the workspace path is known; the
        sink is attached later at the per-turn handler-set build (Design: WS3
        PR3a, section 5.1). Default state (sink ``None``) is today's behavior.
        """
        self._ledger_sink = sink

    def restore_into(self, session_id: str | None) -> None:
        """Re-seed ``_todos`` for ``session_id`` from the durable JSONL last line.

        Seeds the in-memory entry ONLY when it is absent or empty, so a
        same-turn ``TodoWrite`` already executed is never clobbered by an older
        durable line (Design: WS3 PR3a, section 5.2). No-op when no sink is set.
        """
        if self._ledger_sink is None:
            return
        key = session_id or "local"
        if self._todos.get(key):
            return
        restored = self._ledger_sink.restore(session_id)
        if not restored:
            return
        from magi_agent.runtime.plan_ledger import todo_item_to_dict

        self._todos[key] = [todo_item_to_dict(item) for item in restored]

    def snapshot_for(self, session_id: str | None) -> "tuple[TodoItem, ...]":
        """Return the in-memory todo list as a canonical ``TodoItem`` tuple.

        Reads ``_todos``, which is the DURABLE restored snapshot once
        ``restore_into`` has run for this session (Design: WS3 PR3a, section
        5.2). The engine reader (PR3b) consumes this.
        """
        from magi_agent.runtime.plan_ledger import coerce_todo_items

        return coerce_todo_items(self.todos_for(session_id))

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
        if self._ledger_sink is not None:
            # The sink swallows its own I/O failures and never raises, so a
            # ledger problem can never abort the tool call (Design: WS3 PR3a).
            self._ledger_sink.append(
                session_id=context.session_id,
                turn_id=context.turn_id,
                todos=todos,
            )
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
    # Stash the bound handler set on the registry so the per-turn wiring can
    # retrieve it without changing ``_build_core_tool_registry``'s return type
    # (Design: WS3 PR3a, section 5.1). The attribute is additive; callers that
    # do not need it are unaffected.
    setattr(registry, _REGISTRY_HANDLER_SET_ATTR, handler_set)
    return handler_set


def get_todo_write_handler_set(registry: ToolRegistry) -> TodoWriteHandlerSet | None:
    """Return the handler set bound to ``registry``, or ``None`` if unbound.

    The non-signature-breaking accessor for the handler set stashed by
    ``bind_todo_write_handler`` (Design: WS3 PR3a, section 5.1).
    """
    candidate = getattr(registry, _REGISTRY_HANDLER_SET_ATTR, None)
    return candidate if isinstance(candidate, TodoWriteHandlerSet) else None


__all__ = [
    "TODO_WRITE_TOOL_NAME",
    "PlanLedgerSink",
    "TodoWriteHandlerSet",
    "bind_todo_write_handler",
    "get_todo_write_handler_set",
]
