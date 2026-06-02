from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from magi_agent.runtime.turn_maintenance import (
    HEARTBEAT_INTERVAL_MS,
    HEARTBEAT_SILENCE_MS,
    HeartbeatMonitor,
    compact_messages_inline,
    estimate_message_tokens,
    micro_compact,
    snip_compact,
    wrap_event_sink_with_monitor,
)


class FakeClock:
    def __init__(self) -> None:
        self._now = 0
        self._queue: list[dict[str, Any]] = []

    def now(self) -> int:
        return self._now

    def schedule(self, callback: Callable[[], None], delay_ms: int) -> Callable[[], None]:
        task = {
            "callback": callback,
            "fire_at": self._now + delay_ms,
            "cancelled": False,
        }
        self._queue.append(task)

        def cancel() -> None:
            task["cancelled"] = True

        return cancel

    def advance(self, ms: int) -> None:
        target = self._now + ms
        while True:
            due = [
                task
                for task in self._queue
                if not task["cancelled"] and task["fire_at"] <= target
            ]
            if not due:
                break
            next_task = min(due, key=lambda task: task["fire_at"])
            self._now = next_task["fire_at"]
            next_task["cancelled"] = True
            next_task["callback"]()
        self._now = target

    def pending(self) -> int:
        return len([task for task in self._queue if not task["cancelled"]])


def _tool_pair(tool_id: str, result_size: int) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Read",
                    "input": {"path": f"/tmp/{tool_id}.txt"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": "x" * result_size,
                }
            ],
        },
    ]


def _messages(pair_count: int, result_size: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "Hello"}]
    for index in range(pair_count):
        messages.extend(_tool_pair(f"tool_{index}", result_size))
    return messages


def _tool_ids(messages: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                ids.append(str(block["id"]))
    return ids


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_heartbeat_does_not_emit_for_quick_completion_and_stop_is_idempotent() -> None:
    clock = FakeClock()
    events: list[dict[str, Any]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)

    monitor.start(0)
    clock.advance(HEARTBEAT_SILENCE_MS // 2)
    monitor.ping({"type": "text_delta", "delta": "hi"})
    monitor.stop()
    monitor.stop()
    clock.advance(HEARTBEAT_SILENCE_MS * 5)

    assert events == []
    assert monitor.get_heartbeats_emitted() == 0
    assert clock.pending() == 0


def test_heartbeat_emits_after_silence_threshold_and_then_interval() -> None:
    clock = FakeClock()
    events: list[dict[str, Any]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)

    monitor.start(3)
    clock.advance(HEARTBEAT_SILENCE_MS)
    clock.advance(HEARTBEAT_INTERVAL_MS)

    assert events == [
        {
            "type": "heartbeat",
            "turnId": "turn_1",
            "iter": 3,
            "elapsedMs": HEARTBEAT_SILENCE_MS,
            "lastEventAt": 0,
        },
        {
            "type": "heartbeat",
            "turnId": "turn_1",
            "iter": 3,
            "elapsedMs": HEARTBEAT_SILENCE_MS + HEARTBEAT_INTERVAL_MS,
            "lastEventAt": 0,
        },
    ]
    assert monitor.get_heartbeats_emitted() == 2


def test_heartbeat_ping_resets_silence_but_heartbeat_events_do_not() -> None:
    clock = FakeClock()
    events: list[dict[str, Any]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)

    monitor.start(0)
    clock.advance(HEARTBEAT_SILENCE_MS // 2)
    monitor.ping({"type": "tool_result", "content": "progress"})
    clock.advance(HEARTBEAT_SILENCE_MS - 1)

    assert events == []

    clock.advance(1)
    assert len(events) == 1
    assert events[0]["lastEventAt"] == HEARTBEAT_SILENCE_MS // 2

    monitor.ping(
        {
            "type": "heartbeat",
            "turnId": "turn_1",
            "iter": 0,
            "elapsedMs": HEARTBEAT_SILENCE_MS,
            "lastEventAt": HEARTBEAT_SILENCE_MS // 2,
        }
    )
    clock.advance(HEARTBEAT_INTERVAL_MS)

    assert len(events) == 2
    assert events[1]["lastEventAt"] == HEARTBEAT_SILENCE_MS // 2


def test_heartbeat_start_restarts_timer_and_stop_cancels_pending_timer() -> None:
    clock = FakeClock()
    events: list[dict[str, Any]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)

    monitor.start(0)
    clock.advance(HEARTBEAT_SILENCE_MS // 2)
    monitor.start(1)
    clock.advance(HEARTBEAT_SILENCE_MS - 1)

    assert events == []

    clock.advance(1)
    assert events[0]["iter"] == 1
    assert events[0]["elapsedMs"] == HEARTBEAT_SILENCE_MS

    monitor.stop()
    clock.advance(HEARTBEAT_INTERVAL_MS * 3)
    assert len(events) == 1
    assert clock.pending() == 0


def test_heartbeat_event_sink_failures_are_fail_open() -> None:
    clock = FakeClock()

    def raising_sink(_event: dict[str, Any]) -> None:
        raise RuntimeError("sink failed")

    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=raising_sink, clock=clock)
    monitor.start(0)

    clock.advance(HEARTBEAT_SILENCE_MS)
    clock.advance(HEARTBEAT_INTERVAL_MS)

    assert monitor.get_heartbeats_emitted() == 2


def test_event_sink_wrapper_forwards_events_and_pings_without_sse_dependency() -> None:
    clock = FakeClock()
    events: list[dict[str, Any]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)
    wrapped_sink = wrap_event_sink_with_monitor(events.append, monitor)

    monitor.start(0)
    clock.advance(HEARTBEAT_SILENCE_MS // 2)
    wrapped_sink({"type": "text_delta", "delta": "hello"})
    clock.advance(HEARTBEAT_SILENCE_MS - 1)

    assert events == [{"type": "text_delta", "delta": "hello"}]

    clock.advance(1)
    assert [event["type"] for event in events] == ["text_delta", "heartbeat"]


def test_estimate_message_tokens_matches_char_over_four_approximation() -> None:
    messages = [
        {"role": "user", "content": "abcd"},
        {
            "role": "assistant",
            "content": [
                "efgh",
                {"type": "text", "text": "ijkl"},
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "Write",
                    "input": {"path": "/tmp/file"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "mnop"},
                {"type": "unknown", "value": "qrst"},
            ],
        },
    ]

    expected_chars = 4 + 4 + 4 + len('{"path":"/tmp/file"}') + 4
    expected_chars += len('{"type":"unknown","value":"qrst"}')
    assert estimate_message_tokens(messages) == pytest.approx(expected_chars / 4, abs=1)
    assert estimate_message_tokens(messages) == (expected_chars + 3) // 4


def test_estimate_message_tokens_counts_structured_tool_result_as_whole_block() -> None:
    structured_content = [
        {"type": "text", "text": "alpha"},
        {"json": {"ok": True, "count": 2}},
    ]
    tool_result_block = {
        "type": "tool_result",
        "tool_use_id": "tool_1",
        "content": structured_content,
        "is_error": True,
    }
    messages = [{"role": "user", "content": [tool_result_block]}]

    expected_chars = len(
        json.dumps(tool_result_block, ensure_ascii=False, separators=(",", ":"))
    )
    content_only_chars = len(
        json.dumps(structured_content, ensure_ascii=False, separators=(",", ":"))
    )

    assert expected_chars > content_only_chars
    assert estimate_message_tokens(messages) == (expected_chars + 3) // 4


def test_snip_compact_drops_oldest_matched_pairs_keeps_last_n_and_first_user() -> None:
    messages = _messages(pair_count=5, result_size=16)
    before = deepcopy(messages)

    result = snip_compact(messages, keep_last=2)

    assert messages == before
    assert result is not messages
    assert result[0] == {"role": "user", "content": "Hello"}
    assert _tool_ids(result) == ["tool_3", "tool_4"]
    assert result != messages
    remaining_result_ids = [
        block["tool_use_id"]
        for message in result
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert remaining_result_ids == ["tool_3", "tool_4"]


def test_snip_compact_drops_first_user_tool_result_when_pair_is_snipped() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "old_tool",
                    "name": "Read",
                    "input": {"path": "/tmp/old.txt"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "old_tool",
                    "content": "old result",
                }
            ],
        },
        *_tool_pair("new_tool", 16),
    ]

    result = snip_compact(messages, keep_last=1)

    assert _tool_ids(result) == ["new_tool"]
    remaining_result_ids = [
        block["tool_use_id"]
        for message in result
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert remaining_result_ids == ["new_tool"]


def test_snip_compact_returns_deep_copy_when_no_compaction_needed() -> None:
    messages = _messages(pair_count=2, result_size=16)

    result = snip_compact(messages, keep_last=5)

    assert result == messages
    assert result is not messages
    assert result[1] is not messages[1]
    assert result[1]["content"] is not messages[1]["content"]


def test_micro_compact_truncates_large_tool_results_and_preserves_is_error() -> None:
    long_content = "A" * 60 + "B" * 60 + "C" * 60
    messages = [
        {"role": "user", "content": "Hello"},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool_1",
                    "content": long_content,
                    "is_error": True,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool_2",
                    "content": "small",
                },
            ],
        },
    ]
    before = deepcopy(messages)

    result = micro_compact(messages, max_result_chars=80)
    compacted = result[1]["content"][0]
    untouched = result[1]["content"][1]

    assert messages == before
    assert compacted["is_error"] is True
    assert "chars omitted" in compacted["content"]
    assert compacted["content"].startswith("A" * 48)
    assert compacted["content"].endswith("C" * 24)
    assert untouched == before[1]["content"][1]
    assert result[1]["content"] is not messages[1]["content"]


def test_compact_messages_inline_orchestrates_snip_and_micro_without_mutating() -> None:
    messages = _messages(pair_count=12, result_size=12_000)
    before = deepcopy(messages)
    before_tokens = estimate_message_tokens(messages)

    result = compact_messages_inline(messages, target_token_budget=10_000)

    assert messages == before
    assert result is not messages
    assert estimate_message_tokens(result) < before_tokens
    assert estimate_message_tokens(result) <= 10_000
    assert result[0] == {"role": "user", "content": "Hello"}


def test_compact_messages_inline_returns_copy_when_under_budget() -> None:
    messages = _messages(pair_count=1, result_size=16)

    result = compact_messages_inline(messages, target_token_budget=200_000)

    assert result == messages
    assert result is not messages
    assert result[1]["content"] is not messages[1]["content"]


def test_turn_maintenance_import_is_pure_local_only() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.runtime.turn_maintenance")
assert hasattr(module, "HeartbeatMonitor")
assert hasattr(module, "compact_messages_inline")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "subprocess",
    "fastapi",
    "starlette.routing",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.sse",
)
forbidden_prefixes = (
    "google.adk",
    "magi_agent.tools",
    "magi_agent.memory",
    "magi_agent.workspace",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.children",
    "magi_agent.missions",
    "kubernetes",
    "supabase",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"turn_maintenance import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_turn_maintenance_source_forbids_runtime_side_effect_imports() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "magi_agent" / "runtime" / "turn_maintenance.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "asyncio",
        "fastapi",
        "starlette",
        "kubernetes",
        "supabase",
        "magi_agent.adk_bridge",
        "magi_agent.tools",
        "magi_agent.memory",
        "magi_agent.workspace",
        "magi_agent.transport",
        "magi_agent.channels",
        "magi_agent.children",
        "magi_agent.missions",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "SseWriter" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "kubectl" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
