"""Headless entrypoint for the Magi CLI (PR-A1).

``run_headless`` drives a single turn through an :class:`EngineDriver` and emits
protocol frames in one of three output modes (``text`` / ``json`` /
``stream-json``). The CLI is **default-ON** (Track 18 Stream F PR-F2a); set
``MAGI_CLI_ENABLED=0`` (or ``false`` / ``no`` / ``off``) to disable it.
When disabled, ``run_headless`` refuses to run and returns exit code 2 without
writing any protocol frames to stdout.

A1 ships a STUB engine driver (the real ADK-backed driver lands in A2). The stub
yields a couple of ``RuntimeEvent``s then yields a terminal ``EngineResult`` as
its final item, per the consumption convention documented in ``contracts``.

Stdout discipline: in ``stream-json`` mode all frames go through ONE
:class:`NdjsonWriter` (single queue + single drainer => FIFO + per-line flush).
In ``text`` / ``json`` modes a single final write is made. Logs/errors go to
stderr only. The ordering invariant — a ``control_request`` frame is never
written before the assistant frame/event that motivated it — is preserved by the
single-writer queue.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import sys
import threading
import uuid as _uuid
from collections.abc import AsyncGenerator
from typing import IO, Literal

from magi_agent.cli.contracts import (
    CommandContext,
    CommandRegistry,
    CommandSurface,
    Compact,
    ContentBlock,
    EngineDriver,
    EngineResult,
    NullPermissionGate,
    PermissionGate,
    PromptSink,
    RuntimeEvent,
    Skip,
    Terminal,
    Text,
)
from magi_agent.cli.ndjson import NdjsonWriter, ndjson_dumps
from magi_agent.cli.protocol import (
    AssistantFrame,
    ResultFrame,
    StreamEvent,
    SystemInit,
    SystemStatus,
    UserFrame,
)

# A2 wires the real ADK-backed MagiEngineDriver as the DEFAULT driver. It reuses
# magi_agent.transport.sse._sanitize_agent_event internally to redact
# real engine events before projecting them to protocol frames. MagiEngineDriver
# is import-clean (it lazy-imports ADK only when a turn is actually iterated), so
# importing it at module top here does NOT pull google-adk / textual into the
# headless import graph.
from magi_agent.cli.engine import MagiEngineDriver

_FALSY = {"0", "false", "no", "off"}


def _cli_enabled() -> bool:
    """Return True unless MAGI_CLI_ENABLED is explicitly set to a falsy token.

    Default-ON (Track 18 Stream F PR-F2a): unset or any non-falsy value → enabled.
    Set ``MAGI_CLI_ENABLED=0`` (or ``false`` / ``no`` / ``off``) to disable.
    """
    val = os.environ.get("MAGI_CLI_ENABLED")
    if val is None:
        return True  # default-ON (Track 18 Stream F: Kevin's decision)
    return val.strip().lower() not in _FALSY


def _log(message: str) -> None:
    """Write a diagnostic line to stderr (never stdout)."""

    print(message, file=sys.stderr, flush=True)


async def drain(
    gen: AsyncGenerator[RuntimeEvent, EngineResult],
) -> tuple[list[RuntimeEvent], EngineResult]:
    """Consume an engine driver generator per the terminal-result convention.

    The driver yields ``RuntimeEvent`` objects and, as its FINAL yielded item,
    one ``EngineResult`` (an ``async def`` generator cannot ``return`` a value).
    This helper collects the events and returns the terminal result. If the
    generator completes without ever yielding an ``EngineResult``, an error
    terminal is synthesized so callers always get a result.
    """

    events: list[RuntimeEvent] = []
    terminal: EngineResult | None = None
    try:
        async for item in gen:
            if isinstance(item, EngineResult):
                terminal = item
                break
            events.append(item)
    finally:
        # Close the generator so a real (A2) driver holding resources after the
        # terminal yield is released. aclose() on an already-exhausted async gen
        # is a safe no-op.
        await gen.aclose()
    if terminal is None:
        terminal = EngineResult(
            terminal=Terminal.error,
            usage={},
            cost_usd=0.0,
            error="engine_driver_yielded_no_terminal_result",
        )
    return events, terminal


# ---------------------------------------------------------------------------
# A1 stub engine driver
# ---------------------------------------------------------------------------
class StubEngineDriver:
    """A deterministic, model-free driver used until A2 lands the real one."""

    def __init__(
        self,
        *,
        text: str = "Hello from the Magi stub engine.",
        terminal: Terminal = Terminal.completed,
        error: str | None = None,
    ) -> None:
        self._text = text
        self._terminal = terminal
        self._error = error

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # ``gate`` accepted to match the EngineDriver Protocol; the stub never
        # triggers a permission check, so it is ignored.
        _ = (runtime, turn_input, gate)
        turn_id = "stub-turn"
        yield RuntimeEvent(
            type="status",
            payload={"phase": "executing", "label": "stub turn started"},
            turn_id=turn_id,
        )
        if not cancel.is_set():
            yield RuntimeEvent(
                type="token",
                payload={"text": self._text},
                turn_id=turn_id,
            )
        # Terminal EngineResult delivered as the FINAL yielded item.
        yield EngineResult(  # type: ignore[misc]
            terminal=self._terminal,
            usage={"input_tokens": 8, "output_tokens": 12},
            cost_usd=0.0,
            error=self._error,
        )


def _result_subtype(terminal: Terminal, error: str | None) -> str:
    if error is None and terminal == Terminal.completed:
        return "success"
    if terminal == Terminal.max_turns:
        return "error_max_turns"
    return "error_during_execution"


def _is_error(terminal: Terminal, error: str | None) -> bool:
    return error is not None or terminal in {
        Terminal.error,
        Terminal.max_turns,
        Terminal.aborted,
    }


def _token_text(payload: dict) -> str:
    """Extract assistant text from a ``token`` RuntimeEvent payload.

    The real ADK engine emits ``text_delta`` events whose text lives under the
    ``delta`` key, while the A1 stub uses ``text``. Read both so the headless
    projection works for every driver.
    """

    for key in ("delta", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _accumulate_text(events: list[RuntimeEvent]) -> str:
    parts: list[str] = []
    for event in events:
        if event.type == "token":
            parts.append(_token_text(event.payload))
    return "".join(parts)


# ---------------------------------------------------------------------------
# stream-json projection helpers
# ---------------------------------------------------------------------------
def _inner_type(payload: dict) -> str:
    inner = payload.get("type")
    return inner if isinstance(inner, str) else ""


def _parent_tool_use_id(payload: dict) -> str | None:
    """Extract the parent tool-use id for ``parent_tool_use_id`` threading.

    Top-level tool calls carry no parent (-> None). A nested/sub-agent tool
    carries the spawning tool-use id under one of several documented key spellings.
    """

    for key in ("parentToolUseId", "parent_tool_use_id", "parentToolId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _redact_composio_value(value: object) -> object:
    from magi_agent.composio.redaction import redact_composio_value

    return redact_composio_value(value)


def _redact_composio_text(value: str) -> str:
    redacted = _redact_composio_value(value)
    return redacted if isinstance(redacted, str) else value


def _tool_input(payload: dict) -> object:
    """Best-effort tool input for a tool_use block (preview/input/arguments)."""

    for key in ("input", "arguments", "input_preview", "inputPreview"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                # An input_preview may be a JSON string; surface the parsed form
                # when possible, else the raw string.
                try:
                    return _redact_composio_value(_json.loads(value))
                except (ValueError, TypeError):
                    return _redact_composio_value(value)
            return _redact_composio_value(value)
    return {}


def _assistant_text_frame(text: str, *, session_id: str) -> AssistantFrame:
    clean_text = _redact_composio_text(text)
    return AssistantFrame(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        message={"role": "assistant", "content": clean_text},
    )


def _assistant_tool_use_frame(
    payload: dict, *, session_id: str
) -> AssistantFrame:
    tool_id = payload.get("id")
    block = {
        "type": "tool_use",
        "id": tool_id if isinstance(tool_id, str) else "",
        "name": payload.get("name") if isinstance(payload.get("name"), str) else "tool",
        "input": _tool_input(payload),
    }
    return AssistantFrame(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        message={"role": "assistant", "content": [block]},
        parent_tool_use_id=_parent_tool_use_id(payload),
    )


def _user_tool_result_frame(payload: dict, *, session_id: str) -> UserFrame:
    tool_id = payload.get("id")
    status = payload.get("status")
    output = payload.get("output_preview")
    if not isinstance(output, str):
        output = payload.get("outputPreview")
    clean_output = _redact_composio_value(output) if isinstance(output, str) else ""
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_id if isinstance(tool_id, str) else "",
        "content": clean_output if isinstance(clean_output, str) else "",
        "is_error": status in {"error", "blocked"} or bool(payload.get("interrupted")),
    }
    if isinstance(status, str):
        block["status"] = status
    return UserFrame(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        message={"role": "user", "content": [block]},
    )


def _system_status_frame(event: RuntimeEvent, *, session_id: str) -> SystemStatus:
    inner = _inner_type(event.payload)
    # Map a few known progress-ish inner types to ``task_progress``; everything
    # else is a coarse ``status``.
    if inner in {"tool_progress", "child_progress", "task_progress", "heartbeat"}:
        subtype = "task_progress"
    elif inner in {"child_started", "task_started"}:
        subtype = "task_started"
    else:
        subtype = "status"
    clean_payload = _redact_composio_value({"kind": event.type, **dict(event.payload)})
    return SystemStatus(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        subtype=subtype,  # type: ignore[arg-type]
        payload=clean_payload if isinstance(clean_payload, dict) else {},
    )


def _partial_event_payload(event: RuntimeEvent) -> dict[str, object]:
    payload = dict(event.payload)
    if event.type == "token":
        for key in ("delta", "text"):
            if key in payload:
                payload[key] = "[redacted]"
        return payload
    clean_payload = _redact_composio_value(payload)
    return clean_payload if isinstance(clean_payload, dict) else {}


# ---------------------------------------------------------------------------
# Slash-command dispatch (headless)
# ---------------------------------------------------------------------------
_HEADLESS_SURFACE = CommandSurface(tui=False, headless=True)


def _parse_slash(prompt: str) -> tuple[str, str]:
    """Split a ``/name args`` prompt into ``(name, args)``."""

    body = prompt[1:]  # strip leading "/"
    parts = body.split(None, 1)
    name = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return name, args


async def _dispatch_headless_command(
    prompt: str,
    *,
    commands: CommandRegistry,
    cwd: str,
) -> tuple[str, list[ContentBlock] | None, str | None]:
    """Dispatch a ``/command`` for the headless surface.

    Returns ``(kind, prompt_blocks, message)`` where ``kind`` is one of:

    - ``"prompt"``: ``prompt_blocks`` are the content blocks to feed the engine
      as the turn input (run a normal turn).
    - ``"local"``: the command ran locally; ``message`` is a human line to emit
      (no engine turn).
    - ``"error"``: unknown command / interactive-only widget reached headless;
      ``message`` is the error text (no engine turn).
    """

    from magi_agent.cli.commands import dispatch

    name, args = _parse_slash(prompt)
    command = commands.lookup(name)
    if command is None:
        return "error", None, f"unknown command: /{name}"

    ctx = CommandContext(cwd=cwd)
    try:
        result = await dispatch(
            commands, name, args, ctx, surface=_HEADLESS_SURFACE
        )
    except PermissionError as exc:
        # A WidgetCommand is interactive-only; dispatch raises in headless.
        return "error", None, str(exc)

    if isinstance(result, list):
        # PromptCommand -> content blocks fed to the engine as the turn input.
        return "prompt", result, None
    if isinstance(result, Text):
        return "local", None, result.text
    if isinstance(result, Compact):
        return "local", None, "[compact] context compaction requested"
    if isinstance(result, Skip):
        return "error", None, f"unknown command: /{name}"
    # Unexpected (e.g. a widget on_done result leaking through) -> treat as error.
    return "error", None, f"command /{name} produced no headless result"


# ---------------------------------------------------------------------------
# Inbound NDJSON reader (stream-json only)
# ---------------------------------------------------------------------------
def _route_inbound_line(
    line: str,
    *,
    sink: PromptSink | None,
    cancel: asyncio.Event,
) -> None:
    """Parse a single inbound NDJSON line and route a control frame.

    Runs ON THE EVENT LOOP (scheduled via ``call_soon_threadsafe`` from the
    reader daemon thread), so it may safely touch ``sink`` / ``cancel``.
    ``control_response`` -> ``sink.deliver(...)``; ``control_cancel_request`` ->
    set the cancel event. Malformed/non-dict lines are skipped silently.
    """

    from magi_agent.cli.protocol import ControlCancel, ControlResponse

    line = line.strip()
    if not line:
        return
    try:
        obj = _json.loads(line)
    except (ValueError, TypeError):
        return  # malformed inbound line -> skip (do not crash the run)
    if not isinstance(obj, dict):
        return
    frame_type = obj.get("type")
    if frame_type == "control_response":
        if sink is not None and hasattr(sink, "deliver"):
            try:
                sink.deliver(ControlResponse(**obj))
            except Exception:  # noqa: BLE001 - best-effort delivery
                pass
    elif frame_type == "control_cancel_request":
        try:
            _ = ControlCancel(**obj)
        except Exception:  # noqa: BLE001
            pass
        cancel.set()


class _InboundReader:
    """Drives the blocking inbound NDJSON read loop on a DAEMON thread.

    FIX 1 (global review): the previous implementation offloaded the blocking
    ``input_stream.readline`` to the default executor, whose NON-daemon threads
    survive task cancellation. A controller that keeps the stdin write-end open
    leaves ``readline`` blocked forever, and ``asyncio.run``'s executor join then
    gates interpreter exit (observed ~30s+). A daemon thread we own dies with the
    process and NEVER blocks exit: on teardown we set a stop flag, drop the loop
    reference (so no further callbacks are scheduled), and let the thread die.

    Each parsed frame is handed back to the event loop via
    ``loop.call_soon_threadsafe(_route_inbound_line, ...)`` so all sink/cancel
    mutation happens on the loop thread. EOF fail-closes any pending sink asks
    (safe deny) so the gate's race cannot hang forever.
    """

    def __init__(
        self,
        input_stream: IO[str],
        *,
        sink: PromptSink | None,
        cancel: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._input_stream = input_stream
        self._sink = sink
        self._cancel = cancel
        self._loop: asyncio.AbstractEventLoop | None = loop
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="magi-cli-inbound", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _schedule(self, fn, *args) -> None:
        loop = self._loop
        if loop is None or self._stop.is_set():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            # Loop is closed/closing during teardown -> nothing to deliver.
            pass

    def _close_sink(self) -> None:
        if self._sink is not None and hasattr(self._sink, "close"):
            try:
                self._sink.close()
            except Exception:  # noqa: BLE001
                pass

    def _route(self, line: str) -> None:
        # Runs on the loop thread (scheduled via call_soon_threadsafe).
        _route_inbound_line(line, sink=self._sink, cancel=self._cancel)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                line = self._input_stream.readline()
            except Exception:  # noqa: BLE001 - stream torn down under us
                break
            if line == "":
                # EOF: no further answers will arrive. Fail-close any pending
                # sink asks (safe deny) on the loop thread.
                self._schedule(self._close_sink)
                return
            self._schedule(self._route, line)

    def stop(self) -> None:
        """Signal the daemon thread to stop and detach the loop reference.

        The thread may still be blocked in ``readline``; that is fine — it is a
        daemon and will not gate process exit. Dropping ``_loop`` guarantees no
        late callback is scheduled onto a loop that is being torn down.
        """

        self._stop.set()
        self._loop = None


async def _project_stream(
    gen: AsyncGenerator[RuntimeEvent, EngineResult],
    writer: NdjsonWriter,
    *,
    session_id: str,
    include_partial: bool,
) -> tuple[str, EngineResult]:
    """Consume the engine generator, emitting projected NDJSON frames live.

    Returns ``(accumulated_assistant_text, terminal)``. Token runs are coalesced
    into ONE assistant frame, flushed when a non-token event or the terminal
    arrives. Frames are written as events arrive so a ``control_request`` emitted
    by the gate's sink lands AFTER the tool frame that motivated it.
    """

    token_buf: list[str] = []
    all_text: list[str] = []
    terminal: EngineResult | None = None

    async def flush_tokens() -> None:
        if not token_buf:
            return
        text = "".join(token_buf)
        token_buf.clear()
        await writer.write(_assistant_text_frame(text, session_id=session_id))

    try:
        async for item in gen:
            if isinstance(item, EngineResult):
                terminal = item
                break
            event = item
            if include_partial:
                partial_payload = _partial_event_payload(event)
                await writer.write(
                    StreamEvent(
                        uuid=str(_uuid.uuid4()),
                        session_id=session_id,
                        event={
                            "type": event.type,
                            "payload": partial_payload,
                        },
                    )
                )
            if event.type == "token":
                text = _token_text(event.payload)
                if text:
                    token_buf.append(text)
                    all_text.append(text)
                continue
            # Non-token: flush the in-flight assistant text first (ordering).
            await flush_tokens()
            inner = _inner_type(event.payload)
            if event.type == "tool" and inner == "tool_start":
                await writer.write(
                    _assistant_tool_use_frame(event.payload, session_id=session_id)
                )
            elif event.type == "tool" and inner == "tool_end":
                await writer.write(
                    _user_tool_result_frame(event.payload, session_id=session_id)
                )
            else:
                # status / artifact / control / error / tool_progress -> status.
                await writer.write(
                    _system_status_frame(event, session_id=session_id)
                )
        # Stream ended; flush any trailing assistant text.
        await flush_tokens()
    finally:
        await gen.aclose()

    if terminal is None:
        terminal = EngineResult(
            terminal=Terminal.error,
            usage={},
            cost_usd=0.0,
            error="engine_driver_yielded_no_terminal_result",
        )
    return "".join(all_text), terminal


async def run_headless(
    prompt: str,
    *,
    output: Literal["text", "json", "stream-json"] = "text",
    include_partial: bool = False,
    gate: PermissionGate | None = None,
    sink: PromptSink | None = None,
    commands: CommandRegistry | None = None,
    driver: EngineDriver | None = None,
    session_id: str | None = None,
    stream: IO[str] | None = None,
    permission_mode: Literal[
        "default", "acceptEdits", "bypassPermissions", "smartApprove"
    ] = "default",
    model: str | None = None,
    input_stream: IO[str] | None = None,
    mcp_servers: list[str] | tuple[str, ...] | None = None,
) -> int:
    """Run a single headless turn. Returns a process exit code.

    ``stream`` is an injection point for tests (defaults to ``sys.stdout``).
    ``input_stream`` is an OPTIONAL inbound NDJSON reader (stream-json only): a
    background task parses ``control_response`` -> ``sink.deliver(...)`` and
    ``control_cancel_request`` -> cancel. When ``None`` (the one-shot default) no
    blocking reader is started.
    """

    if not _cli_enabled():
        _log("MAGI_CLI_ENABLED is set to a falsy value; refusing to run headless CLI.")
        return 2

    out: IO[str] = stream if stream is not None else sys.stdout
    sid = session_id or str(_uuid.uuid4())
    cwd = os.getcwd()
    active_gate = gate if gate is not None else NullPermissionGate()
    active_driver = driver if driver is not None else MagiEngineDriver()
    cancel = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Slash-command dispatch (before the engine turn).                     #
    # ------------------------------------------------------------------ #
    turn_input: dict[str, object] = {"prompt": prompt}
    local_message: str | None = None
    local_is_error = False
    if prompt.startswith("/") and commands is not None:
        kind, blocks, message = await _dispatch_headless_command(
            prompt, commands=commands, cwd=cwd
        )
        if kind == "prompt":
            # Feed the expanded content blocks as the turn input. We thread BOTH
            # the structured blocks (for a richer driver) AND a flattened text
            # ``prompt`` built from the text blocks (so the real ADK driver — which
            # reads only ``prompt`` — sends the EXPANDED command, not the raw
            # ``/name args`` string).
            expanded = "".join(
                b.text for b in (blocks or []) if getattr(b, "text", None)
            )
            turn_input = {
                "prompt": expanded or prompt,
                "content": blocks,
            }
        elif kind == "local":
            local_message = message
        else:  # error
            local_message = message
            local_is_error = True

    # ------------------------------------------------------------------ #
    # Local / error commands short-circuit: NO engine turn.                #
    # ------------------------------------------------------------------ #
    if local_message is not None:
        return await _emit_local_only(
            out,
            output=output,
            session_id=sid,
            model=model,
            mcp_servers=mcp_servers,
            message=local_message,
            is_error=local_is_error,
        )

    # ------------------------------------------------------------------ #
    # text / json: collect-then-write (single final write).                #
    # ------------------------------------------------------------------ #
    if output in ("text", "json"):
        gen = active_driver.run_turn_stream(
            None, turn_input, cancel=cancel, gate=active_gate
        )
        events, terminal = await drain(gen)
        assistant_text = _accumulate_text(events)
        result_frame = _build_result_frame(
            session_id=sid, assistant_text=assistant_text, terminal=terminal
        )
        if output == "text":
            out.write(_text_mode_body(result_frame) + "\n")
        else:
            out.write(ndjson_dumps(result_frame) + "\n")
        out.flush()
        return 1 if result_frame.is_error else 0

    # ------------------------------------------------------------------ #
    # stream-json: live single-writer NDJSON + sink/inbound wiring.        #
    # ------------------------------------------------------------------ #
    writer = NdjsonWriter(out)
    # Wire a HeadlessSink onto the gate when the gate exposes an (empty) sinks
    # list and the caller did not supply its own sink. This makes the gate's
    # ``ask`` path emit a real ``control_request`` frame through the writer.
    #
    # We attach the sink only when its ``ask`` can actually be resolved:
    #   - an ``input_stream`` is present (a host will answer the control_request,
    #     and on EOF the sink fail-closes — no hang); OR
    #   - the mode resolves without inbound data (``bypassPermissions`` allows,
    #     ``acceptEdits`` auto-allows edit-class tools).
    # In ``default`` mode with NO inbound channel, attaching a sink would let an
    # ``ask`` await a response that can never arrive, so we leave the gate
    # sink-less and it falls back to a safe deny (never an auto-allow).
    headless_sink: PromptSink | None = sink
    gate_sinks = getattr(active_gate, "sinks", None)
    can_resolve_ask = input_stream is not None or permission_mode in (
        "bypassPermissions",
        "acceptEdits",
    )
    if (
        sink is None
        and can_resolve_ask
        and isinstance(gate_sinks, list)
        and not gate_sinks
    ):
        from magi_agent.cli.permissions import HeadlessSink

        headless_sink = HeadlessSink(writer, permission_mode=permission_mode)
        gate_sinks.append(headless_sink)

    reader: _InboundReader | None = None
    try:
        await writer.write(
            SystemInit(
                uuid=str(_uuid.uuid4()),
                session_id=sid,
                tools=[],
                model=model or "magi",
                mcp_servers=list(mcp_servers or []),
                cwd=cwd,
            )
        )
        # Start the inbound reader ONLY when an input stream is provided. A
        # one-shot run (input_stream is None) never blocks on inbound data. The
        # reader runs on a DAEMON thread (FIX 1) so a still-open/blocking inbound
        # pipe can NEVER gate process exit.
        if input_stream is not None:
            reader = _InboundReader(
                input_stream,
                sink=headless_sink,
                cancel=cancel,
                loop=asyncio.get_running_loop(),
            )
            reader.start()
        gen = active_driver.run_turn_stream(
            None, turn_input, cancel=cancel, gate=active_gate
        )
        assistant_text, terminal = await _project_stream(
            gen, writer, session_id=sid, include_partial=include_partial
        )
        result_frame = _build_result_frame(
            session_id=sid, assistant_text=assistant_text, terminal=terminal
        )
        await writer.write(result_frame)
    finally:
        if reader is not None:
            # Signal stop + detach the loop. The daemon thread may still be
            # blocked in readline, but as a daemon it will NOT block exit.
            reader.stop()
        await writer.aclose()

    return 1 if result_frame.is_error else 0


def _text_mode_body(result_frame: ResultFrame) -> str:
    """Body for ``--output text``.

    On success this is the assistant text. On an error turn with no assistant
    text (e.g. a failed model call: bad key, retired model id, network), text
    mode would otherwise print a bare empty line and leave the user with no
    explanation. Surface the error plus an actionable configuration hint so the
    failure is visible on stdout instead of only stderr.
    """

    if result_frame.result:
        return result_frame.result
    if result_frame.is_error:
        detail = next((e for e in (result_frame.errors or []) if e), "unknown error")
        # Tailor the hint to the actual failure. A verification/evidence gate
        # block is NOT a model-connectivity problem, so don't send the user
        # chasing their API key — that hint only fits genuine model-call failures.
        if detail == "pre_final_evidence_gate_blocked":
            hint = (
                "The turn produced a reply but a verification gate blocked it "
                "because required evidence/validators were not observed (the "
                "dev-coding final gate). Provide the expected evidence, or run a "
                "non-coding turn / a permissive mode if a gate is not wanted."
            )
        else:
            hint = (
                "The model call did not return a reply. Check that your provider "
                "API key is valid and that the model id exists. Configure a "
                "provider via ~/.magi/config.toml or environment variables (e.g. "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY/GOOGLE_API_KEY, "
                "FIREWORKS_API_KEY), and optionally MAGI_PROVIDER / MAGI_MODEL."
            )
        return f"Error: {detail}\n{hint}"
    return ""


def _build_result_frame(
    *, session_id: str, assistant_text: str, terminal: EngineResult
) -> ResultFrame:
    subtype = _result_subtype(terminal.terminal, terminal.error)
    is_error = _is_error(terminal.terminal, terminal.error)
    clean_assistant_text = _redact_composio_text(assistant_text)
    clean_error = (
        _redact_composio_text(terminal.error) if terminal.error is not None else None
    )
    errors = [clean_error] if clean_error is not None else []
    return ResultFrame(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        subtype=subtype,  # type: ignore[arg-type]
        result=clean_assistant_text or None,
        usage=terminal.usage,
        total_cost_usd=terminal.cost_usd,
        is_error=is_error,
        errors=errors,
    )


async def _emit_local_only(
    out: IO[str],
    *,
    output: Literal["text", "json", "stream-json"],
    session_id: str,
    model: str | None,
    mcp_servers: list[str] | tuple[str, ...] | None,
    message: str,
    is_error: bool,
) -> int:
    """Emit the result of a LOCAL / error slash-command (no engine turn)."""

    if output == "text":
        out.write(message + "\n")
        out.flush()
        return 1 if is_error else 0

    subtype = "error_during_execution" if is_error else "success"
    result_frame = ResultFrame(
        uuid=str(_uuid.uuid4()),
        session_id=session_id,
        subtype=subtype,  # type: ignore[arg-type]
        result=None if is_error else message,
        usage={},
        total_cost_usd=0.0,
        is_error=is_error,
        errors=[message] if is_error else [],
    )
    if output == "json":
        out.write(ndjson_dumps(result_frame) + "\n")
        out.flush()
        return 1 if is_error else 0

    # stream-json: init -> a status frame carrying the local output -> result.
    writer = NdjsonWriter(out)
    try:
        await writer.write(
            SystemInit(
                uuid=str(_uuid.uuid4()),
                session_id=session_id,
                tools=[],
                model=model or "magi",
                mcp_servers=list(mcp_servers or []),
                cwd=os.getcwd(),
            )
        )
        await writer.write(
            SystemStatus(
                uuid=str(_uuid.uuid4()),
                session_id=session_id,
                subtype="status",
                payload={"kind": "command", "is_error": is_error, "message": message},
            )
        )
        await writer.write(result_frame)
    finally:
        await writer.aclose()
    return 1 if is_error else 0


__all__ = [
    "run_headless",
    "drain",
    "StubEngineDriver",
]
