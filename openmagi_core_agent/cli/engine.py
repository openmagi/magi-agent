"""Real ADK-backed engine driver for the Magi headless CLI (PR-A2).

``MagiEngineDriver`` implements the :class:`EngineDriver` Protocol from
``cli.contracts``. It drives a single turn through the ADK runner using the same
adapter + bridge wiring as
``runtime.runner_session_boundary._collect_runner_events`` (the reference
implementation), but YIELDS each projected public event incrementally as a
``RuntimeEvent`` instead of accumulating-then-returning. The terminal
``EngineResult`` is delivered as the FINAL yielded item, per the consumption
convention documented in ``cli.contracts``.

Import-cleanliness
------------------
This module MUST import without ``google-adk`` / ``google-genai`` / ``textual``
installed. Every heavy symbol (``google.genai.types``, ``OpenMagiRunnerAdapter``,
``RunnerTurnInput``, ``OpenMagiEventBridge``, ``_sanitize_agent_event``) is
imported lazily inside ``_lazy_engine_deps`` which is only called the first time
``run_turn_stream`` is actually iterated. Nothing at module top pulls ADK in.

Single-flight
-------------
A second concurrent turn for the same session id is rejected. We reuse the real
``ActiveTurnRegistry`` from ``runner_session_boundary`` (a thread-safe
session-key -> turn-id map). A per-driver default registry is shared across all
turns of a driver instance; on a concurrent turn we yield a terminal
``EngineResult(terminal=Terminal.aborted, error="active_session_turn")`` without
running the engine. The registry slot is always released in a ``finally`` (even
on cancel/exception).

Cancellation + orphan tool_result synthesis
-------------------------------------------
``cancel`` (an ``asyncio.Event``) is checked every iteration and the per-step
adapter pull is raced against ``cancel.wait()`` so a mid-step cancel is honored
promptly. As we stream we track tool-call ids (``tool_start``) and clear them on
the matching ``tool_end``. On cancel, for every still-pending (orphaned) tool
call we SYNTHESIZE and yield a ``tool`` ``RuntimeEvent`` representing an
interrupted ``tool_end`` (so the transcript stays balanced and the session can
resume), then emit an interruption status event and finally an aborted terminal.

Runner resolution
-----------------
``MagiEngineDriver(runner=...)`` accepts an explicit runner (tests always inject
a mock). When ``runner is None`` we resolve it from the ``runtime`` arg passed to
``run_turn_stream`` via ``getattr(runtime, "runner", runtime)`` — so a future
production caller (Stream F) can pass a wired runtime object. If no runner can be
resolved, the turn terminates with ``Terminal.error`` (``"no_runner"``) rather
than raising.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from openmagi_core_agent.cli.contracts import ControlRequest, EngineResult, Terminal
from openmagi_core_agent.runtime.events import RuntimeEvent

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from openmagi_core_agent.cli.contracts import PermissionGate

# A sane default cap so a runaway stream can't yield forever. Mirrors the spirit
# of RunnerSessionBoundaryConfig.max_event_count but headless can tolerate more.
_DEFAULT_MAX_EVENT_COUNT = 4096

# Map a projected public-event dict's "type" -> RuntimeEvent EventKind. Anything
# not listed defaults to "status".
_TOKEN_EVENT_TYPES = frozenset({"text_delta"})
_TOOL_EVENT_TYPES = frozenset({"tool_start", "tool_progress", "tool_end"})
_CONTROL_EVENT_TYPES = frozenset(
    {"control_event", "control_request", "control_replay_complete"}
)
_ARTIFACT_EVENT_TYPES = frozenset(
    {"source_inspected", "document_draft", "research_artifact_delta", "patch_preview"}
)
_ERROR_EVENT_TYPES = frozenset({"error"})


def _map_event_kind(event_type: object) -> str:
    if event_type in _TOKEN_EVENT_TYPES:
        return "token"
    if event_type in _TOOL_EVENT_TYPES:
        return "tool"
    if event_type in _CONTROL_EVENT_TYPES:
        return "control"
    if event_type in _ARTIFACT_EVENT_TYPES:
        return "artifact"
    if event_type in _ERROR_EVENT_TYPES:
        return "error"
    return "status"


def _lazy_engine_deps() -> dict[str, object]:
    """Import every heavy ADK symbol lazily.

    Called only when a turn is actually iterated; keeps the module import-clean.
    """

    from google.genai import types

    from openmagi_core_agent.adk_bridge.event_adapter import OpenMagiEventBridge
    from openmagi_core_agent.adk_bridge.runner_adapter import (
        OpenMagiRunnerAdapter,
        RunnerTurnInput,
    )
    from openmagi_core_agent.transport.sse import _sanitize_agent_event

    return {
        "types": types,
        "OpenMagiEventBridge": OpenMagiEventBridge,
        "OpenMagiRunnerAdapter": OpenMagiRunnerAdapter,
        "RunnerTurnInput": RunnerTurnInput,
        "sanitize_agent_event": _sanitize_agent_event,
    }


def _active_turn_registry():
    """Lazily build the real ActiveTurnRegistry (no ADK import needed).

    runner_session_boundary imports ADK at *function* scope only, so importing
    the module itself is import-clean — but we still defer it to keep engine.py's
    module-load dependency graph minimal.
    """

    from openmagi_core_agent.runtime.runner_session_boundary import (
        ActiveTurnRegistry,
    )

    return ActiveTurnRegistry()


class MagiEngineDriver:
    """ADK-backed :class:`EngineDriver` for the headless CLI.

    Parameters
    ----------
    runner:
        An ADK runner object exposing ``run_async(...)`` (what
        ``OpenMagiRunnerAdapter`` calls). If ``None`` it is resolved from the
        ``runtime`` argument of :meth:`run_turn_stream`.
    max_event_count:
        Upper bound on the number of ADK events consumed before the stream is
        force-completed.
    user_id:
        ``userId`` to stamp on the ``RunnerTurnInput`` (defaults to ``"cli"``).
    """

    def __init__(
        self,
        *,
        runner: object | None = None,
        max_event_count: int = _DEFAULT_MAX_EVENT_COUNT,
        user_id: str = "cli",
    ) -> None:
        self._runner = runner
        self._max_event_count = max(1, int(max_event_count))
        self._user_id = user_id
        # Shared across all turns of this driver instance: single-flight per
        # session id. Lazily built so construction stays cheap + import-clean.
        self._registry: object | None = None

    def _get_registry(self) -> object:
        if self._registry is None:
            self._registry = _active_turn_registry()
        return self._registry

    def _resolve_runner(self, runtime: object) -> object | None:
        if self._runner is not None:
            return self._runner
        if runtime is None:
            return None
        # A wired runtime may expose `.runner`; otherwise treat `runtime` itself
        # as the runner (DI-friendly: tests can pass a bare mock runner).
        return getattr(runtime, "runner", runtime)

    @staticmethod
    def _turn_identity(turn_input: object) -> tuple[str, str, str]:
        """Derive (session_id, turn_id, prompt) from the headless turn_input.

        ``run_headless`` passes ``{"prompt": prompt}``; production callers may
        pass a richer object (a ``TurnInput`` dataclass or any attribute-bearing
        object). We accept either a mapping or an attribute-bearing object and
        fall back to sane defaults.
        """

        def _get(key: str, default: str) -> str:
            if isinstance(turn_input, dict):
                value = turn_input.get(key, default)
            else:
                value = getattr(turn_input, key, default)
            return value if isinstance(value, str) and value else default

        session_id = _get("session_id", "cli-session")
        turn_id = _get("turn_id", "cli-turn")
        prompt = _get("prompt", "")
        if not prompt:
            prompt = _get("message_text", "")
        return session_id, turn_id, prompt

    @staticmethod
    def _turn_extra(turn_input: object) -> tuple[object | None, list]:
        """Read the additive ``harness_state`` / ``initial_messages`` seams.

        Works for BOTH a bare dict (``run_headless`` passes ``{"prompt": ...}``)
        and a ``TurnInput`` dataclass / attribute-bearing object. When the key is
        absent (the dict case today) ``harness_state`` is ``None`` and
        ``initial_messages`` is ``[]`` — identical to pre-A3 behavior.
        """

        def _attr(key: str, default: object) -> object:
            if isinstance(turn_input, dict):
                return turn_input.get(key, default)
            return getattr(turn_input, key, default)

        harness_state = _attr("harness_state", None)
        initial_messages = _attr("initial_messages", [])
        if not isinstance(initial_messages, list):
            initial_messages = []
        return harness_state, initial_messages

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # Stream F wires permission interception: ``gate`` (when not None) is
        # threaded into ``_drive``, which attaches an ADK ``before_tool_callback``
        # so the gate intercepts every tool BEFORE it executes. ``gate=None``
        # leaves behavior byte-for-byte identical to pre-F.
        session_id, turn_id, prompt = self._turn_identity(turn_input)
        harness_state, initial_messages = self._turn_extra(turn_input)

        registry = self._get_registry()
        acquired = registry.try_acquire(session_key=session_id, turn_id=turn_id)  # type: ignore[attr-defined]
        if not acquired:
            # A turn is already active for this session. Do NOT run.
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.aborted,
                usage={},
                cost_usd=0.0,
                error="active_session_turn",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        # async-for delegation does NOT propagate aclose()/GeneratorExit into the
        # sub-generator, so on an early/mid-stream consumer aclose() (interactive
        # cancel) `_drive`'s finally (which closes the ADK iterator) would be
        # deferred to GC. Hold the sub-generator and explicitly close it in a
        # finally so cleanup is prompt. The single-flight release is also in the
        # finally; it runs exactly once on every path (normal / cancel /
        # exception / early-aclose).
        driver_gen = self._drive(
            runtime=runtime,
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            harness_state=harness_state,
            initial_messages=initial_messages,
            cancel=cancel,
            gate=gate,
        )
        try:
            async for item in driver_gen:
                yield item  # RuntimeEvent OR the terminal EngineResult
        finally:
            # FIX 3 (global review): release() MUST run even if aclose() raises,
            # else the session's single-flight slot leaks and every future turn
            # for this session is rejected as ``active_session_turn``.
            try:
                await driver_gen.aclose()
            finally:
                registry.release(session_key=session_id, turn_id=turn_id)  # type: ignore[attr-defined]

    async def _drive(
        self,
        *,
        runtime: object,
        session_id: str,
        turn_id: str,
        prompt: str,
        harness_state: object | None = None,
        initial_messages: list | None = None,
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # PR3/Stream B: feed initial_messages via SessionContinuityBoundary.
        # Read here (so the seam is plumbed end-to-end) but NOT yet fed into the
        # runner — full rehydration lands with Stream B.
        _ = initial_messages

        runner = self._resolve_runner(runtime)
        if runner is None:
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage={},
                cost_usd=0.0,
                error="no_runner",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        try:
            deps = _lazy_engine_deps()
        except Exception as exc:  # pragma: no cover - import failure path
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage={},
                cost_usd=0.0,
                error=f"engine_import_failed: {exc}",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        types = deps["types"]
        adapter = deps["OpenMagiRunnerAdapter"](runner=runner)  # type: ignore[operator]
        bridge = deps["OpenMagiEventBridge"](live_compatible=True)  # type: ignore[operator]
        sanitize = deps["sanitize_agent_event"]
        runner_turn_input_cls = deps["RunnerTurnInput"]

        runner_input = runner_turn_input_cls(
            userId=self._user_id,
            sessionId=session_id,
            turnId=turn_id,
            invocationId=turn_id,
            newMessage=types.Content(  # type: ignore[attr-defined]
                role="user",
                parts=[types.Part(text=prompt)],  # type: ignore[attr-defined]
            ),
            # Threaded from the turn_input (TurnInput.harness_state / dict key).
            # A plain dict without the key leaves this None — identical to today.
            harnessState=harness_state,
        )

        # Tracks tool_use ids we emitted (tool_start) but have not yet seen a
        # matching tool_end for. Used to synthesize orphan tool_results on cancel.
        pending_tool_ids: dict[str, str] = {}
        event_count = 0
        usage: dict[str, object] = {}

        # Permission interception (Stream F): attach a before_tool_callback to
        # the runner's agent so the gate intercepts every tool BEFORE it runs.
        # The agent is per-RUNNER (not per-turn); two concurrent turns sharing
        # one runner but DIFFERENT gates would race on this attribute. The CLI
        # runs one turn at a time per session (the single-flight
        # ``ActiveTurnRegistry`` enforces this), so it is safe here — but a
        # shared-runner SERVER must NOT assume this. The original value is always
        # restored in the ``finally`` below, on every exit path.
        gate_attach = self._attach_gate_callback(
            runner=runner, gate=gate, turn_id=turn_id, cancel=cancel
        )

        adk_iter: AsyncIterator[object] = adapter.run_turn(runner_input).__aiter__()  # type: ignore[union-attr]
        cancelled = False
        engine_error: str | None = None

        try:
            while True:
                if cancel.is_set():
                    cancelled = True
                    break

                step = await self._next_adk_event(adk_iter, cancel)
                if step is _CANCELLED:
                    cancelled = True
                    break
                if step is _EXHAUSTED:
                    break

                adk_event = step
                event_count += 1
                projection = bridge.project_adk_event(adk_event, turn_id=turn_id)  # type: ignore[union-attr]
                for raw_event in projection.agent_events:  # type: ignore[union-attr]
                    safe = sanitize(dict(raw_event))  # type: ignore[operator]
                    if safe is None:
                        continue
                    self._track_pending_tool(safe, pending_tool_ids)
                    yield RuntimeEvent(
                        type=_map_event_kind(safe.get("type")),
                        payload=safe,
                        turn_id=turn_id,
                    )

                if event_count >= self._max_event_count:
                    break
        except Exception as exc:  # noqa: BLE001 - surface as terminal error
            engine_error = str(exc) or exc.__class__.__name__
        finally:
            self._restore_gate_callback(gate_attach)
            await self._aclose_iter(adk_iter)

        if cancelled:
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids, turn_id=turn_id
            ):
                yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "turn_end",
                    "turnId": turn_id,
                    "status": "aborted",
                    "reason": "user_interrupt",
                },
                turn_id=turn_id,
            )
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.aborted,
                usage=usage,
                cost_usd=0.0,
                error="cancelled",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        if engine_error is not None:
            # Balance the transcript on a mid-tool failure too: a runner error
            # while a tool_use is pending would otherwise leave a dangling
            # tool_use that a resuming session cannot reconcile (same hazard the
            # cancel path guards against).
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids, turn_id=turn_id
            ):
                yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage=usage,
                cost_usd=0.0,
                error=engine_error,
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        yield EngineResult(  # type: ignore[misc]
            terminal=Terminal.completed,
            usage=usage,
            cost_usd=0.0,
            error=None,
            session_id=session_id,
            turn_id=turn_id,
        )

    async def _next_adk_event(
        self,
        adk_iter: AsyncIterator[object],
        cancel: asyncio.Event,
    ) -> object:
        """Pull the next ADK event, racing it against ``cancel.wait()``.

        Returns the event, or the ``_EXHAUSTED`` / ``_CANCELLED`` sentinels.
        """

        next_task = asyncio.ensure_future(self._anext(adk_iter))
        cancel_task = asyncio.ensure_future(cancel.wait())
        try:
            done, _pending = await asyncio.wait(
                {next_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:  # pragma: no cover - propagate cleanup
            next_task.cancel()
            cancel_task.cancel()
            raise

        if next_task in done:
            cancel_task.cancel()
            with _suppress_cancel():
                await cancel_task
            result = next_task.result()
            return result

        # cancel fired first; abandon the in-flight pull.
        next_task.cancel()
        with _suppress_cancel():
            await next_task
        return _CANCELLED

    @staticmethod
    async def _anext(adk_iter: AsyncIterator[object]) -> object:
        try:
            return await adk_iter.__anext__()
        except StopAsyncIteration:
            return _EXHAUSTED

    @staticmethod
    async def _aclose_iter(adk_iter: AsyncIterator[object]) -> None:
        aclose = getattr(adk_iter, "aclose", None)
        if aclose is None:
            return
        with _suppress_cancel():
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    @staticmethod
    def _track_pending_tool(
        safe: dict[str, object],
        pending_tool_ids: dict[str, str],
    ) -> None:
        event_type = safe.get("type")
        tool_id = safe.get("id")
        if not isinstance(tool_id, str):
            return
        if event_type == "tool_start":
            pending_tool_ids[tool_id] = str(safe.get("name") or "tool")
        elif event_type == "tool_end":
            pending_tool_ids.pop(tool_id, None)

    @staticmethod
    def _synthesize_orphan_tool_results(
        pending_tool_ids: dict[str, str],
        *,
        turn_id: str,
    ) -> list[dict[str, object]]:
        """Build interrupted ``tool_end`` events for any unmatched tool calls.

        These keep the transcript balanced (every tool_use gets a tool_result)
        so a resumed session does not see a dangling tool call.
        """

        results: list[dict[str, object]] = []
        for tool_id in pending_tool_ids:
            results.append(
                {
                    "type": "tool_end",
                    "id": tool_id,
                    "status": "error",
                    "output_preview": "tool interrupted by user cancellation",
                    "durationMs": 0,
                    "interrupted": True,
                }
            )
        pending_tool_ids.clear()
        return results

    # -- Permission gate wiring (Stream F) ----------------------------------
    def _attach_gate_callback(
        self,
        *,
        runner: object,
        gate: "PermissionGate | None",
        turn_id: str,
        cancel: asyncio.Event,
    ) -> "_GateAttachment | None":
        """Attach a gate ``before_tool_callback`` to the runner's agent.

        Returns a restoration handle (or None when nothing was attached). When
        ``gate`` is None, or the runner exposes no ``agent``, this is a no-op and
        behavior is identical to today (keeps the agentless ``MockRunner`` tests
        green).

        Composes WITHOUT clobbering: the gate callback is prepended (FIRST) to
        any pre-existing ``before_tool_callback`` so a deny short-circuits before
        other callbacks run. ADK normalizes a single callable / a list / None via
        ``canonical_before_tool_callbacks``; we mirror that normalization.
        """
        if gate is None:
            return None
        agent = getattr(runner, "agent", None)
        if agent is None:
            return None

        original = getattr(agent, "before_tool_callback", None)
        if original is None:
            original_as_list: list = []
        elif isinstance(original, list):
            original_as_list = list(original)
        else:
            original_as_list = [original]

        callback = self._build_gate_before_tool(
            gate=gate, turn_id=turn_id, cancel=cancel
        )
        agent.before_tool_callback = [callback, *original_as_list]
        return _GateAttachment(agent=agent, original=original)

    @staticmethod
    def _restore_gate_callback(attachment: "_GateAttachment | None") -> None:
        if attachment is None:
            return
        try:
            attachment.agent.before_tool_callback = attachment.original
        except Exception:  # noqa: BLE001 - best-effort restore
            pass

    @staticmethod
    def _build_gate_before_tool(
        *,
        gate: "PermissionGate",
        turn_id: str,
        cancel: asyncio.Event,
    ):
        """Build the async ADK ``before_tool_callback`` enforcing ``gate``.

        ADK contract (verified against the installed
        ``google/adk/flows/llm_flows/functions.py``): the callback is invoked as
        ``callback(tool=..., args=<mutable dict>, tool_context=...)``. Returning a
        dict SKIPS the tool and uses the dict as the tool result (DENY). Returning
        None lets the tool run. Mutating ``args`` in place rewrites the tool input
        (UPDATED_INPUT). The callback may be async.
        """
        seq = 0

        def _deny_result(tool_name: str, feedback: str | None) -> dict[str, object]:
            result: dict[str, object] = {
                "status": "blocked",
                "error": "permission_denied",
                "tool": tool_name,
            }
            if feedback is not None:
                result["feedback"] = feedback
            return result

        async def _gate_before_tool(*, tool, args, tool_context=None):
            nonlocal seq
            _ = tool_context
            tool_name = getattr(tool, "name", "tool")
            seq += 1
            req = ControlRequest(
                requestId=f"{turn_id}:{tool_name}:{seq}",
                turnId=turn_id,
                toolName=tool_name,
                arguments=dict(args),
                reason="tool_use",
            )
            decision = await gate.check(req)

            if decision.kind == "deny":
                if decision.interrupt:
                    cancel.set()
                return _deny_result(tool_name, decision.feedback)

            # allow.
            updated = decision.updated_input
            if isinstance(updated, dict):
                # Re-validate the rewrite BEFORE applying it: a sink that rewrites
                # an allowed call into a forbidden one must NOT escalate past the
                # rules engine. (Closes the allow-then-rewrite-to-forbidden gap.)
                rules = getattr(gate, "rules", None)
                if rules is not None:
                    seq += 1
                    req2 = ControlRequest(
                        requestId=f"{turn_id}:{tool_name}:{seq}",
                        turnId=turn_id,
                        toolName=tool_name,
                        arguments=dict(updated),
                        reason="tool_use",
                    )
                    if rules.evaluate(req2) == "deny":
                        return _deny_result(tool_name, decision.feedback)
                # Apply the rewrite IN PLACE so the tool receives the new args.
                args.clear()
                args.update(updated)

            return None  # tool runs (with original or rewritten args)

        return _gate_before_tool


class _GateAttachment:
    """Restoration handle for a gate ``before_tool_callback`` attachment."""

    __slots__ = ("agent", "original")

    def __init__(self, *, agent: object, original: object) -> None:
        self.agent = agent
        self.original = original


class _Sentinel:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self._name}>"


_EXHAUSTED = _Sentinel("adk_stream_exhausted")
_CANCELLED = _Sentinel("adk_stream_cancelled")


class _suppress_cancel:
    """Context manager swallowing ``asyncio.CancelledError`` (and others)."""

    def __enter__(self) -> "_suppress_cancel":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (asyncio.CancelledError, Exception)
        )


__all__ = ["MagiEngineDriver"]
