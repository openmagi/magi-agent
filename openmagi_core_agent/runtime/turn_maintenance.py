from __future__ import annotations

from copy import deepcopy
import json
import threading
import time
from typing import Any, Callable, Mapping, Protocol


HEARTBEAT_SILENCE_MS = 10_000
HEARTBEAT_INTERVAL_MS = 10_000

Message = dict[str, Any]
Event = dict[str, Any]
EventSink = Callable[[Event], None]


class Clock(Protocol):
    def now(self) -> int:
        ...

    def schedule(self, callback: Callable[[], None], delay_ms: int) -> Callable[[], None]:
        ...


class RealClock:
    def now(self) -> int:
        return int(time.time() * 1000)

    def schedule(self, callback: Callable[[], None], delay_ms: int) -> Callable[[], None]:
        timer = threading.Timer(delay_ms / 1000, callback)
        timer.daemon = True
        timer.start()
        return timer.cancel


REAL_CLOCK = RealClock()


class HeartbeatMonitor:
    def __init__(
        self,
        *,
        turn_id: str,
        event_sink: EventSink,
        clock: Clock | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._event_sink = event_sink
        self._clock = clock or REAL_CLOCK
        self._iter = -1
        self._iter_started_at = 0
        self._last_event_at = 0
        self._running = False
        self._heartbeats_emitted = 0
        self._cancel: Callable[[], None] | None = None

    def start(self, iter: int) -> None:
        self.stop()
        now = self._clock.now()
        self._iter = iter
        self._iter_started_at = now
        self._last_event_at = now
        self._heartbeats_emitted = 0
        self._running = True
        self._arm(HEARTBEAT_SILENCE_MS)

    def stop(self) -> None:
        self._running = False
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    def ping(self, event: Mapping[str, Any]) -> None:
        if not self._running:
            return
        if event.get("type") == "heartbeat":
            return
        self._last_event_at = self._clock.now()
        self._heartbeats_emitted = 0
        self._arm(HEARTBEAT_SILENCE_MS)

    def get_heartbeats_emitted(self) -> int:
        return self._heartbeats_emitted

    def _arm(self, delay_ms: int) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        if not self._running:
            return
        self._cancel = self._clock.schedule(self._on_tick, delay_ms)

    def _on_tick(self) -> None:
        self._cancel = None
        if not self._running:
            return

        now = self._clock.now()
        elapsed_since_last = now - self._last_event_at
        needed = (
            HEARTBEAT_SILENCE_MS
            if self._heartbeats_emitted == 0
            else HEARTBEAT_INTERVAL_MS
        )
        if elapsed_since_last < needed:
            self._arm(needed - elapsed_since_last)
            return

        event: Event = {
            "type": "heartbeat",
            "turnId": self._turn_id,
            "iter": self._iter,
            "elapsedMs": now - self._iter_started_at,
            "lastEventAt": self._last_event_at,
        }
        self._heartbeats_emitted += 1
        try:
            self._event_sink(event)
        except Exception:
            pass
        self._arm(HEARTBEAT_INTERVAL_MS)


def wrap_event_sink_with_monitor(
    event_sink: EventSink,
    monitor: HeartbeatMonitor,
) -> EventSink:
    def wrapped(event: Event) -> None:
        try:
            event_sink(event)
        except Exception:
            pass
        try:
            monitor.ping(event)
        except Exception:
            pass

    return wrapped


def wrap_event_sink_with_runtime_heartbeat_boundary(
    event_sink: EventSink,
    boundary: Any | None = None,
) -> EventSink:
    if boundary is None or getattr(boundary, "enabled", False) is not True:
        return event_sink

    def wrapped(event: Event) -> None:
        try:
            event_sink(event)
        except Exception:
            pass
        try:
            boundary.consume_event(event)
        except Exception:
            pass

    return wrapped


def estimate_message_tokens(messages: list[Message] | tuple[Message, ...]) -> int:
    chars = 0
    for message in messages:
        chars += _estimate_content_chars(message.get("content"))
    return (chars + 3) // 4


def snip_compact(messages: list[Message] | tuple[Message, ...], keep_last: int) -> list[Message]:
    pairs = _find_tool_pairs(messages)
    if len(pairs) <= keep_last:
        return deepcopy(list(messages))

    drop_count = len(pairs) - keep_last
    drop_tool_use_ids = {pair["tool_use_id"] for pair in pairs[:drop_count]}

    result: list[Message] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            result.append(deepcopy(message))
            continue

        filtered = [
            deepcopy(block)
            for block in content
            if not _is_dropped_tool_block(block, drop_tool_use_ids)
        ]
        if filtered:
            copied = deepcopy(message)
            copied["content"] = filtered
            result.append(copied)

    return result


def micro_compact(
    messages: list[Message] | tuple[Message, ...],
    max_result_chars: int,
) -> list[Message]:
    result: list[Message] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            result.append(deepcopy(message))
            continue

        copied = deepcopy(message)
        copied["content"] = [
            _compact_tool_result_block(block, max_result_chars) for block in content
        ]
        result.append(copied)
    return result


def compact_messages_inline(
    messages: list[Message] | tuple[Message, ...],
    target_token_budget: int,
) -> list[Message]:
    if estimate_message_tokens(messages) <= target_token_budget:
        return deepcopy(list(messages))

    result = snip_compact(messages, max(3, len(messages) // 4))
    if estimate_message_tokens(result) <= target_token_budget:
        return result

    result = snip_compact(result, 2)
    if estimate_message_tokens(result) <= target_token_budget:
        return result

    result = micro_compact(result, 2048)
    if estimate_message_tokens(result) <= target_token_budget:
        return result

    return micro_compact(result, 512)


def _estimate_content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_estimate_block_chars(block) for block in content)
    if content is None:
        return 0
    return len(_json_repr(content))


def _estimate_block_chars(block: Any) -> int:
    if isinstance(block, str):
        return len(block)
    if not isinstance(block, dict):
        return len(_json_repr(block))

    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return len(block["text"])
    if block_type == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            return len(content)
        return len(_json_repr(block))
    if block_type == "tool_use":
        return len(_json_repr(block.get("input") or {}))
    return len(_json_repr(block))


def _json_repr(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _find_tool_pairs(messages: list[Message] | tuple[Message, ...]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for assistant_index, message in enumerate(messages):
        if message.get("role") != "assistant" or not isinstance(
            message.get("content"),
            list,
        ):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str):
                continue
            user_index = _find_result_index(messages, assistant_index, tool_use_id)
            if user_index >= 0:
                pairs.append(
                    {
                        "assistant_index": assistant_index,
                        "user_index": user_index,
                        "tool_use_id": tool_use_id,
                    }
                )
    return pairs


def _find_result_index(
    messages: list[Message] | tuple[Message, ...],
    assistant_index: int,
    tool_use_id: str,
) -> int:
    for index in range(assistant_index + 1, len(messages)):
        message = messages[index]
        if message.get("role") != "user" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
            ):
                return index
    return -1


def _is_dropped_tool_block(block: Any, drop_tool_use_ids: set[str]) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") == "tool_use" and block.get("id") in drop_tool_use_ids:
        return True
    return (
        block.get("type") == "tool_result"
        and block.get("tool_use_id") in drop_tool_use_ids
    )


def _compact_tool_result_block(block: Any, max_result_chars: int) -> Any:
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return deepcopy(block)
    content = block.get("content")
    if not isinstance(content, str) or len(content) <= max_result_chars:
        return deepcopy(block)

    head_len = int(max_result_chars * 0.6)
    tail_len = int(max_result_chars * 0.3)
    head = content[:head_len]
    tail = content[-tail_len:] if tail_len > 0 else ""
    omitted = len(content) - head_len - tail_len

    copied = deepcopy(block)
    copied["content"] = f"{head}\n...[{omitted} chars omitted]...\n{tail}"
    return copied
