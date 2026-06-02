"""Append-only JSONL session log for the Magi CLI (Stream B, PR-B1).

This module persists the durable stream of :class:`RuntimeEvent` objects a CLI
turn produces, one JSON object ("envelope") per line. It is the storage
substrate that PR-B2 (resume/continue) and Stream F (wiring) build on. This PR
deliberately implements ONLY the append-only writer + a tolerant reader — no
resume/rehydration logic lives here.

Design constraints (mirrors ``cli/contracts.py``)
-------------------------------------------------
- Additive, default-OFF. Nothing here runs unless a caller constructs a
  ``SessionLog``.
- Zero heavy deps. Pure stdlib + ``cli.contracts``. No ``textual``,
  ``google-adk``/``google-genai``, or ``rich`` imports, so ``cli`` stays
  import-clean (``import openmagi_core_agent.cli.session_log`` must succeed with
  none of those installed).

Storage path scheme
--------------------
Claude Code stores sessions at ``~/.claude/projects/<cwd-slug>/<id>.jsonl``.
We pick a Magi-appropriate analogue:

    <root>/projects/<cwd-slug>/<session_id>.jsonl

where:
- ``<root>`` is ``~/.magi`` by default, overridable via the ``MAGI_CLI_SESSION_DIR``
  environment variable (so tests and Stream F can redirect it to ``tmp_path``).
- ``<cwd-slug>`` is the current working directory path slugified: every run of
  non-alphanumeric characters is collapsed to a single ``-`` (mirroring Claude
  Code's project-scoping so logs for different working directories never
  collide).
- ``<session_id>`` is the engine session id.

Rationale: scoping by ``(cwd, session_id)`` keeps each project's sessions
isolated and human-discoverable, matches the established Claude Code layout, and
keeps the on-disk format trivially greppable / tail-able.

Envelope + DAG
--------------
Each appended ``RuntimeEvent`` becomes one JSONL line wrapped in an envelope::

    {"uuid": <uuid4>, "parent_uuid": <parent uuid or null>, "ts": <epoch float>,
     "type": <event.type>, "payload": <event.payload>, "turn_id": <event.turn_id>}

``parent_uuid`` points at an explicit parent envelope (NOT merely "the previous
line"), so the chain is a DAG that can branch/rewind. By default an append
chains off the last-appended uuid (linear history); callers may pass an explicit
``parent_uuid`` to fork from an arbitrary earlier node.

Sync IO under an async caller
-----------------------------
This module is intentionally synchronous. ``flush()`` issues an ``os.fsync``,
which is a blocking syscall; batching (``flush_interval_s``, ~100ms) bounds how
often it fires but not its per-call latency. A caller running on an asyncio
event loop should invoke ``append``/``flush``/``close`` via ``asyncio.to_thread``
(or an equivalent executor) so the loop is never blocked — Stream F owns that
wiring when it connects the log to the engine drain.

Caller preconditions (SessionLog is a verbatim persister)
---------------------------------------------------------
- **Sanitization is the caller's job.** ``append`` writes ``event.payload``
  verbatim and performs NO redaction (it must stay import-clean of
  ``transport.sse``). Callers MUST pass events already projected to the public /
  sanitized agent-event shape (the CLI engine yields events through
  ``transport.sse._sanitize_agent_event``). The user-turn event
  (``{"type": "user_message", "content": ...}``) is persisted RAW by design — a
  faithful local-disk transcript, mirroring Claude Code's ``~/.claude/projects``.
  Session files are created owner-only (0600) to protect that transcript at rest.
- **Single writer per path.** One ``SessionLog`` per ``(bot_id, session_id, cwd)``
  at a time. Concurrent writers to the same file are NOT guarded (independent
  ``_last_uuid`` chains + non-atomic multi-line flushes would corrupt the DAG).
  The per-invocation session id makes this the norm; do not double-open a path.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from openmagi_core_agent.cli.contracts import RuntimeEvent

__all__ = [
    "Envelope",
    "SessionLog",
    "load",
    "resolve_session_path",
    "slugify_cwd",
    "DEFAULT_SESSION_ROOT_ENV",
    # PR-B2 resume / continue surface.
    "ResumeContext",
    "reconstruct_linear_chain",
    "reconstruct_messages",
    "resume",
    "resume_async",
    "continue_latest",
    "prepare_resume",
    "prepare_resume_async",
]

# Environment variable that overrides the on-disk session root.
DEFAULT_SESSION_ROOT_ENV = "MAGI_CLI_SESSION_DIR"

# Default flush cadence (seconds). Appends buffer in memory and flush to disk
# once at least this much wall-clock time has elapsed since the last flush, or
# on an explicit ``flush()`` / ``close()``.
_DEFAULT_FLUSH_INTERVAL_S = 0.1

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass
class Envelope:
    """One persisted line: a ``RuntimeEvent`` plus DAG/identity metadata.

    Fields are typed ``| None`` because :func:`load` is a *tolerant* reader:
    writer-produced lines always populate every field, but a hand-edited or
    crash-truncated-but-still-valid-JSON line may be missing keys, in which case
    the corresponding field is ``None``. Downstream consumers (PR-B2 resume) must
    not assume a non-``None`` ``uuid``/``type``/``payload`` for arbitrary inputs.
    """

    uuid: str | None
    parent_uuid: str | None
    ts: float | None
    type: str | None
    payload: dict[str, object] | None
    turn_id: str | None


def slugify_cwd(cwd: str | os.PathLike[str]) -> str:
    """Slugify a working-directory path into a stable, filesystem-safe token.

    Collapses every run of non-alphanumeric characters into a single ``-`` and
    trims leading/trailing dashes. Deterministic for a given input.
    """

    slug = _NON_ALNUM.sub("-", str(cwd)).strip("-")
    return slug or "root"


def _safe_token(value: str) -> str:
    """Sanitize a ``bot_id``/``session_id`` for safe use inside a filename.

    ``resolve_session_path`` accepts externally-supplied ids (``--resume <id>``
    via :func:`prepare_resume`), so a raw value like ``../../.bashrc`` must not
    be allowed to escape the project dir. We collapse every run of characters
    outside ``[A-Za-z0-9._-]`` to ``-`` and strip leading dots/dashes so no
    ``..`` or path separator survives. Empty input → ``""`` (callers treat an
    empty ``bot_id`` as "no scope").
    """

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    # Neutralize any ``..`` / leading-dot traversal that survived as literals.
    cleaned = cleaned.replace("..", "-").lstrip(".-")
    return cleaned


def _session_root() -> Path:
    """Resolve the session root, honoring ``MAGI_CLI_SESSION_DIR``."""

    override = os.environ.get(DEFAULT_SESSION_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".magi"


def resolve_session_path(
    bot_id: str,
    session_id: str,
    cwd: str | os.PathLike[str] | None = None,
) -> Path:
    """Compute the JSONL path for a ``(bot_id, session_id)`` under ``cwd``.

    Layout: ``<root>/projects/<cwd-slug>/<session_id>.jsonl``. ``bot_id`` scopes
    the session id (``<bot_id>__<session_id>``) so two bots cannot collide on the
    same session id within a project. Reusable by PR-B2 and Stream F.

    ``bot_id``/``session_id`` are sanitized via :func:`_safe_token` so an
    externally-supplied id cannot use ``../`` to escape the project dir.
    """

    if cwd is None:
        cwd = os.getcwd()
    root = _session_root()
    slug = slugify_cwd(cwd)
    safe_bot = _safe_token(bot_id)
    safe_session = _safe_token(session_id) or "session"
    file_stem = f"{safe_bot}__{safe_session}" if safe_bot else safe_session
    return root / "projects" / slug / f"{file_stem}.jsonl"


class SessionLog:
    """Append-only JSONL writer for durable ``RuntimeEvent`` envelopes.

    Construct with an explicit ``path`` OR with ``bot_id`` + ``session_id``
    (+ optional ``cwd``) to derive the path via :func:`resolve_session_path`.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        bot_id: str | None = None,
        session_id: str | None = None,
        cwd: str | os.PathLike[str] | None = None,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        if path is None:
            if session_id is None:
                raise ValueError(
                    "SessionLog requires either `path` or `session_id` "
                    "(with optional bot_id/cwd)."
                )
            path = resolve_session_path(bot_id or "", session_id, cwd)
        self.path = Path(path)
        self._flush_interval_s = max(0.0, float(flush_interval_s))

        self._buffer: list[str] = []
        self._last_uuid: str | None = None
        self._last_flush = time.monotonic()
        self._fh: TextIO | None = None
        self._closed = False

    # -- internals ---------------------------------------------------------
    def _ensure_open(self) -> None:
        if self._fh is None:
            # Session logs hold the raw user/assistant transcript at rest, so
            # restrict to owner-only: dirs 0700, file 0600 (independent of the
            # process umask). On multi-user hosts this keeps the transcript
            # private. Existing dirs/files are left as-is (exist_ok / append).
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            self._fh = os.fdopen(fd, "a", encoding="utf-8")

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_flush >= self._flush_interval_s:
            self.flush()

    # -- public API --------------------------------------------------------
    def append(self, event: RuntimeEvent, *, parent_uuid: str | None = None) -> str:
        """Append ``event`` as one envelope; return the new envelope uuid.

        When ``parent_uuid`` is None the envelope chains off the last-appended
        uuid (linear history). Pass an explicit ``parent_uuid`` to fork the DAG
        from an arbitrary earlier node.
        """

        if self._closed:
            raise ValueError("SessionLog is closed")

        new_uuid = str(uuid.uuid4())
        parent = parent_uuid if parent_uuid is not None else self._last_uuid

        data = event.model_dump()
        envelope = {
            "uuid": new_uuid,
            "parent_uuid": parent,
            "ts": time.time(),
            "type": data.get("type"),
            "payload": data.get("payload"),
            "turn_id": data.get("turn_id"),
        }
        # Each line is fully serialized in memory (trailing newline) before any
        # IO. A graceful close() flushes everything; on SIGKILL/power-loss only
        # the unflushed buffer (≤ flush_interval_s of appends) is lost, and a
        # flush interrupted mid-write can leave a torn TRAILING line — which
        # load() tolerates. Interior lines, once flushed, are never corrupted by
        # this writer (append-only).
        self._buffer.append(json.dumps(envelope, ensure_ascii=False) + "\n")
        self._last_uuid = new_uuid
        self._maybe_flush()
        return new_uuid

    def flush(self) -> None:
        """Force buffered lines to disk (write + OS-level flush)."""

        if self._closed:
            return
        if self._buffer:
            self._ensure_open()
            assert self._fh is not None
            self._fh.write("".join(self._buffer))
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._buffer.clear()
        self._last_flush = time.monotonic()

    def close(self) -> None:
        """Final flush + release the file handle. Idempotent."""

        if self._closed:
            return
        self.flush()
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._closed = True

    def __enter__(self) -> "SessionLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def load(path: str | os.PathLike[str]) -> list[Envelope]:
    """Parse a JSONL session file into envelopes, in file order.

    Blank lines and a trailing partial/blank line are skipped gracefully. A
    nonexistent file yields an empty list.
    """

    p = Path(path)
    if not p.exists():
        return []

    envelopes: list[Envelope] = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                # Skip ANY unparseable line (not only the trailing one). In
                # normal operation only the trailing line can be torn (see
                # append); an interior unparseable line (manual edit / bit-rot)
                # is also dropped, which can truncate the resumed chain at a
                # dangling parent — acceptable for a best-effort reader.
                continue
            if not isinstance(obj, dict):
                continue
            envelopes.append(
                Envelope(
                    uuid=obj.get("uuid"),
                    parent_uuid=obj.get("parent_uuid"),
                    ts=obj.get("ts"),
                    type=obj.get("type"),
                    payload=obj.get("payload"),
                    turn_id=obj.get("turn_id"),
                )
            )
    return envelopes


# ---------------------------------------------------------------------------
# PR-B2: resume / continue
# ---------------------------------------------------------------------------
#
# Resume reconstructs prior context from a session log. The rehydration
# primitives below (:func:`resume_async` / :func:`prepare_resume_async` /
# :func:`_rehydrate`) ARE IMPLEMENTED — they reconstruct the linear transcript
# and feed it through ``SessionContinuityBoundary.import_committed_transcript``.
#
# v1 STATUS (NOT yet wired): in v1 these primitives are NOT called by the CLI
# entrypoint or the engine. The entrypoint only threads a session-id label, and
# ``engine._drive`` reads ``initial_messages`` but does not feed them into the
# runner. Wiring resume into the live turn loop is a v1.1 follow-up; the code is
# kept here (tested in isolation) so that work is a small wire-up, not a rewrite.
#
# Import-cleanliness contract (identical to ``cli/engine.py``)
# -----------------------------------------------------------
# Everything in *this* file's top-level import block is pure stdlib + the
# already-clean ``cli.contracts`` re-export. The rehydration step reuses
# ``runtime`` modules that DO pull in pydantic / google-adk / google-genai
# (``runtime.session_continuity``, ``runtime.transcript``,
# ``adk_bridge.session_service``). Those are imported LAZILY inside
# :func:`_lazy_resume_deps`, which runs only when :func:`resume` is actually
# called — so ``import openmagi_core_agent.cli.session_log`` succeeds with none
# of those installed. Rehydration is BEST-EFFORT: any failure to import or
# convert leaves ``ResumeContext`` populated with the pure message list and a
# ``reason`` rather than raising.


@dataclass
class ResumeContext:
    """Reconstructed prior context for a resumed/continued CLI session.

    ``initial_messages`` is always populated (pure, ADK-free). The remaining
    fields describe the optional ADK rehydration hand-off and are ``None`` when
    rehydration was skipped or could not run (``reason`` says why).
    """

    session_id: str
    initial_messages: list[dict[str, str]] = field(default_factory=list)
    session_service: object | None = None
    session: object | None = None
    continuity_result: object | None = None
    reason: str | None = None


def reconstruct_linear_chain(envelopes: list[Envelope]) -> list[Envelope]:
    """Collapse the ``parent_uuid`` DAG into a single linear ordered chain.

    Strategy:
    - Build ``{uuid: Envelope}`` and ``{uuid: parent_uuid}`` maps (ignoring
      envelopes whose ``uuid`` is ``None`` — a tolerant-reader artifact).
    - The **leaf tip** is the envelope that is no other envelope's
      ``parent_uuid``. If several leaves exist (a branched/forked DAG) pick the
      most-recently-appended one = the last such leaf in file order.
    - Walk ``parent_uuid`` from the tip back to the root, then reverse so the
      result is root -> tip order.
    - Defensive cycle guard: stop if a uuid repeats on the walk.

    Disjoint chains (two independent roots — e.g. a concatenated/corrupted log)
    intentionally resolve to the most-recently-appended chain only; the other
    chain is dropped. A ``parent_uuid`` pointing at a uuid absent from the file
    (dangling parent) terminates the walk cleanly at that node.

    Returns ``[]`` for an empty input or when no usable ``uuid`` is present.
    """

    by_uuid: dict[str, Envelope] = {}
    parent_of: dict[str, str | None] = {}
    order: list[str] = []
    for env in envelopes:
        if not isinstance(env.uuid, str) or not env.uuid:
            continue
        by_uuid[env.uuid] = env
        parent_of[env.uuid] = env.parent_uuid
        order.append(env.uuid)

    if not by_uuid:
        return []

    # A uuid is a "parent" if some other envelope points at it.
    referenced_parents = {
        parent for parent in parent_of.values() if isinstance(parent, str)
    }
    leaves = [uid for uid in order if uid not in referenced_parents]
    if not leaves:
        # Fully cyclic / self-referential: fall back to the last appended node.
        tip = order[-1]
    else:
        # Most-recently-appended leaf = last in file order.
        tip = leaves[-1]

    chain: list[Envelope] = []
    seen: set[str] = set()
    cursor: str | None = tip
    while cursor is not None and cursor in by_uuid and cursor not in seen:
        seen.add(cursor)
        chain.append(by_uuid[cursor])
        cursor = parent_of.get(cursor)
    chain.reverse()
    return chain


# Public agent-event "type" values (engine.py / runtime.events vocabulary) that
# carry assistant text deltas.
_ASSISTANT_TEXT_TYPES = frozenset({"text_delta"})
# Payload shapes that denote a persisted USER message.
_USER_MESSAGE_TYPES = frozenset({"user_message", "user_input", "user_text"})
# Payload ``type`` values the forward map constructs a TranscriptEntry for. Used
# to distinguish a real construction failure (drop) from an intentionally-skipped
# unmapped type.
_MAPPED_ENTRY_TYPES = frozenset(
    {"turn_start", "text_delta", "tool_start", "tool_end", "turn_end", "control_event"}
)


def _payload_user_text(payload: dict[str, object]) -> str | None:
    """Extract user-message text from a persisted RuntimeEvent payload.

    Tolerant of several shapes Stream F may persist a user turn as:
    ``{"type":"user_message","content":...}``, ``{"role":"user","content":...}``,
    or a nested ``{"message":{"role":"user","content":...}}``.
    """

    p_type = payload.get("type")
    role = payload.get("role")
    is_user = p_type in _USER_MESSAGE_TYPES or role == "user"
    nested = payload.get("message")
    if not is_user and isinstance(nested, dict) and nested.get("role") == "user":
        is_user = True
        payload = nested
    if not is_user:
        return None
    for key in ("content", "text", "prompt", "delta", "message_text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def reconstruct_messages(chain: list[Envelope]) -> list[dict[str, str]]:
    """Rebuild a linear ``list[{"role","content"}]`` from linear envelopes.

    - Consecutive assistant ``text_delta`` payloads accumulate into ONE
      assistant message (deltas concatenated in order).
    - A persisted user-message event becomes a user message, flushing any
      pending assistant accumulation first.
    - Envelope/DAG metadata is stripped; only ``role``/``content`` survive.
    - Deterministic and pure (no ADK / network).
    """

    messages: list[dict[str, str]] = []
    pending_assistant: list[str] = []

    def _flush_assistant() -> None:
        if pending_assistant:
            messages.append(
                {"role": "assistant", "content": "".join(pending_assistant)}
            )
            pending_assistant.clear()

    for env in chain:
        payload = env.payload if isinstance(env.payload, dict) else {}
        p_type = payload.get("type")

        if p_type in _ASSISTANT_TEXT_TYPES:
            delta = payload.get("delta")
            if isinstance(delta, str):
                pending_assistant.append(delta)
            continue

        user_text = _payload_user_text(payload)
        if user_text is not None:
            _flush_assistant()
            messages.append({"role": "user", "content": user_text})
            continue

        # Any other event type (tool_start/tool_end/turn_start/turn_end/...) is
        # a turn boundary for assistant-text purposes: flush so a later turn's
        # assistant text does not merge with this one.
        if p_type in {"turn_end", "turn_start"}:
            _flush_assistant()

    _flush_assistant()
    return messages


def _lazy_resume_deps() -> dict[str, object]:
    """Import the heavy ADK/runtime rehydration symbols lazily.

    Mirrors ``cli.engine._lazy_engine_deps``: called only inside :func:`resume`,
    so the module stays import-clean without google-adk / google-genai.
    """

    from openmagi_core_agent.adk_bridge.session_service import (
        WorkspaceSessionService,
    )
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )
    from openmagi_core_agent.runtime.transcript import (
        AssistantTextEntry,
        ControlEventTranscriptEntry,
        ToolCallEntry,
        ToolResultEntry,
        TurnAbortedEntry,
        TurnCommittedEntry,
        TurnStartedEntry,
    )

    return {
        "WorkspaceSessionService": WorkspaceSessionService,
        "SessionContinuityBoundary": SessionContinuityBoundary,
        "SessionContinuityConfig": SessionContinuityConfig,
        "AssistantTextEntry": AssistantTextEntry,
        "ControlEventTranscriptEntry": ControlEventTranscriptEntry,
        "ToolCallEntry": ToolCallEntry,
        "ToolResultEntry": ToolResultEntry,
        "TurnAbortedEntry": TurnAbortedEntry,
        "TurnCommittedEntry": TurnCommittedEntry,
        "TurnStartedEntry": TurnStartedEntry,
    }


def _ts_int(value: object) -> int:
    """Coerce an envelope ``ts`` to an int (TranscriptEntry.ts is int|float)."""

    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return 0
    return 0


def _envelopes_to_transcript_entries(
    chain: list[Envelope],
    deps: dict[str, object],
) -> tuple[list[object], int, str | None]:
    """FORWARD map linear envelopes -> ``TranscriptEntry`` objects.

    Returns ``(entries, dropped_count, first_error)``. ``dropped_count`` counts
    convertible-type payloads whose entry construction RAISED (a malformed
    payload — or, importantly, a systematic kwarg/Literal break that would
    otherwise silently zero out the whole transcript and look like an empty
    session). ``first_error`` is the first such exception's message, so callers
    can surface a ``rehydration_partial`` reason instead of an indistinguishable
    ``no_committed_history``. Payloads of unmapped types are NOT counted as
    drops (they are intentionally skipped).

    This is the inverse of ``runtime.events.transcript_entries_to_agent_events``
    (entries -> public agent-event dicts). Our persisted ``payload`` IS such a
    public agent-event dict, so we map back per its ``type``:

        text_delta -> AssistantTextEntry(text=delta)
        tool_start -> ToolCallEntry(toolUseId=id, name=name, input=input_preview)
        tool_end   -> ToolResultEntry(toolUseId=id, status=..., output=output_preview)
        turn_start -> TurnStartedEntry(declaredRoute=...)
        turn_end committed -> TurnCommittedEntry   (so read_committed() is non-empty)
        turn_end aborted   -> TurnAbortedEntry      (likewise)
        control_event      -> ControlEventTranscriptEntry

    A persisted user message becomes a user-authored ``AssistantTextEntry``-shaped
    entry is NOT used; instead we skip it for the committed-transcript hand-off
    (ADK rehydration replays committed history; the user prompt is supplied by
    the live turn). Unknown/partial payloads are skipped (best-effort).
    """

    entries: list[object] = []
    dropped = 0
    first_error: str | None = None
    for env in chain:
        payload = env.payload if isinstance(env.payload, dict) else {}
        p_type = payload.get("type")
        turn_id = env.turn_id or payload.get("turnId") or payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            turn_id = "turn"
        ts = _ts_int(env.ts)

        try:
            if p_type == "turn_start":
                route = payload.get("declaredRoute")
                entries.append(
                    deps["TurnStartedEntry"](  # type: ignore[operator]
                        ts=ts,
                        turnId=turn_id,
                        declaredRoute=route if isinstance(route, str) and route else "direct",
                    )
                )
            elif p_type == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    entries.append(
                        deps["AssistantTextEntry"](  # type: ignore[operator]
                            ts=ts, turnId=turn_id, text=delta
                        )
                    )
            elif p_type == "tool_start":
                tool_id = payload.get("id")
                if isinstance(tool_id, str) and tool_id:
                    entries.append(
                        deps["ToolCallEntry"](  # type: ignore[operator]
                            ts=ts,
                            turnId=turn_id,
                            toolUseId=tool_id,
                            name=str(payload.get("name") or "unknown_tool"),
                            input=payload.get("input_preview", {}),
                        )
                    )
            elif p_type == "tool_end":
                tool_id = payload.get("id")
                if isinstance(tool_id, str) and tool_id:
                    status = payload.get("status")
                    status_str = status if isinstance(status, str) and status else "ok"
                    output = payload.get("output_preview")
                    entries.append(
                        deps["ToolResultEntry"](  # type: ignore[operator]
                            ts=ts,
                            turnId=turn_id,
                            toolUseId=tool_id,
                            status=status_str,
                            output=output if isinstance(output, str) else None,
                            isError=status_str not in {"ok", "needs_approval"},
                        )
                    )
            elif p_type == "turn_end":
                status = payload.get("status")
                if status == "aborted":
                    reason = payload.get("reason")
                    entries.append(
                        deps["TurnAbortedEntry"](  # type: ignore[operator]
                            ts=ts,
                            turnId=turn_id,
                            reason=str(reason or "aborted"),
                        )
                    )
                else:
                    # Token counts are unrecoverable from the persisted public
                    # envelope (the turn_end agent-event carries no usage), so 0
                    # is the only honest value — intentionally lossy round-trip.
                    entries.append(
                        deps["TurnCommittedEntry"](  # type: ignore[operator]
                            ts=ts,
                            turnId=turn_id,
                            inputTokens=0,
                            outputTokens=0,
                        )
                    )
            elif p_type == "control_event":
                event_id = payload.get("eventId")
                event_type = payload.get("eventType")
                if isinstance(event_id, str) and event_id:
                    entries.append(
                        deps["ControlEventTranscriptEntry"](  # type: ignore[operator]
                            ts=ts,
                            turnId=turn_id,
                            seq=int(payload.get("seq") or 0),
                            eventId=event_id,
                            eventType=str(event_type or "control_resumed"),
                        )
                    )
            # else: user messages + unknown types skipped for the committed
            # transcript hand-off (best-effort).
        except Exception as exc:  # noqa: BLE001 - tolerate a malformed payload
            # Only a MAPPED type that raised is a real drop (a systematic
            # kwarg/Literal break). Unmapped types never reach construction.
            if p_type in _MAPPED_ENTRY_TYPES:
                dropped += 1
                if first_error is None:
                    first_error = f"{type(exc).__name__}: {exc}"
            continue
    return entries, dropped, first_error


# Entry kinds that mark a completed turn, mirroring runtime.TranscriptStore.
_COMMITTED_KINDS = frozenset({"turn_committed", "turn_aborted"})
# Trailing kinds the real store keeps after the last committed/aborted turn.
_TRAILING_COMMITTED_KINDS = frozenset(
    {"canonical_message", "compaction_boundary", "control_event"}
)


class _StaticTranscriptStore:
    """Duck-typed transcript store exposing only ``read_committed()``.

    ``SessionContinuityBoundary.import_committed_transcript`` calls *only*
    ``transcript_store.read_committed()``, so a tiny adapter over our
    reconstructed entries is sufficient — no on-disk transcript file needed.

    It replicates the real ``runtime.transcript.TranscriptStore.read_committed``
    semantics: the prefix up to and including the LAST ``turn_committed`` /
    ``turn_aborted`` entry, plus any trailing canonical_message /
    compaction_boundary / control_event. A trailing in-flight turn (no
    ``turn_end``) is dropped, so the CLI rehydrates exactly what the production
    transcript-file path would — not an uncommitted tail.
    """

    def __init__(self, entries: list[object]) -> None:
        self._entries = entries

    def read_committed(self) -> list[object]:
        entries = self._entries
        last_complete = -1
        for index in range(len(entries) - 1, -1, -1):
            if getattr(entries[index], "kind", None) in _COMMITTED_KINDS:
                last_complete = index
                break
        if last_complete < 0:
            return []
        end = last_complete + 1
        for index in range(last_complete + 1, len(entries)):
            if getattr(entries[index], "kind", None) in _TRAILING_COMMITTED_KINDS:
                end = index + 1
                continue
            break
        return list(entries[:end])


async def _rehydrate(
    session_id: str,
    chain: list[Envelope],
    *,
    max_imported_events: int,
) -> tuple[object | None, object | None, object | None, str | None]:
    """Best-effort ADK rehydration. Returns (service, session, result, reason).

    Mirrors the production caller (``runner_session_boundary`` ~L748): create an
    ADK session, then call ``import_committed_transcript`` with an enabled
    ``SessionContinuityConfig``. Never raises — on any failure returns
    ``(None, None, None, <reason>)``.
    """

    try:
        deps = _lazy_resume_deps()
    except Exception as exc:  # noqa: BLE001 - ADK deps unavailable
        return None, None, None, f"rehydration_skipped: {exc}"

    entries, dropped, first_error = _envelopes_to_transcript_entries(chain, deps)
    store = _StaticTranscriptStore(entries)
    if not store.read_committed():
        # Nothing committed. Distinguish "genuinely empty" from "the forward map
        # systematically failed to build entries" (a kwarg/Literal break) so a
        # dead rehydration path surfaces instead of masquerading as empty.
        if dropped:
            return None, None, None, (
                f"rehydration_partial: all {dropped} mapped entr"
                f"{'y' if dropped == 1 else 'ies'} dropped ({first_error})"
            )
        return None, None, None, "no_committed_history"

    partial_reason = (
        f"rehydration_partial: {dropped} entr"
        f"{'y' if dropped == 1 else 'ies'} dropped ({first_error})"
        if dropped
        else None
    )

    try:
        service = deps["WorkspaceSessionService"](app_name="openmagi-cli")  # type: ignore[operator]
        session = await service.create_session(  # type: ignore[attr-defined]
            app_name="openmagi-cli",
            user_id="cli",
            session_id=session_id,
        )
        boundary = deps["SessionContinuityBoundary"]()  # type: ignore[operator]
        config = deps["SessionContinuityConfig"](  # type: ignore[operator]
            enabled=True,
            maxImportedEvents=max_imported_events,
        )
        result = await boundary.import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=config,
        )
        return service, session, result, partial_reason
    except Exception as exc:  # noqa: BLE001 - rehydration is best-effort
        return None, None, None, f"rehydration_failed: {exc}"


async def resume_async(
    session_id: str,
    *,
    bot_id: str = "",
    cwd: str | os.PathLike[str] | None = None,
    max_imported_events: int = 128,
) -> ResumeContext:
    """Async core of :func:`resume` — await this from an async entrypoint.

    Pure step (always runs): resolve the path, ``load()`` it, collapse the DAG
    via :func:`reconstruct_linear_chain`, and build ``initial_messages`` via
    :func:`reconstruct_messages`.

    Best-effort rehydration step (lazy ADK): convert the linear envelopes to
    ``TranscriptEntry`` objects and feed them through
    ``SessionContinuityBoundary.import_committed_transcript`` exactly like the
    production caller. If ADK deps are unavailable or nothing committed is found,
    the returned ``ResumeContext`` still carries ``initial_messages`` and a
    ``reason`` — never raises just because rehydration could not run.

    A future async entrypoint should ``await resume_async(...)`` directly so
    rehydration actually runs; the sync :func:`resume` wrapper is for non-async
    callers only and CANNOT rehydrate from inside a running loop. NOTE (v1): the
    CLI entrypoint does NOT yet call this — resume wiring is a v1.1 follow-up
    (see the module-level "PR-B2: resume / continue" note above).

    An empty/nonexistent session yields an empty-but-valid ``ResumeContext``.
    """

    path = resolve_session_path(bot_id, session_id, cwd)
    envelopes = load(path)
    chain = reconstruct_linear_chain(envelopes)
    messages = reconstruct_messages(chain)

    if not chain:
        return ResumeContext(
            session_id=session_id,
            initial_messages=messages,
            reason="empty_session",
        )

    service, session, continuity_result, reason = await _rehydrate(
        session_id, chain, max_imported_events=max_imported_events
    )
    return ResumeContext(
        session_id=session_id,
        initial_messages=messages,
        session_service=service,
        session=session,
        continuity_result=continuity_result,
        reason=reason,
    )


def resume(
    session_id: str,
    *,
    bot_id: str = "",
    cwd: str | os.PathLike[str] | None = None,
    max_imported_events: int = 128,
) -> ResumeContext:
    """Sync convenience wrapper over :func:`resume_async` for non-async callers.

    Runs the async core via ``asyncio.run``. If called from WITHIN a running
    event loop (where ``asyncio.run`` is illegal), it degrades to the pure
    message-list path with ``reason="rehydration_skipped"`` rather than raising —
    async callers (Stream F) MUST use :func:`resume_async` to get rehydration.
    """

    coro = resume_async(
        session_id,
        bot_id=bot_id,
        cwd=cwd,
        max_imported_events=max_imported_events,
    )
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        # Called from inside a running loop: ``asyncio.run`` rejects BEFORE
        # awaiting, so close the orphaned coroutine to avoid a "never awaited"
        # warning, then rebuild the pure path synchronously (no rehydration) so
        # the wrapper still returns initial_messages.
        coro.close()
        path = resolve_session_path(bot_id, session_id, cwd)
        chain = reconstruct_linear_chain(load(path))
        messages = reconstruct_messages(chain)
        return ResumeContext(
            session_id=session_id,
            initial_messages=messages,
            reason="empty_session" if not chain else f"rehydration_skipped: {exc}",
        )


def continue_latest(
    bot_id: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> str | None:
    """Return the ``session_id`` of the most-recently-modified session log.

    Scans the project directory (the parent of any resolved session path for
    this ``bot_id``/``cwd``) for ``*.jsonl`` files, picks the newest by mtime,
    and strips the ``<bot_id>__`` prefix + ``.jsonl`` suffix. ``None`` when no
    session log exists.
    """

    project_dir = resolve_session_path(bot_id, "_", cwd).parent
    if not project_dir.is_dir():
        return None

    candidates = list(project_dir.glob("*.jsonl"))
    if not candidates:
        return None

    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    stem = newest.name[: -len(".jsonl")] if newest.name.endswith(".jsonl") else newest.stem
    # Filenames are written with the SANITIZED bot_id prefix, so strip the same.
    prefix = f"{_safe_token(bot_id)}__"
    if bot_id and stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem


@dataclass
class _PrepareTarget:
    """Resolved resume target, or an ``early`` context when none was requested."""

    session_id: str | None = None
    bot_id: str = ""
    cwd: str | os.PathLike[str] | None = None
    max_imported_events: int = 128
    early: ResumeContext | None = None


def _resolve_prepare_target(args: object) -> _PrepareTarget:
    """Duck-type ``args`` into a resume target (shared by sync + async)."""

    def _attr(name: str, default: object) -> object:
        return getattr(args, name, default)

    bot_id = _attr("bot_id", "") or ""
    if not isinstance(bot_id, str):
        bot_id = str(bot_id)
    cwd = _attr("cwd", None)
    max_imported_events = _attr("max_imported_events", 128)
    if not isinstance(max_imported_events, int) or isinstance(max_imported_events, bool):
        max_imported_events = 128

    explicit = _attr("resume", None)
    session_id: str | None = explicit if isinstance(explicit, str) and explicit else None

    if session_id is None and _attr("continue_", False):
        session_id = continue_latest(bot_id, cwd=cwd)
        if session_id is None:
            return _PrepareTarget(
                early=ResumeContext(session_id="", reason="no_session_to_continue")
            )

    if session_id is None:
        return _PrepareTarget(
            early=ResumeContext(session_id="", reason="no_session_requested")
        )

    return _PrepareTarget(
        session_id=session_id,
        bot_id=bot_id,
        cwd=cwd,
        max_imported_events=max_imported_events,
    )


_PREPARE_RESUME_DOC = """Thin CLI-facing helper the Stream F entrypoint calls.

``args`` is any attribute-bearing object (an ``argparse.Namespace``, a
dataclass, or a plain object) — duck-typed, NOT an argparse type. Recognized
attributes (all optional):

- ``resume``: a session id to resume directly.
- ``continue_``: truthy -> resolve the latest session for ``bot_id``/``cwd``
  via :func:`continue_latest`, then resume it.
- ``bot_id``: bot scope for path resolution (default ``""``).
- ``cwd``: working directory for project scoping (default: process cwd).
- ``max_imported_events``: forwarded to resume (default 128).

Precedence: an explicit ``resume`` session id wins; otherwise ``continue_``
resolves the latest. When neither yields a session id, an empty-but-valid
``ResumeContext`` is returned (``reason="no_session_requested"`` /
``"no_session_to_continue"``)."""


def prepare_resume(args: object) -> ResumeContext:
    target = _resolve_prepare_target(args)
    if target.early is not None:
        return target.early
    assert target.session_id is not None
    return resume(
        target.session_id,
        bot_id=target.bot_id,
        cwd=target.cwd,
        max_imported_events=target.max_imported_events,
    )


async def prepare_resume_async(args: object) -> ResumeContext:
    """Async sibling of :func:`prepare_resume` — Stream F's async entrypoint
    awaits this so rehydration runs (the sync wrapper cannot rehydrate from
    within a running loop)."""

    target = _resolve_prepare_target(args)
    if target.early is not None:
        return target.early
    assert target.session_id is not None
    return await resume_async(
        target.session_id,
        bot_id=target.bot_id,
        cwd=target.cwd,
        max_imported_events=target.max_imported_events,
    )


prepare_resume.__doc__ = _PREPARE_RESUME_DOC
