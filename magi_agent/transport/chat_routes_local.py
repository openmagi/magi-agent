"""Local ADK chat SSE path + background-inject helpers, pure move out of
magi_agent/transport/chat_routes.py (PR-G4).

Bodies moved verbatim (source order preserved). chat_routes re-imports every
name so import paths are preserved. Depends downward on chat_shared only.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from fastapi.responses import StreamingResponse
from magi_agent.config.flags import flag_str
from magi_agent.missions.work_queue import inject_buffer as _inject_buffer
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.turn_context import TurnContext
from magi_agent.transport.chat_shared import _local_chat_string


_LOCAL_SERVE_PERMISSION_MODE = "bypassPermissions"


def _local_adk_chat_response(
    runtime: OpenMagiRuntime,
    payload: object,
) -> StreamingResponse:
    prompt = _local_chat_prompt_text(payload)
    return StreamingResponse(
        _local_adk_chat_sse(runtime, payload, prompt),
        media_type="text/event-stream",
    )


async def _local_adk_chat_sse(
    runtime: OpenMagiRuntime,
    payload: object,
    prompt: str,
) -> AsyncIterator[str]:
    from magi_agent.engine.contracts import EngineResult
    from magi_agent.engine.model_runner import (
        reset_per_turn_reasoning_effort,
        set_per_turn_reasoning_effort,
    )
    from magi_agent.cli.wiring import (
        build_headless_runtime,
        local_runner_policy_routing_enabled_from_env,
    )
    from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL
    from magi_agent.runtime.goal_loop_policy import (
        build_goal_loop_policy_from_request,
    )
    from magi_agent.runtime.per_turn_goal_loop_context import (
        reset_per_turn_goal_loop_policy,
        set_per_turn_goal_loop_policy,
    )
    from magi_agent.runtime.per_turn_goal_intensity import (
        reset_per_turn_goal_mission,
        set_per_turn_goal_mission,
    )
    from magi_agent.runtime.per_turn_agent_mode_context import (
        reset_per_turn_agent_mode,
        set_per_turn_agent_mode,
    )

    session_id = _local_chat_string(payload, "sessionId", "local-dashboard")
    turn_id = _local_chat_string(payload, "turnId", f"{session_id}:turn")
    # Background-task completion injection (default-OFF). When on and the
    # session has pending injections enqueued by a finished background task,
    # fold them onto the prompt so the next assistant turn surfaces the result.
    prompt = _apply_background_inject(session_id, prompt)
    yield _sse_data({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
    yield _sse_event(
        "agent",
        {
            "type": "turn_phase",
            "turnId": turn_id,
            "phase": "executing",
        },
    )
    yield _sse_event(
        "agent",
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running local ADK",
            "detail": "Local headless engine active",
        },
    )

    # The no-env local fallback injects ``LOCAL_DEV_MODEL_SENTINEL`` as the
    # required ``CORE_AGENT_MODEL``; treat it as "unset" so the headless runner
    # uses the per-provider default model instead of trying to call a
    # nonexistent ``<provider>/local-dev`` model.
    configured_model = runtime.config.model
    model_override = (
        None if configured_model == LOCAL_DEV_MODEL_SENTINEL else configured_model
    )
    # I-4 follow-up: workspace root flows through the registry typed reader.
    # ``flag_str`` returns ``""`` (the FlagSpec default) when the env is unset
    # or empty; the ``or os.getcwd()`` keeps the historical fallback semantics
    # byte-identical (non-empty env wins, empty/unset falls back to cwd).
    workspace_root = flag_str("MAGI_AGENT_WORKSPACE") or os.getcwd()
    # Per-turn query-based memory recall (PR-E item 3): pass the incoming user
    # message as the recall query so build_cli_instruction can search the
    # workspace memory tree and inject a <memory-recall> block. Gated + fail-soft
    # downstream (recall_enabled AND prefer_local_search, incognito-aware): when
    # off this is byte-identical (recall_query is just an unused string).
    #
    # TODO(PR-C offload): the recall search runs SYNCHRONOUSLY inside
    # build_headless_runtime → build_cli_instruction (prompt assembly), so it is
    # on this event loop. It already has an empty-tree guard
    # (memory_recall_block._has_indexable_memory) + a tiny corpus + a qmd
    # subprocess timeout, so it is cheap today. It is NOT offloaded here because
    # build_headless_runtime is a single sync call that assembles the whole
    # runtime (engine/gate/commands), not just recall — wrapping the lot in
    # to_thread would move unrelated wiring off-loop. If the memory tree ever
    # grows enough to matter, split the recall-block build out of prompt assembly
    # and to_thread JUST that, mirroring the record_turn offload below.
    # 01-PR4 (C2, issue 3): thread the REAL bot/owner identity into prompt
    # assembly so the gated-live learning recall/write ladder matches the
    # selected-canary digest against the genuine identity (the previous literal
    # "local" default could only ever target the literal "local" scope). The
    # readiness config itself is operator/control-plane-owned: locally there is
    # none, so it resolves to ``disabled`` and the serve prompt stays
    # byte-identical (default-OFF). The hosted prompt-assembly seam (08-hosted-
    # path) is where a real readiness config gets threaded in.
    learning_live_readiness = _resolve_local_learning_live_readiness(runtime)
    runtime_config = getattr(runtime, "config", None)
    serve_bot_id = str(getattr(runtime_config, "bot_id", None) or "local")
    serve_owner_user_id = str(getattr(runtime_config, "user_id", None) or "local")
    pinned_recipe_pack_ids = _pinned_recipe_pack_ids_from_payload(payload)
    # PR2c: extract per-turn ``reasoningEffort`` from the chat-completions
    # payload (wire protocol: ``"minimal" | "low" | "medium" | "high"``). Any
    # truthy string flows into the ContextVar below; ``None`` / unknown shape
    # leaves the override unset so the env path remains authoritative for this
    # turn (byte-identical to pre-PR2c behavior).
    _payload_reasoning_effort: str | None = None
    _payload_goal_mode_requested = False
    _payload_agent_mode: str | None = None
    if isinstance(payload, Mapping):
        _raw_reasoning = payload.get("reasoningEffort")
        if isinstance(_raw_reasoning, str):
            _payload_reasoning_effort = _raw_reasoning
        # PR-B (goal-loop wire): the composer's Goal-mission toggle (#835)
        # surfaces here as ``goalMode: true``. Phase 1 is opt-in, so absence /
        # falsy values must keep behavior byte-identical to today.
        _payload_goal_mode_requested = bool(payload.get("goalMode"))
        # PR-4c (agent-mode wire): the composer's mode toggle surfaces as
        # ``agentMode: "<mode-id>"``. A non-str / absent value leaves it None so
        # the stored sticky default (customize) stays authoritative for the turn.
        _raw_agent_mode = payload.get("agentMode")
        if isinstance(_raw_agent_mode, str) and _raw_agent_mode.strip():
            _payload_agent_mode = _raw_agent_mode.strip()
    # Scope the per-turn override across the LiteLlm build (inside
    # ``build_headless_runtime``) AND the entire streaming loop. Child/subagent
    # runners can spawn during the turn and call ``_build_litellm_model`` in
    # the same async task, so they must observe the same override; the
    # ``finally`` restores the prior ContextVar value so concurrent or
    # back-to-back turns never leak state.
    _reasoning_token = set_per_turn_reasoning_effort(_payload_reasoning_effort)
    # Build the goal-loop policy (PR-B). Returns ``None`` unless the per-send
    # toggle is on AND the master ``MAGI_GOAL_LOOP_ENABLED`` flag is truthy
    # AND the objective is non-empty — i.e. default-OFF is byte-identical to
    # today. The engine reads the policy via the ContextVar in PR-C; this PR
    # is wiring only (no engine behavior change).
    _goal_loop_policy = build_goal_loop_policy_from_request(
        goal_mode_requested=_payload_goal_mode_requested,
        objective=prompt,
        env=os.environ,
    )
    _goal_loop_token = set_per_turn_goal_loop_policy(_goal_loop_policy)
    # Auto-continue intensity: the composer Goal-mission toggle no longer gates
    # the loop on/off (that is the profile-aware MAGI_GOAL_LOOP_ENABLED flag);
    # it raises the ambient budget ceiling. Publish it on the per-turn intensity
    # ContextVar so SEAM 2 picks MISSION vs AMBIENT budgets. Reset in the finally.
    _goal_mission_token = set_per_turn_goal_mission(_payload_goal_mode_requested)
    # PR-4c: publish the per-send agent mode. An explicit request mode wins over
    # the operator's stored sticky default (customize.active_agent_mode); absence
    # (None) keeps PR-4b behavior. Reset in the finally below.
    _agent_mode_token = set_per_turn_agent_mode(_payload_agent_mode)
    # Out-of-band agent-event channel. SpawnAgent (and any other tool that
    # wants to surface child / subagent lifecycle into the dashboard Work
    # pane) pushes events here from inside the tool dispatch; the SSE
    # streaming loop below drains the deque BEFORE every engine event yield
    # so the dashboard sees `child_started` / `child_progress` /
    # `child_completed` in the right order alongside engine events.
    #
    # The pre-fix wire path never wired this on local serve — ToolContext's
    # ``emit_agent_event`` was always ``None``, so SpawnAgent's emit-helpers
    # no-op'd silently (the cause of Kevin's "subagents missing from right
    # panel" report at 0.1.66). The hosted gate5b path always set it; local
    # parity lands here.
    _pending_agent_events: collections.deque[dict[str, object]] = collections.deque()

    def _push_agent_event(event: Mapping[str, object]) -> None:
        # SpawnAgent's _emit_agent_event invokes us with a dict. Coerce
        # defensively (a future caller may pass a richer Mapping subtype)
        # and skip empties — never raise across the tool boundary.
        try:
            if not isinstance(event, Mapping):
                return
            _pending_agent_events.append(dict(event))
        except Exception:  # noqa: BLE001 — emitter must never crash a tool call.
            return

    # When the Goal-mission toggle is on AND the deployment opted in to
    # goal_loop, surface the active policy as an in-memory mission so the
    # dashboard's Missions panel has something to render WHILE the turn is
    # running. The PR-B/C goal-loop is ContextVar + judge-loop (no durable
    # mission persistence yet — see design doc Section 8 "Out of scope:
    # Mission/Run persistence"). This emits the bare minimum the frontend
    # needs to display a row.
    _goal_loop_mission_id: str | None = None
    _goal_loop_mission_status = "running"
    _goal_loop_mission_continuations = 0
    if _goal_loop_policy is not None:
        _goal_loop_mission_id = f"goal:{turn_id}"
        _goal_loop_mission_title = _goal_loop_policy.objective.strip()[:240] or "Goal mission"
    try:
        if _goal_loop_mission_id is not None and _goal_loop_policy is not None:
            yield _sse_event(
                "agent",
                {
                    "type": "mission_created",
                    "mission": {
                        "id": _goal_loop_mission_id,
                        "kind": "goal",
                        "title": _goal_loop_mission_title,
                        "status": _goal_loop_mission_status,
                        "createdAt": int(time.time() * 1000),
                        "metadata": {
                            "objective": _goal_loop_policy.objective,
                            "maxTurns": _goal_loop_policy.max_turns,
                            "turnId": turn_id,
                            "sessionId": session_id,
                        },
                    },
                },
            )
        # An active mode may TIGHTEN the turn's permission posture (e.g. a
        # "review" mode → default/smartApprove even on the YOLO serve baseline).
        # capped_permission_mode only ever raises restrictiveness above the
        # baseline, never loosens; hard-safety denies are unaffected regardless.
        from magi_agent.customize.modes import (  # noqa: PLC0415
            active_permission_mode as _active_permission_mode,
            capped_permission_mode as _capped_permission_mode,
        )

        _effective_permission_mode = _capped_permission_mode(
            _active_permission_mode(), _LOCAL_SERVE_PERMISSION_MODE
        )
        headless = build_headless_runtime(
            cwd=workspace_root,
            # A-8: explicit, audited local-serve YOLO opt-in (see module constant),
            # tightened by the active mode's permission_mode when set.
            permission_mode=_effective_permission_mode,
            session_id=session_id,
            model=model_override,
            runner_policy_routing_enabled=local_runner_policy_routing_enabled_from_env(),
            recall_query=prompt,
            bot_id=serve_bot_id,
            owner_user_id=serve_owner_user_id,
            learning_live_readiness=learning_live_readiness,
            pinned_recipe_pack_ids=pinned_recipe_pack_ids,
            agent_event_emitter=_push_agent_event,
        )
        # Route the top-level serve turn through the single ``run_governed_turn``
        # primitive (Phase 1). ``runtime=headless`` reuses the SAME runner/gate/
        # driver assembly built above — the primitive does not rebuild it — so
        # this is behavior-preserving. ``to_turn_input()`` adds
        # ``harness_state=ctx``; output neutrality holds because (a)
        # ``_extract_task_types`` treats any non-Mapping as ``()`` and (b) even
        # the ``effective_harness_state`` that runner-policy assembly computes
        # (adding ``resolvedHarnessStateType``) is passed only as
        # ``harnessState=`` to the ADK runner adapter, which drops it via its
        # kwargs allowlist — nothing reaches the model or any event.
        ctx = TurnContext(
            prompt=prompt,
            session_id=session_id,
            turn_id=turn_id,
            model=model_override,
            # A-8: keep the ctx authority consistent with the pre-built ``headless``
            # runtime above. Although ``runtime=headless`` means ``_build_runtime``
            # is not invoked here, set the field explicitly so the serve TurnContext
            # never silently relies on the deny/ask default — the serve policy choice
            # (mode-tightened) is visible on the context too.
            permission_mode=_effective_permission_mode,
        )
        stream = run_governed_turn(ctx, runtime=headless)
        # Accumulate the assistant text + a tool-use signal so the turn-end
        # memory hook (below) can flush a concise daily entry and skip trivial
        # turns. This mirrors data we already stream, so it adds no extra
        # engine work.
        assistant_parts: list[str] = []
        used_tool = False
        turn_errored = False
        async for item in stream:
            # Drain any agent-events SpawnAgent (or other tools) pushed during
            # the previous engine step, so the dashboard sees them in causal
            # order alongside the engine's own events.
            while _pending_agent_events:
                yield _sse_event("agent", _pending_agent_events.popleft())
            if isinstance(item, EngineResult):
                if item.error:
                    turn_errored = True
                    yield _sse_event(
                        "agent",
                        {
                            "type": "error",
                            "turnId": turn_id,
                            "reason": item.error,
                        },
                    )
                break
            event_payload = dict(item.payload)
            if event_payload.get("type") == "tool_start":
                used_tool = True
            yield _sse_event("agent", event_payload)
            # Mirror engine goal_loop_* status events as mission_event updates
            # so the Missions panel can render lifecycle progress for the
            # in-memory goal mission. Terminal events (complete / exhausted /
            # judge_unavailable) set the mission's final status.
            if _goal_loop_mission_id is not None:
                _gl_type = str(event_payload.get("type") or "")
                if _gl_type.startswith("goal_loop_"):
                    if _gl_type == "goal_loop_continuation":
                        _goal_loop_mission_continuations += 1
                        _mission_event_status = "running"
                    elif _gl_type == "goal_loop_complete":
                        _goal_loop_mission_status = "succeeded"
                        _mission_event_status = "succeeded"
                    elif _gl_type in (
                        "goal_loop_exhausted",
                        "goal_loop_judge_unavailable",
                    ):
                        _goal_loop_mission_status = "failed"
                        _mission_event_status = "failed"
                    else:
                        _mission_event_status = "running"
                    yield _sse_event(
                        "agent",
                        {
                            "type": "mission_event",
                            "missionId": _goal_loop_mission_id,
                            "eventType": _gl_type.removeprefix("goal_loop_"),
                            "status": _mission_event_status,
                            "continuations": _goal_loop_mission_continuations,
                            "max": _goal_loop_policy.max_turns
                            if _goal_loop_policy is not None
                            else None,
                            "reason": event_payload.get("reason")
                            or event_payload.get("judgeReason"),
                        },
                    )
            delta = _local_runtime_event_delta(event_payload)
            if delta:
                assistant_parts.append(delta)
                yield _sse_data({"choices": [{"index": 0, "delta": {"content": delta}}]})
        # Final drain: a child event emitted during the last engine step (or
        # after the EngineResult) would otherwise be dropped.
        while _pending_agent_events:
            yield _sse_event("agent", _pending_agent_events.popleft())
        # Close the in-memory goal mission. A goal_loop_* terminal event
        # already set the status; otherwise the turn finished without the
        # judge firing (the model produced satisfying text on the first
        # attempt) so we mark the mission succeeded. Turn errors mark it
        # failed so the Missions panel surfaces the actual outcome.
        if _goal_loop_mission_id is not None:
            if turn_errored and _goal_loop_mission_status == "running":
                _goal_loop_mission_status = "failed"
            elif _goal_loop_mission_status == "running":
                _goal_loop_mission_status = "succeeded"
            yield _sse_event(
                "agent",
                {
                    "type": "mission_updated",
                    "mission": {
                        "id": _goal_loop_mission_id,
                        "status": _goal_loop_mission_status,
                        "completedAt": int(time.time() * 1000),
                        "metadata": {
                            "continuations": _goal_loop_mission_continuations,
                            "turnId": turn_id,
                        },
                    },
                },
            )
    finally:
        reset_per_turn_reasoning_effort(_reasoning_token)
        reset_per_turn_goal_loop_policy(_goal_loop_token)
        reset_per_turn_goal_mission(_goal_mission_token)
        reset_per_turn_agent_mode(_agent_mode_token)
    # ── TURN-END MEMORY HOOK (PR-B) ─────────────────────────────────────────
    # This is the turn-finalization point of the live local chat path: the
    # engine stream has drained, so the assistant turn is complete. Flush a
    # concise turn entry to memory/daily/YYYY-MM-DD.md (the compaction tree's
    # raw input) and trigger a compaction build once per session. Both are GATED
    # (default-OFF master) and FAIL-SOFT — record_turn never raises, so a memory
    # error can never break the user's turn or the SSE stream. Errored turns are
    # skipped (nothing useful to persist). Real date injected at this call site.
    if not turn_errored:
        from magi_agent.runtime.memory_mode_context import (  # noqa: PLC0415
            current_memory_mode,
        )
        from magi_agent.runtime.memory_turn_hook import record_turn  # noqa: PLC0415

        # Thread the per-request memory mode so incognito / read_only actually
        # suppress the live daily flush. ``current_memory_mode()`` is NORMAL
        # unless the (default-OFF) memory-mode routing gate bound it from the
        # ``x-core-agent-memory-mode`` header; ``.value`` yields the string form
        # ``record_turn`` compares against ``_NON_WRITING_MODES``.
        #
        # HOT-PATH OFFLOAD (PR-C): ``record_turn`` is synchronous and its
        # first-turn ``_maybe_run_compaction`` can do ~300ms of file IO (a final
        # review measured it). Run it on a worker thread via ``asyncio.to_thread``
        # so the daily flush + compaction build never block this SSE event loop.
        # Still fail-soft (record_turn swallows its own errors) and gated (no-op
        # when memory is off); the await just keeps the loop responsive.
        #
        # CONCURRENCY: offloading makes genuine concurrent execution possible, and
        # compaction_tree.append_daily_entry is read-modify-write (atomic write,
        # but last-writer-wins if two same-workspace turns finalize concurrently →
        # a daily entry could be lost). Acceptable for the single-user local CLI;
        # a lock here would only be needed if concurrent same-workspace turns
        # become common.
        # PR2: no ``summarizer=`` is passed here. ``record_turn`` builds the
        # default production cheap-model summarizer itself, but ONLY when the
        # (default-OFF) ``compaction_enabled`` gate resolves True — so a clean
        # install constructs no model and this path stays byte-identical. The
        # model is built lazily on first ``summarize`` and fails open to
        # truncation if no provider/key is configured.
        await asyncio.to_thread(
            record_turn,
            workspace_root=workspace_root,
            session_id=session_id,
            turn_id=turn_id,
            user_text=prompt,
            assistant_text="".join(assistant_parts),
            used_tool=used_tool,
            memory_mode=current_memory_mode().value,
        )
        # Serve session-end extraction: buffer this turn so the app lifespan can
        # flush the whole transcript through the session-end extractor on
        # shutdown (the local serve path has no per-conversation end / shared
        # session service to enumerate). Gated no-op when the feature is OFF.
        try:
            from magi_agent.runtime.active_sessions import note_turn  # noqa: PLC0415

            note_turn(
                session_id=session_id,
                workspace_root=workspace_root,
                user_text=prompt,
                assistant_text="".join(assistant_parts),
                memory_mode=current_memory_mode().value,
            )
        except Exception:  # noqa: BLE001, S110 — best-effort; never break the turn
            pass
    #
    # NOTE: the Hermes-style background memory *review* (re-reading the transcript
    # to "save what the model forgot") is a SEPARATE mechanism that still needs a
    # live model-backed reviewer and MUST run OFF this hot path. It is intentionally
    # NOT wired here — see magi_agent/harness/memory_review.py. ──────────────────
    yield _sse_data({"choices": [{"index": 0, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"


def _local_runtime_event_delta(payload: Mapping[str, object]) -> str:
    for key in ("delta", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _buffer_injection(session_id: str, text: str) -> int:
    """Append *text* to *session_id*'s pending-injection buffer; return its size."""
    return _inject_buffer.enqueue(session_id, text)


_BACKGROUND_INJECT_CONSUMER_ENV = "MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED"


def _background_inject_consumer_enabled() -> bool:
    # I-2 PR A: was a denylist check; now uses the strict-allowlist
    # :func:`magi_agent.config._truthy.env_bool` so unknown values like
    # ``"disabled"`` correctly read as False.
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, _BACKGROUND_INJECT_CONSUMER_ENV, default=False)


def _format_background_inject_block(notes: list[str]) -> str:
    """Render drained background-task summaries as a system note block.

    Single rendering site so the prompt-fold is byte-identical wherever it's
    applied. Returns an empty string for no notes.
    """
    if not notes:
        return ""
    rendered = "\n\n".join(note.strip() for note in notes if note and note.strip())
    if not rendered:
        return ""
    return (
        "[background-task completions since your last turn]\n"
        f"{rendered}\n"
        "[end background-task completions]"
    )


def _apply_background_inject(session_id: str, prompt: str) -> str:
    """Drain *session_id*'s pending injections and prepend them to *prompt*.

    Off (default) -> byte-identical to ``prompt``. On with no pending
    injections -> also byte-identical (drain returns empty). On with pending
    injections -> formatted system note block prepended, buffer cleared.
    """
    if not _background_inject_consumer_enabled() or not session_id:
        return prompt
    pending = _inject_buffer.drain(session_id)
    block = _format_background_inject_block(pending)
    if not block:
        return prompt
    return f"{block}\n\n{prompt}" if prompt else block


class _NoopChatSink:
    """A do-nothing :class:`ActiveTurn.sink` stand-in for the gate5b path.

    The gate5b live-runner boundary has no headless permission sink (that is a
    cli/engine concept consumed by ``/v1/chat/control-response``). The interrupt
    route never touches ``turn.sink``, so a no-op placeholder satisfies the
    dataclass without dragging engine imports into this module.
    """

    def deliver(self, *_args: object, **_kwargs: object) -> None:  # pragma: no cover
        return None


def _sse_data(payload: Mapping[str, object]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _sse_event(name: str, payload: Mapping[str, object]) -> str:
    return f"event: {name}\n{_sse_data(payload)}"


def _local_chat_prompt_text(payload: object) -> str:
    """Extract the LATEST user-text content from ``payload["messages"]``.

    Lock-step with :func:`streaming_chat_route._extract_prompt_text` (queue
    masquerade 2nd-pass, PR-I, after #686). The dashboard sends the full
    OpenAI-compat conversation history each turn; prior turns already live in
    ADK session events, so the new-turn prompt must be only the latest user
    message. The legacy across-turn join let a long prior request (Kevin's
    Tesla 10-K repro) drown out a short fresh message ("hi") so the runtime
    kept executing the prior task instead of greeting back. Walk newest-first
    and return the first user-authored message's content.

    Within the single latest user message, multimodal text blocks still
    concatenate with newlines (per-turn multimodal contract is preserved).
    Missing role = user (bare ``{"content": ...}`` payload compat).
    """
    if not isinstance(payload, Mapping):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    for message in reversed(list(messages)):
        if not isinstance(message, Mapping):
            continue
        # Only user-authored text. Assistant/system text in a joined prompt
        # poisoned the coding-evidence-gate prompt classifier (see
        # streaming_chat_route._extract_prompt_text). Missing role = user.
        role = message.get("role")
        if role is not None and role != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
            continue
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            block_parts: list[str] = []
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        block_parts.append(text)
            joined = "\n".join(part.strip() for part in block_parts if part.strip())
            if joined:
                return joined
            continue
    return ""


def _resolve_local_learning_live_readiness(runtime: OpenMagiRuntime) -> object | None:
    """Return the operator/control-plane learning-live readiness config, or None.

    01-PR4 (C2): the gated-live learning recall/write serve seam consumes a
    readiness config the runtime/control-plane resolves (it owns the selected-
    canary digests + environment) — NOT any net-new env var (spec "no new
    flags"). Mirrors the optional ``getattr(runtime, "<canary>_config", None)``
    pattern used for the other gate route configs. When no config is bound (the
    default local case), this returns ``None`` so the serve prompt stays
    byte-identical (the live ladder resolves ``disabled``). Hosted prompt
    assembly (08-hosted-path) is where a real readiness config gets bound.
    """
    from magi_agent.gates.learning_live_readiness import (  # noqa: PLC0415
        LearningLiveReadinessConfig,
    )

    config = getattr(runtime, "learning_live_readiness_config", None)
    if isinstance(config, LearningLiveReadinessConfig):
        return config
    return None


def _pinned_recipe_pack_ids_from_payload(payload: object) -> tuple[str, ...]:
    """Read user-explicit recipe pin from the request payload.

    Reads ``pinnedRecipePackIds`` (camelCase) or ``pinned_recipe_pack_ids``
    (snake_case) from *payload* and returns a tuple of non-empty strings.
    Validation (registry lookup, hard-limit) is downstream in
    ``normalize_pinned_recipe_pack_ids``; this reader is a thin string filter.
    Returns ``()`` for any non-list value or absent key.
    """
    if not isinstance(payload, Mapping):
        return ()
    values = payload.get("pinnedRecipePackIds")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        values = payload.get("pinned_recipe_pack_ids")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    return tuple(v for v in values if isinstance(v, str) and v)
