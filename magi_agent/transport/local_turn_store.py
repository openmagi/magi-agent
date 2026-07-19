"""Process-local in-flight turn store + SSE snapshot reducer for local serve.

Why this module exists
----------------------
On the hosted path the chat-proxy sits between the runtime SSE stream and the
browser. It plays three roles the browser depends on for refresh/reconnect:

  1. It keeps the turn running even after the browser tab disconnects (the
     runtime turn is not tied to the browser socket).
  2. It reduces the live SSE frames into an ``active-snapshot`` it stores in
     Redis with a generous TTL (live while running, completed record after).
  3. It answers ``GET .../active-snapshot`` and ``GET .../channel-messages`` so
     a fresh mount can rehydrate the in-progress (or just-finished) turn.

The local ``magi-agent serve`` path has no chat-proxy. The browser talks to the
runtime directly, so a tab refresh (or the two-phase idle watchdog closing the
fetch) tore the SSE generator down and the turn's ``finally`` cancelled it. This
module absorbs the three chat-proxy roles into the runtime process, scoped to
the LOCAL streaming branch only (the hosted gate5b branches are untouched and
stay byte-identical).

Design (mirrors ``infra/docker/chat-proxy/stream-snapshot.js``)
---------------------------------------------------------------
* :class:`LocalSnapshotReducer` ingests SSE byte chunks (the exact frames the
  local driver yields), stitches across chunk boundaries, parses complete
  ``event: agent`` / ``data:`` frames, and accumulates an ``ActiveSnapshot``
  dict field-for-field compatible with the browser reducer
  (``apps/web/src/chat-core/active-snapshot.ts``).
* :class:`LocalTurnStore` is a process-local dict keyed by the reset-aware
  session key (``agent:main:app:<channel>[:<resetCount>]``). While a turn runs it
  holds the live snapshot; when the turn ends it holds a completed-turn record
  (with the final assistant text, so ``channel-messages`` can serve it) under a
  generous TTL.

Everything here is best-effort: a reducer/store fault must never break the live
turn. There is no new feature flag; the behavior is active whenever the local
streaming branch runs (which the local serve profile force-enables via
``MAGI_STREAMING_CHAT``).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "COMPLETED_RECORD_TTL_S",
    "IDLE_ABORT_WATCHDOG_S",
    "LocalSnapshotReducer",
    "LocalTurnStore",
    "LOCAL_TURN_STORE",
    "CompletedTurnRecord",
    "stable_assistant_message_id",
    "stable_user_message_id",
]

# --- Generous budgets (Kevin policy: budgets generous, default-ON) ----------
# Completed-turn records live this long so a browser that refreshes well after a
# turn finished still rehydrates the delivered text from channel-messages. The
# hosted Redis snapshot uses 1800s; the local record is intentionally more
# generous because the local single-user process has no memory pressure.
COMPLETED_RECORD_TTL_S = 3600.0

# The idle-abort watchdog is the ONLY thing that may cancel a detached turn. It
# fires only when the turn produces no new frames for this long (a genuinely
# stuck turn), NOT on browser disconnect. The design suggested 600s; a much more
# generous 1800s avoids ever killing a slow-but-live turn (long tool calls,
# parked control_request waiting on the user across a refresh).
IDLE_ABORT_WATCHDOG_S = 1800.0


_AGENT_EVENT_FRAME_RE = re.compile(r"^event:\s*(agent|agent_event)\s*$")

_VALID_TURN_PHASES = frozenset(
    {
        "pending",
        "planning",
        "executing",
        "verifying",
        "committing",
        "committed",
        "aborted",
    }
)

_MISSION_STATUSES = frozenset(
    {
        "queued",
        "running",
        "blocked",
        "waiting",
        "completed",
        "failed",
        "cancelled",
        "paused",
    }
)

_TERMINAL_MISSION_STATUSES = frozenset({"completed", "cancelled", "failed"})


def _clock_ms(now: float) -> int:
    return int(now * 1000)


def stable_user_message_id(turn_id: str | None) -> str:
    """``<turn_id>:user`` durable/legacy message id (mirrors the ADK path)."""
    return f"{turn_id}:user" if turn_id else "local:user"


def stable_assistant_message_id(turn_id: str | None) -> str:
    """``<turn_id>:assistant`` durable/legacy message id (mirrors the ADK path)."""
    return f"{turn_id}:assistant" if turn_id else "local:assistant"


# Internal alias kept for the in-module call site (finish()).
_assistant_message_id_for = stable_assistant_message_id


def _safe_label(value: object, fallback: str = "tool") -> str:
    if not isinstance(value, str):
        return fallback
    raw = value.strip()
    if not raw:
        return fallback
    raw = re.sub(r"^functions\.", "", raw)
    raw = re.sub(r"\(.*\)$", "", raw)
    return raw.strip() or fallback


def _safe_preview(value: object, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return f"{trimmed[: limit - 3]}..." if len(trimmed) > limit else trimmed


def _safe_text(value: object, fallback: str = "", limit: int = 240) -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    if not trimmed:
        return fallback
    return f"{trimmed[: limit - 3]}..." if len(trimmed) > limit else trimmed


def _tool_status_from_event(status: object) -> str:
    if status == "permission_denied":
        return "denied"
    if status in ("error", "unknown_tool", "aborted"):
        return "error"
    return "done"


def _subagent_status_from_background(value: object) -> str:
    return {
        "completed": "done",
        "failed": "error",
        "aborted": "cancelled",
        "running": "running",
    }.get(value if isinstance(value, str) else "", "running")


def _subagent_status_from_spawn(value: object) -> str:
    return {
        "ok": "done",
        "aborted": "cancelled",
        "error": "error",
    }.get(value if isinstance(value, str) else "", "error")


def _mission_status_from_event(event_type: object) -> str | None:
    return {
        "blocked": "blocked",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "cancel_requested": "cancelled",
        "paused": "paused",
        "resumed": "running",
        "unblocked": "running",
        "heartbeat": "running",
        # The local driver emits mission_event.eventType values without the
        # ``goal_loop_`` prefix (see chat_routes_local); "succeeded"/"failed"
        # map onto the mission status vocabulary.
        "succeeded": "completed",
        "complete": "completed",
        "continuation": "running",
    }.get(event_type if isinstance(event_type, str) else "")


def _parse_frames(text: str) -> list[tuple[bool, dict[str, Any]]]:
    """Split *text* into (is_agent_frame, payload_dict) tuples.

    Mirrors ``parseAgentEvents`` + ``parseDeltas`` frame walking in the JS
    reference: frames are ``\\n\\n``-delimited, ``data:`` lines are stripped of a
    leading space, ``[DONE]`` sentinels are ignored, malformed JSON is skipped.
    """
    out: list[tuple[bool, dict[str, Any]]] = []
    if not text:
        return out
    for frame in re.split(r"\n\n+", text):
        if not frame:
            continue
        is_agent = False
        data_pieces: list[str] = []
        for line in frame.split("\n"):
            if _AGENT_EVENT_FRAME_RE.match(line):
                is_agent = True
                continue
            if line.startswith("data:"):
                piece = line[5:]
                if piece.startswith(" "):
                    piece = piece[1:]
                if piece == "[DONE]":
                    continue
                data_pieces.append(piece)
        if not data_pieces:
            continue
        raw = "\n".join(data_pieces)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append((is_agent, parsed))
    return out


@dataclass
class CompletedTurnRecord:
    """A finished local turn, kept for a generous TTL so a late refresh can
    rehydrate the delivered assistant text via ``channel-messages``."""

    session_id: str
    turn_id: str | None
    role: str
    content: str
    terminal: str | None
    created_at_ms: int
    stored_at: float
    detached_snapshot: dict[str, Any] | None = None
    # Stable cross-source message id (``<turn_id>:assistant``), mirroring the
    # durable channel_messages id scheme + the ADK path's ``<log_turn_id>:*`` ids
    # so the legacy ``completed_messages`` projection and the durable ``full=1``
    # rows agree on identity for the same logical message (no duplicate bubble
    # from a random per-source id).
    message_id: str = ""


class LocalSnapshotReducer:
    """Accumulate live SSE frames into an ``ActiveSnapshot`` dict.

    One instance per turn. Feed raw byte/str chunks via :meth:`ingest`; read the
    running snapshot via :meth:`snapshot`. ``turn_id``/``session_id`` are pinned
    at construction. All ingest work is guarded -- a reducer fault never raises.
    """

    def __init__(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        now: "callable[[], float] | None" = None,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self._now = now or time.monotonic
        self._wall = time.time

        self._buf = ""
        self._content = ""
        self._thinking = ""
        self._started_at: int | None = None
        self._updated_at: int | None = None
        self._has_live_state = False
        self._turn_phase: str | None = None
        self._heartbeat_elapsed_ms: int | None = None
        self._pending_injection_count = 0
        self._active_tools: dict[str, dict[str, Any]] = {}
        self._subagents: dict[str, dict[str, Any]] = {}
        self._task_board: dict[str, Any] | None = None
        self._missions: dict[str, dict[str, Any]] = {}
        self._active_goal_mission_id: str | None = None
        self._current_goal: str | None = None
        self._terminal: str | None = None
        # Status carried by the ``turn_end`` frame (``committed`` / ``aborted``).
        # This is the AUTHORITATIVE turn outcome for durability: a turn can commit
        # (turn_end status=committed) yet still ship a ``turn_result`` frame whose
        # terminal is ``error`` (a citation-gate refusal / blank-turn notice rides
        # the terminal frame). The durable ``incomplete``/``terminal`` fields must
        # follow the committed turn_end, not the contradictory turn_result -- else
        # a fully-committed answer is persisted incomplete=1 terminal='error' and
        # then loses to nothing / reappears truncated. ``None`` means no turn_end
        # frame was seen (e.g. a cancel mid-stream), in which case the turn_result
        # terminal is the only signal and its error/aborted stamping stands.
        self._turn_end_status: str | None = None
        self._last_ingest_monotonic = self._now()

    # -- public API ---------------------------------------------------------

    def ingest(self, chunk: object) -> None:
        try:
            self._ingest_inner(chunk)
        except Exception:  # noqa: BLE001 -- reducer must never break the stream
            return

    def _ingest_inner(self, chunk: object) -> None:
        if isinstance(chunk, (bytes, bytearray)):
            as_str = bytes(chunk).decode("utf-8", errors="replace")
        else:
            as_str = str(chunk or "")
        if not as_str:
            return
        self._last_ingest_monotonic = self._now()
        self._buf += as_str
        last_boundary = self._buf.rfind("\n\n")
        if last_boundary < 0:
            return
        parseable = self._buf[: last_boundary + 2]
        self._buf = self._buf[last_boundary + 2 :]
        for is_agent, payload in _parse_frames(parseable):
            self._apply(is_agent, payload)

    def snapshot(self) -> dict[str, Any] | None:
        """Return the live running snapshot, or ``None`` if nothing to show."""
        if not self._content and not self._thinking and not self._has_live_state:
            return None
        return {
            "turnId": self.turn_id,
            "sessionKey": self.session_id,
            "status": "running",
            "content": self._content,
            "thinking": self._thinking,
            "startedAt": self._started_at,
            "updatedAt": self._updated_at,
            "turnPhase": self._turn_phase,
            "heartbeatElapsedMs": self._heartbeat_elapsed_ms,
            "currentGoal": self._current_goal,
            "pendingInjectionCount": self._pending_injection_count,
            "activeTools": list(self._active_tools.values()),
            "subagents": list(self._subagents.values()),
            "taskBoard": self._task_board,
            "missions": list(self._missions.values()),
            "activeGoalMissionId": self._active_goal_mission_id,
            "inspectedSources": [],
            "citationGate": None,
        }

    def detached_snapshot(self) -> dict[str, Any] | None:
        """Return a detached snapshot when background subagents are still active
        after the parent turn ends, else ``None`` (mirrors the JS finalize)."""
        active = [
            s
            for s in self._subagents.values()
            if s.get("status") in ("running", "waiting")
        ]
        if not active:
            return None
        started = [s.get("startedAt") for s in active if isinstance(s.get("startedAt"), int)]
        started_at = min(started) if started else (self._started_at or _clock_ms(self._wall()))
        return {
            "turnId": self.turn_id,
            "sessionKey": self.session_id,
            "status": "running",
            "detached": True,
            "content": "",
            "thinking": "",
            "startedAt": started_at,
            "updatedAt": _clock_ms(self._wall()),
            "turnPhase": None,
            "heartbeatElapsedMs": None,
            "pendingInjectionCount": 0,
            "activeTools": [],
            "subagents": active,
            "taskBoard": None,
            "missions": list(self._missions.values()),
            "activeGoalMissionId": self._active_goal_mission_id,
            "inspectedSources": [],
            "citationGate": None,
        }

    @property
    def content(self) -> str:
        return self._content

    @property
    def terminal(self) -> str | None:
        return self._terminal

    @property
    def turn_end_status(self) -> str | None:
        return self._turn_end_status

    def effective_terminal(self) -> str | None:
        """Terminal reason for durability, honoring a committed turn_end.

        A committed turn_end wins over a contradictory ``turn_result`` terminal:
        the answer committed, so the durable row is NOT incomplete even when the
        terminal frame carried ``error`` (citation gate / blank-turn notice). An
        aborted turn_end (or, when no turn_end was seen, an error/aborted
        ``turn_result``) keeps the error/aborted stamping. Returns ``None`` when
        the turn is complete (nothing to flag).
        """
        if self._turn_end_status == "committed":
            return None
        if self._turn_end_status == "aborted":
            return "aborted"
        if self._terminal in ("error", "aborted"):
            return self._terminal
        return None

    def effective_incomplete(self) -> bool:
        return self.effective_terminal() is not None

    @property
    def started_at_ms(self) -> int | None:
        return self._started_at

    def idle_seconds(self) -> float:
        return max(0.0, self._now() - self._last_ingest_monotonic)

    # -- internals ----------------------------------------------------------

    def _mark_live(self) -> None:
        now_ms = _clock_ms(self._wall())
        if self._started_at is None:
            self._started_at = now_ms
        self._updated_at = now_ms
        self._has_live_state = True

    def _upsert_tool(self, tool_id: str, label: object, **updates: Any) -> None:
        if not tool_id:
            return
        existing = self._active_tools.get(tool_id)
        now_ms = _clock_ms(self._wall())
        base: dict[str, Any] = {
            "id": tool_id,
            "label": _safe_label(label, (existing or {}).get("label", "tool")),
            "status": "running",
            "startedAt": (existing or {}).get("startedAt", now_ms),
        }
        if existing:
            base.update(existing)
        base.update(updates)
        base["id"] = tool_id
        self._active_tools[tool_id] = base
        self._mark_live()

    def _complete_tool(self, tool_id: str, status: object, duration_ms: object, **updates: Any) -> None:
        if not tool_id:
            return
        existing = self._active_tools.get(tool_id)
        if not existing:
            return
        merged = dict(existing)
        merged["status"] = _tool_status_from_event(status)
        if isinstance(duration_ms, (int, float)):
            merged["durationMs"] = duration_ms
        merged.update(updates)
        self._active_tools[tool_id] = merged
        self._mark_live()

    def _note_subagent(self, task_id: object, **updates: Any) -> None:
        if not isinstance(task_id, str) or not task_id:
            return
        now_ms = _clock_ms(self._wall())
        existing = self._subagents.get(task_id, {})
        role = updates.get("role")
        detail = _safe_preview(updates.get("detail"))
        record = {
            "taskId": task_id,
            "role": _safe_text(role, existing.get("role", "subagent"), 64) or "subagent",
            "status": updates.get("status") or existing.get("status") or "running",
            "startedAt": existing.get("startedAt", now_ms),
            "updatedAt": now_ms,
        }
        if detail is not None:
            record["detail"] = detail
        elif "detail" in existing:
            record["detail"] = existing["detail"]
        self._subagents[task_id] = record
        self._mark_live()

    def _upsert_mission(self, mission: object) -> None:
        if not isinstance(mission, Mapping):
            return
        mission_id = _safe_text(mission.get("id"), "", 120)
        title = _safe_text(mission.get("title"), "Mission", 240)
        if not mission_id or not title:
            return
        existing = self._missions.get(mission_id, {})
        status = mission.get("status")
        if status not in _MISSION_STATUSES:
            status = "running"
        kind = _safe_text(mission.get("kind"), existing.get("kind", "manual"), 80)
        self._missions[mission_id] = {
            "id": mission_id,
            "title": title,
            "kind": kind,
            "status": status,
            "updatedAt": _clock_ms(self._wall()),
        }
        if kind == "goal":
            self._active_goal_mission_id = (
                None if status in _TERMINAL_MISSION_STATUSES else mission_id
            )
            metadata = mission.get("metadata")
            if isinstance(metadata, Mapping):
                objective = metadata.get("objective")
                if isinstance(objective, str) and objective.strip():
                    self._current_goal = _safe_text(objective, "", 240)
        self._mark_live()

    def _apply_mission_event(self, ev: Mapping[str, Any]) -> None:
        mission_id = _safe_text(ev.get("missionId"), "", 120)
        if not mission_id or mission_id not in self._missions:
            return
        status = _mission_status_from_event(ev.get("eventType"))
        if not status:
            return
        existing = self._missions[mission_id]
        detail = _safe_preview(ev.get("reason") or ev.get("message"))
        record = dict(existing)
        record["status"] = status
        record["updatedAt"] = _clock_ms(self._wall())
        if detail is not None:
            record["detail"] = detail
        self._missions[mission_id] = record
        if record.get("kind") == "goal":
            self._active_goal_mission_id = (
                None if status in _TERMINAL_MISSION_STATUSES else mission_id
            )
        self._mark_live()

    def _apply_final_text(self, payload: Mapping[str, Any]) -> None:
        """Fold a final authoritative assistant-text frame with longer-wins.

        Reads the full answer text from ``content`` / ``text`` / ``delta`` (in
        that order). The streamed deltas are the source of truth for length: the
        final text only REPLACES ``_content`` when it is strictly longer than the
        text accumulated from deltas so far, so a truncated or already-streamed
        aggregate can never shorten the durable answer.
        """
        final = None
        for key in ("content", "text", "delta"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                final = value
                break
        if final is None:
            return
        if len(final) > len(self._content):
            self._content = final
            self._mark_live()

    def _apply(self, is_agent: bool, payload: Mapping[str, Any]) -> None:
        kind = payload.get("type") if "type" in payload else payload.get("kind")

        # OpenAI-compat content deltas (choices[].delta.content) -- the local
        # driver frames text via text_delta, but the completions fallback path
        # and any pass-through frame uses this shape, so handle both.
        if not is_agent or kind is None:
            choices = payload.get("choices")
            if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)):
                for ch in choices:
                    if isinstance(ch, Mapping):
                        delta = ch.get("delta")
                        if isinstance(delta, Mapping):
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                self._content += content
                                self._mark_live()
            if kind is None:
                return

        if kind == "text_delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._content += delta
                self._mark_live()
        elif kind == "thinking_delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._thinking += delta
                self._mark_live()
        elif kind == "response_clear":
            self._content = ""
            self._thinking = ""
            self._mark_live()
        elif kind == "turn_start":
            tid = payload.get("turnId") or payload.get("turn_id")
            if isinstance(tid, str):
                self.turn_id = tid
            if not self._turn_phase:
                self._turn_phase = "pending"
            self._mark_live()
        elif kind == "turn_phase":
            tid = payload.get("turnId") or payload.get("turn_id")
            if isinstance(tid, str):
                self.turn_id = tid
            phase = payload.get("phase")
            if phase in _VALID_TURN_PHASES:
                self._turn_phase = phase
            self._mark_live()
        elif kind == "heartbeat":
            elapsed = payload.get("elapsedMs")
            if isinstance(elapsed, (int, float)):
                self._heartbeat_elapsed_ms = int(elapsed)
                self._mark_live()
        elif kind == "injection_queued":
            count = payload.get("queuedCount")
            if isinstance(count, (int, float)):
                self._pending_injection_count = int(count)
                self._mark_live()
        elif kind == "injection_drained":
            self._pending_injection_count = 0
            self._mark_live()
        elif kind == "tool_start":
            tool_id = payload.get("id")
            tool_id = tool_id if isinstance(tool_id, str) and tool_id else f"ag-{len(self._active_tools)}"
            updates: dict[str, Any] = {}
            preview = _safe_preview(payload.get("input_preview"))
            if preview:
                updates["inputPreview"] = preview
            self._upsert_tool(tool_id, payload.get("name"), **updates)
        elif kind == "tool_end":
            tool_id = payload.get("id")
            tool_id = tool_id if isinstance(tool_id, str) else ""
            updates = {}
            preview = _safe_preview(payload.get("output_preview"))
            if preview:
                updates["outputPreview"] = preview
            self._complete_tool(tool_id, payload.get("status"), payload.get("durationMs"), **updates)
        elif kind in ("spawn_started", "child_started"):
            self._note_subagent(payload.get("taskId"), role=payload.get("persona"), status="running")
        elif kind == "background_task":
            self._note_subagent(
                payload.get("taskId"),
                role=payload.get("persona"),
                status=_subagent_status_from_background(payload.get("status")),
                detail=payload.get("detail"),
            )
        elif kind == "spawn_result":
            self._note_subagent(payload.get("taskId"), status=_subagent_status_from_spawn(payload.get("status")))
        elif kind == "child_progress":
            self._note_subagent(payload.get("taskId"), status="running", detail=payload.get("detail"))
        elif kind == "child_completed":
            self._note_subagent(payload.get("taskId"), status="done")
        elif kind == "child_cancelled":
            self._note_subagent(payload.get("taskId"), status="cancelled", detail=payload.get("reason"))
        elif kind == "child_failed":
            self._note_subagent(payload.get("taskId"), status="error", detail=payload.get("errorMessage"))
        elif kind == "task_board":
            board = self._normalize_task_board(payload)
            if board is not None:
                self._task_board = board
                self._mark_live()
        elif kind == "mission_created":
            self._upsert_mission(payload.get("mission"))
        elif kind == "mission_updated":
            self._upsert_mission(payload.get("mission"))
        elif kind == "mission_event":
            self._apply_mission_event(payload)
        elif kind == "turn_interrupted":
            self._turn_phase = "aborted"
            self._mark_live()
        elif kind in ("message", "message_completed", "final_text", "response_completed"):
            # Final authoritative assistant text frame (A1). The public SSE wire
            # today carries answer text ONLY as incremental ``text_delta`` frames
            # (and OpenAI-compat ``choices[].delta.content``), both folded above,
            # so this handler is a no-op on the current wire and stays byte-
            # identical. It exists so that IF a transport ever emits a final full-
            # text aggregate frame (the whole answer in one frame, distinct from a
            # delta), the durable content adopts it with LONGER-WINS semantics
            # instead of appending it -- so the durable row can never be a prefix
            # of, nor a duplicate-concatenation of, the streamed answer.
            self._apply_final_text(payload)
        elif kind == "turn_end":
            status = payload.get("status")
            if status == "aborted":
                self._turn_phase = "aborted"
                self._turn_end_status = "aborted"
            elif status == "committed":
                self._turn_phase = "committed"
                self._turn_end_status = "committed"
            self._mark_live()
        elif kind == "turn_result":
            tid = payload.get("turn_id") or payload.get("turnId")
            if isinstance(tid, str) and tid:
                self.turn_id = tid
            terminal = payload.get("terminal")
            self._terminal = terminal if isinstance(terminal, str) else None
            if terminal in ("error", "aborted"):
                self._turn_phase = "aborted"
            elif terminal in ("completed", "max_turns"):
                self._turn_phase = "committed"
            self._mark_live()

    def _normalize_task_board(self, ev: Mapping[str, Any]) -> dict[str, Any] | None:
        raw_tasks = ev.get("tasks")
        if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, (str, bytes)):
            return None
        tasks: list[dict[str, Any]] = []
        for task in raw_tasks:
            if not isinstance(task, Mapping):
                continue
            tid = task.get("id")
            title = task.get("title")
            status = task.get("status")
            if not isinstance(tid, str) or not isinstance(title, str):
                continue
            if status not in ("pending", "in_progress", "completed", "cancelled"):
                continue
            entry: dict[str, Any] = {
                "id": tid,
                "title": title,
                "description": task.get("description") if isinstance(task.get("description"), str) else "",
                "status": status,
            }
            pg = task.get("parallelGroup")
            if isinstance(pg, str):
                entry["parallelGroup"] = pg
            depends = task.get("dependsOn")
            if isinstance(depends, Sequence) and not isinstance(depends, (str, bytes)):
                dep_list = [d for d in depends if isinstance(d, str)]
                if dep_list:
                    entry["dependsOn"] = dep_list
            tasks.append(entry)
        if not tasks:
            return None
        return {"tasks": tasks, "receivedAt": _clock_ms(self._wall())}


@dataclass
class _LiveEntry:
    reducer: LocalSnapshotReducer
    started_at: float = field(default_factory=time.time)


class LocalTurnStore:
    """Process-local, thread-safe map of sessionId -> live-or-completed turn.

    A single instance (:data:`LOCAL_TURN_STORE`) is shared by the local
    streaming branch (writer) and the two GET endpoints (readers). Keys are the
    reset-aware session key the browser sends and polls with, so a reset
    (``:<n>`` suffix) naturally scopes to a fresh entry.
    """

    def __init__(self, *, completed_ttl_s: float = COMPLETED_RECORD_TTL_S) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, _LiveEntry] = {}
        self._completed: dict[str, CompletedTurnRecord] = {}
        self._completed_ttl_s = completed_ttl_s

    # -- writer side (local streaming branch) -------------------------------

    def begin(self, session_id: str, reducer: LocalSnapshotReducer) -> None:
        if not session_id:
            return
        with self._lock:
            self._live[session_id] = _LiveEntry(reducer=reducer)
            # Do NOT drop the prior completed record here. A new turn starting
            # must not destroy the previous turn's committed answer before the
            # client has rehydrated it: the sub-3s send race (follow-up sent
            # right after a truncated turn) otherwise wiped the only server copy
            # and the answer vanished. ``finish()`` overwrites the record when
            # THIS turn completes; TTL eviction handles genuine staleness.

    def finish(self, session_id: str, reducer: LocalSnapshotReducer) -> None:
        """Move a finished turn from live to a completed record (or detached)."""
        if not session_id:
            return
        with self._lock:
            live = self._live.get(session_id)
            # Only clear the live slot if it still belongs to THIS reducer; a
            # newer turn may have replaced it (multi-tab / rapid re-send).
            if live is not None and live.reducer is reducer:
                self._live.pop(session_id, None)
            elif live is not None:
                # A newer turn owns the slot -- do not overwrite its live state.
                return
            detached = reducer.detached_snapshot()
            self._completed[session_id] = CompletedTurnRecord(
                session_id=session_id,
                turn_id=reducer.turn_id,
                role="assistant",
                content=reducer.content,
                # Honor a committed turn_end over a contradictory turn_result
                # terminal so a committed answer is not persisted incomplete.
                terminal=reducer.effective_terminal(),
                created_at_ms=reducer.started_at_ms or _clock_ms(time.time()),
                stored_at=time.time(),
                detached_snapshot=detached,
                message_id=_assistant_message_id_for(reducer.turn_id),
            )

    # -- reader side (GET endpoints) ----------------------------------------

    def active_snapshot(self, session_id: str) -> dict[str, Any] | None:
        if not session_id:
            return None
        self._evict_expired()
        with self._lock:
            live = self._live.get(session_id)
            if live is not None:
                snap = live.reducer.snapshot()
                if snap is not None:
                    return snap
            completed = self._completed.get(session_id)
            if completed is not None and completed.detached_snapshot is not None:
                return completed.detached_snapshot
        return None

    def completed_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return committed assistant message(s) for a just-finished turn.

        Shaped like the hosted ``channel-messages`` server payload: a list of
        ``{role, content, createdAt, turnId}`` entries. A turn that errored or
        aborted mid-stream but still produced visible text is delivered too,
        flagged ``incomplete``: dropping it made a truncated answer VANISH on
        the next turn (the client's error-recovery path never re-committed it,
        so the server copy was the only survivor). Only genuinely empty turns
        deliver nothing.
        """
        if not session_id:
            return []
        self._evict_expired()
        with self._lock:
            record = self._completed.get(session_id)
            if record is None:
                return []
            if not record.content:
                return []
            message: dict[str, Any] = {
                "role": record.role,
                "content": record.content,
                "createdAt": record.created_at_ms,
                "turnId": record.turn_id,
                # Stable cross-source id so the legacy projection and the durable
                # full=1 rows agree on identity (the frontend dedups by id first).
                "messageId": record.message_id
                or stable_assistant_message_id(record.turn_id),
            }
            if record.terminal in ("error", "aborted"):
                message["incomplete"] = True
                message["terminal"] = record.terminal
            return [message]

    def live_reducer(self, session_id: str) -> LocalSnapshotReducer | None:
        with self._lock:
            live = self._live.get(session_id)
            return live.reducer if live is not None else None

    def _evict_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                key
                for key, rec in self._completed.items()
                if now - rec.stored_at >= self._completed_ttl_s
            ]
            for key in expired:
                self._completed.pop(key, None)

    # -- test/introspection helpers -----------------------------------------

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._live.clear()
            self._completed.clear()


# Process-global singleton shared by the writer branch + reader endpoints.
LOCAL_TURN_STORE = LocalTurnStore()
