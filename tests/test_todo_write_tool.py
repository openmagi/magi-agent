from __future__ import annotations

import asyncio

from magi_agent.tools import ToolRegistry, register_core_tool_manifests
from magi_agent.tools.concurrency import ToolCall, partition_tool_calls
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.todo_toolhost import (
    TODO_WRITE_TOOL_NAME,
    TodoWriteHandlerSet,
    bind_todo_write_handler,
)


def _context(session_id: str | None) -> ToolContext:
    return ToolContext(bot_id="bot-test", session_id=session_id, turn_id="turn-1")


def _registry_with_todo() -> tuple[ToolRegistry, TodoWriteHandlerSet]:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    handler_set = bind_todo_write_handler(registry)
    return registry, handler_set


def test_todowrite_manifest_registered_and_active() -> None:
    registry, _ = _registry_with_todo()

    registration = registry.resolve_registration(TODO_WRITE_TOOL_NAME)
    assert registration is not None
    assert registration.handler is not None
    assert registry.is_enabled(TODO_WRITE_TOOL_NAME) is True
    assert registration.manifest.parallel_safety == "unsafe"

    act_names = {manifest.name for manifest in registry.list_available(mode="act")}
    plan_names = {manifest.name for manifest in registry.list_available(mode="plan")}
    assert TODO_WRITE_TOOL_NAME in act_names
    assert TODO_WRITE_TOOL_NAME in plan_names


def test_todowrite_handler_stores_and_returns_list() -> None:
    handler_set = TodoWriteHandlerSet()
    context = _context("session-a")

    first = [
        {"content": "Plan the work", "status": "in_progress"},
        {"content": "Do the work", "status": "pending"},
    ]
    result = handler_set._handle({"todos": first}, context)
    assert result.status == "ok"
    assert result.output == {"todos": first}
    assert result.metadata["todos"] == first
    assert handler_set.todos_for("session-a") == first

    # A second call REPLACES the list rather than appending to it.
    second = [{"content": "Plan the work", "status": "completed"}]
    result2 = handler_set._handle({"todos": second}, context)
    assert result2.status == "ok"
    assert result2.output == {"todos": second}
    assert handler_set.todos_for("session-a") == second


def test_todowrite_per_session_isolation() -> None:
    handler_set = TodoWriteHandlerSet()

    a_todos = [{"content": "Task A", "status": "pending"}]
    b_todos = [{"content": "Task B", "status": "in_progress"}]

    handler_set._handle({"todos": a_todos}, _context("session-a"))
    handler_set._handle({"todos": b_todos}, _context("session-b"))

    assert handler_set.todos_for("session-a") == a_todos
    assert handler_set.todos_for("session-b") == b_todos


def test_todowrite_dispatches_through_registry_as_meta_tool() -> None:
    registry, _ = _registry_with_todo()
    todos = [{"content": "Step 1", "status": "pending"}]

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            TODO_WRITE_TOOL_NAME,
            {"todos": todos},
            _context("session-dispatch"),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert result.output == {"todos": todos}


def test_todowrite_runs_exclusively_and_is_not_readonly_offloaded() -> None:
    registry, _ = _registry_with_todo()

    batches = partition_tool_calls(
        (
            ToolCall(name="ToolSearch", arguments={}, tool_use_id="read-1"),
            ToolCall(name=TODO_WRITE_TOOL_NAME, arguments={"todos": []}, tool_use_id="todo-1"),
            ToolCall(name="ToolSearch", arguments={}, tool_use_id="read-2"),
        ),
        registry,
    )

    assert [(batch.is_concurrent, [call.name for call in batch.calls]) for batch in batches] == [
        (True, ["ToolSearch"]),
        (False, [TODO_WRITE_TOOL_NAME]),
        (True, ["ToolSearch"]),
    ]

    registration = registry.resolve_registration(TODO_WRITE_TOOL_NAME)
    assert registration is not None
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True)
    assert dispatcher._should_offload(registration.manifest, registration.handler) is False


def test_todowrite_normalizes_missing_and_invalid_status() -> None:
    handler_set = TodoWriteHandlerSet()

    result = handler_set._handle(
        {"todos": [{"content": "X"}, {"content": "Y", "status": "bogus"}, "not-a-dict"]},
        _context("session-norm"),
    )

    assert result.status == "ok"
    assert result.output == {
        "todos": [
            {"content": "X", "status": "pending"},
            {"content": "Y", "status": "pending"},
        ]
    }
