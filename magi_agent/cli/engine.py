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

Genuine error recovery (PR12 honest retry seam)
-----------------------------------------------
This is THE live error-recovery seam. ``Runner.run_async`` owns the multi-step
model/tool loop; its ADK ``on_model_error_callback`` is a *substitute-the-
response* seam, NOT a *retry* seam — returning a content-less ``LlmResponse``
there ends the turn (ADK treats it as the final step) and no re-invocation
happens. So recovery is implemented HERE, around the run *invocation*: when the
ADK iteration raises a model error, :class:`MagiEngineDriver` classifies it via
the existing :class:`ErrorClassifier`, and for a retryable error (e.g. a 429)
applies backoff through the existing :class:`RecoveryEngine` (honoring
``Retry-After``) and then RE-INVOKES a fresh ``adapter.run_turn(...)`` — a
genuine second ``run_async`` (and therefore a real second model call).

Recovery is bounded by ``recovery_max_attempts`` and only fires BEFORE any agent
event has been streamed for the turn (so a mid-stream failure never replays
already-delivered output / duplicates tool effects). Terminal errors are not
retried (they propagate to a ``Terminal.error``); a prompt-too-long /
context-overflow error is NOT blind-retried here (it would just fail again) —
it is left to propagate (PR13 compaction territory). The whole wrapper is
flag-gated: with ``recovery=None`` (the default, and what the OFF env produces)
the streaming path is byte-for-byte identical to pre-PR12.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from magi_agent.cli.contracts import ControlRequest, EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from magi_agent.cli.contracts import PermissionGate
    from magi_agent.runtime.error_recovery import (
        RecoverableError,
        RecoveryAttemptState,
        RecoveryEngine,
    )


@dataclass(frozen=True)
class EngineRecoveryPolicy:
    """Live retry policy for the run invocation (PR12 genuine recovery seam).

    Holds the EXISTING :class:`RecoveryEngine` (activation, not reimpl) plus the
    per-turn attempt budget. Passed to :class:`MagiEngineDriver`; ``None`` (the
    default) disables the retry wrapper entirely so the OFF path is unchanged.
    """

    engine: "RecoveryEngine"
    max_attempts: int = 3


@dataclass(frozen=True, init=False)
class RunnerPolicyAssembly:
    """Local OSS runner policy assembled from first-party recipes.

    This object is intentionally public-metadata shaped. It can be threaded into
    harness state and emitted as a runtime event, but it cannot grant production
    write authority.
    """

    model_provider: str
    model_label: str
    selected_pack_ids: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    required_validators: tuple[str, ...]
    missing_evidence_action: str
    repair_policy: Mapping[str, object]
    attachment_flags: Mapping[str, bool]

    def __init__(
        self,
        *,
        modelProvider: str | None = None,
        model_provider: str | None = None,
        modelLabel: str | None = None,
        model_label: str | None = None,
        selectedPackIds: tuple[str, ...] | list[str] = (),
        selected_pack_ids: tuple[str, ...] | list[str] = (),
        evidenceRequirements: tuple[str, ...] | list[str] = (),
        evidence_requirements: tuple[str, ...] | list[str] = (),
        requiredValidators: tuple[str, ...] | list[str] = (),
        required_validators: tuple[str, ...] | list[str] = (),
        missingEvidenceAction: str | None = None,
        missing_evidence_action: str | None = None,
        repairPolicy: Mapping[str, object] | None = None,
        repair_policy: Mapping[str, object] | None = None,
        attachmentFlags: Mapping[str, bool] | None = None,
        attachment_flags: Mapping[str, bool] | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "model_provider",
            _non_empty_str(model_provider or modelProvider, "local"),
        )
        object.__setattr__(
            self,
            "model_label",
            _non_empty_str(model_label or modelLabel, "local-stub"),
        )
        object.__setattr__(
            self,
            "selected_pack_ids",
            _str_tuple(selected_pack_ids or selectedPackIds),
        )
        object.__setattr__(
            self,
            "evidence_requirements",
            _str_tuple(evidence_requirements or evidenceRequirements),
        )
        object.__setattr__(
            self,
            "required_validators",
            _str_tuple(required_validators or requiredValidators),
        )
        object.__setattr__(
            self,
            "missing_evidence_action",
            _non_empty_str(missing_evidence_action or missingEvidenceAction, "audit"),
        )
        object.__setattr__(
            self,
            "repair_policy",
            dict(repair_policy or repairPolicy or {}),
        )
        object.__setattr__(
            self,
            "attachment_flags",
            _authority_safe_attachment_flags(attachment_flags or attachmentFlags or {}),
        )

    def to_public_payload(self) -> dict[str, object]:
        return {
            "modelProvider": self.model_provider,
            "modelLabel": self.model_label,
            "selectedPackIds": list(self.selected_pack_ids),
            "evidenceRequirements": list(self.evidence_requirements),
            "requiredValidators": list(self.required_validators),
            "missingEvidenceAction": self.missing_evidence_action,
            "repairPolicy": dict(self.repair_policy),
            "attachmentFlags": dict(self.attachment_flags),
        }


def build_engine_recovery_policy(env: object = None) -> "EngineRecoveryPolicy | None":
    """Build the recovery policy from env, or ``None`` when recovery is OFF.

    Reuses ``MAGI_ERROR_RECOVERY_ENABLED`` / ``MAGI_MAX_RECOVERY_ATTEMPTS`` (the
    single source of truth in ``config.env``) and the existing default
    ``RecoveryEngine``. Imports are deferred so ``import cli.engine`` stays
    cold-clean (no error_recovery import at module top is required, but these
    are pure-python anyway).
    """

    import os

    from magi_agent.config.env import parse_error_recovery_env
    from magi_agent.runtime.error_recovery import ErrorRecoveryConfig, RecoveryEngine

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_error_recovery_env(mapping)
    if not parsed.enabled:
        return None
    config = ErrorRecoveryConfig(
        recovery_enabled=True,
        max_recovery_attempts=parsed.max_recovery_attempts,
    )
    return EngineRecoveryPolicy(
        engine=RecoveryEngine(config),
        max_attempts=parsed.max_recovery_attempts,
    )

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


_CODING_TASK_TYPES = frozenset(
    {
        "coding",
        "code",
        "dev-coding",
        "developer",
        "software",
        "workspace",
        "file-edit",
        "patch",
    }
)
_NON_CODING_TASK_TYPES = frozenset(
    {
        "chat",
        "general",
        "conversation",
        "research",
        "readonly",
        "read-only",
        "planning",
        "plan",
    }
)
_CODING_PROMPT_MARKERS = frozenset(
    {
        "apply_patch",
        "bash",
        "bug",
        "build",
        "code",
        "commit",
        "compile",
        "debug",
        "diff",
        "edit",
        "file",
        "fix",
        "grep",
        "implement",
        "lint",
        "patch",
        "pytest",
        "refactor",
        "repo",
        "script",
        "test",
        "typescript",
        "코드",
        "파일",
        "테스트",
        "수정",
        "고쳐",
        "구현",
        "패치",
        "버그",
        "리팩터",
        "커밋",
    }
)


def _pre_final_gate_applies(
    *,
    assembly: RunnerPolicyAssembly,
    prompt: str,
    harness_state: object | None,
) -> bool:
    """Return whether the assembled policy should enforce the final gate.

    The local runner may assemble the dev-coding pack as an available first-party
    policy, but availability is not the same thing as routing every turn through
    a coding verification gate.  Explicit task profiles win; otherwise a small
    conservative prompt classifier avoids blocking ordinary chat while still
    enforcing evidence on obvious coding/workspace turns.
    """

    selected = set(assembly.selected_pack_ids)
    if "openmagi.dev-coding" not in selected:
        return True

    task_types = _extract_task_types(harness_state)
    if task_types:
        normalized = {_normalize_task_type(item) for item in task_types}
        if normalized & _CODING_TASK_TYPES:
            return True
        if normalized & _NON_CODING_TASK_TYPES:
            return False

    normalized_prompt = prompt.lower()
    return any(marker in normalized_prompt for marker in _CODING_PROMPT_MARKERS)


def _extract_task_types(harness_state: object | None) -> tuple[str, ...]:
    if not isinstance(harness_state, Mapping):
        return ()
    profile = harness_state.get("taskProfile") or harness_state.get("task_profile")
    if not isinstance(profile, Mapping):
        return ()
    direct = profile.get("taskType") or profile.get("task_type")
    multi = profile.get("taskTypes") or profile.get("task_types")
    values: list[str] = []
    if isinstance(direct, str):
        values.append(direct)
    if isinstance(multi, str):
        values.append(multi)
    elif isinstance(multi, list | tuple):
        values.extend(item for item in multi if isinstance(item, str))
    return tuple(values)


def _normalize_task_type(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _lazy_engine_deps() -> dict[str, object]:
    """Import every heavy ADK symbol lazily.

    Called only when a turn is actually iterated; keeps the module import-clean.
    """

    from google.genai import types

    from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
    from magi_agent.adk_bridge.runner_adapter import (
        OpenMagiRunnerAdapter,
        RunnerTurnInput,
    )
    from magi_agent.transport.sse import _sanitize_agent_event

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

    from magi_agent.runtime.runner_session_boundary import (
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
        recovery: "EngineRecoveryPolicy | None" = None,
        runner_policy_assembly: RunnerPolicyAssembly | None = None,
    ) -> None:
        self._runner = runner
        self._max_event_count = max(1, int(max_event_count))
        self._user_id = user_id
        # Genuine error-recovery retry policy (PR12). ``None`` -> no retry
        # wrapper (the OFF path; byte-for-byte identical streaming). When set,
        # a classified-retryable model error raised by the run invocation is
        # backed-off and the run is RE-INVOKED (fresh run_async).
        self._recovery = recovery
        self._runner_policy_assembly = runner_policy_assembly
        # Shared across all turns of this driver instance: single-flight per
        # session id. Lazily built so construction stays cheap + import-clean.
        self._registry: object | None = None

    @property
    def runner(self) -> object | None:
        return self._runner

    @property
    def runner_policy_assembly(self) -> RunnerPolicyAssembly | None:
        return self._runner_policy_assembly

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
        effective_harness_state = self._with_runner_policy_harness_state(harness_state)

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
            harnessState=effective_harness_state,
        )

        # Tracks tool_use ids we emitted (tool_start) but have not yet seen a
        # matching tool_end for. Used to synthesize orphan tool_results on cancel.
        pending_tool_ids: dict[str, str] = {}
        event_count = 0
        usage: dict[str, object] = {}
        observed_public_refs: set[str] = set()

        policy_payload = self._runner_policy_payload()
        if policy_payload is not None:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "runner_policy_assembly",
                    "turnId": turn_id,
                    **policy_payload,
                },
                turn_id=turn_id,
            )

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

        cancelled = False
        engine_error: str | None = None
        # Number of agent RuntimeEvents actually yielded to the consumer across
        # ALL attempts. Recovery only re-invokes the run while this is 0, so a
        # mid-stream failure never replays already-delivered output.
        yielded_events = 0
        # Per-turn recovery attempt state (the existing RecoveryEngine threads
        # its per-strategy budget through this).
        recovery_state: "RecoveryAttemptState | None" = None
        recovery_attempts = 0

        try:
            while True:
                # (Re-)invoke the run: a FRESH ``adapter.run_turn`` is a fresh
                # ``Runner.run_async`` and therefore a real model call. On the
                # first iteration this is the original invocation; on a recovery
                # retry it is the genuine second invocation.
                adk_iter: AsyncIterator[object] = (
                    adapter.run_turn(runner_input).__aiter__()  # type: ignore[union-attr]
                )
                attempt_error: Exception | None = None
                attempt_yielded = 0
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
                            self._collect_public_refs(safe, observed_public_refs)
                            self._track_pending_tool(safe, pending_tool_ids)
                            attempt_yielded += 1
                            yielded_events += 1
                            yield RuntimeEvent(
                                type=_map_event_kind(safe.get("type")),
                                payload=safe,
                                turn_id=turn_id,
                            )

                        if event_count >= self._max_event_count:
                            break
                except Exception as exc:  # noqa: BLE001 - surface as terminal error
                    attempt_error = exc
                finally:
                    await self._aclose_iter(adk_iter)

                if cancelled:
                    break
                if attempt_error is None:
                    break

                # The run invocation raised. Decide whether to GENUINELY retry.
                # Only safe before any output was streamed (this turn AND this
                # attempt) so we never double-emit / duplicate tool effects.
                should_retry = (
                    self._recovery is not None
                    and yielded_events == 0
                    and attempt_yielded == 0
                    and recovery_attempts < self._recovery.max_attempts
                )
                if should_retry:
                    recovery_state, recovered = await self._attempt_run_recovery(
                        error=attempt_error,
                        session_id=session_id,
                        turn_id=turn_id,
                        state=recovery_state,
                    )
                    if recovered:
                        recovery_attempts += 1
                        continue  # re-invoke run_async (genuine 2nd model call)
                # Terminal / non-retryable / budget exhausted -> surface.
                engine_error = str(attempt_error) or attempt_error.__class__.__name__
                break
        finally:
            self._restore_gate_callback(gate_attach)

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

        pre_final_gate = self._pre_final_gate_payload(
            turn_id=turn_id,
            prompt=prompt,
            harness_state=effective_harness_state,
            observed_public_refs=observed_public_refs,
        )
        if pre_final_gate is not None:
            yield RuntimeEvent(type="status", payload=pre_final_gate, turn_id=turn_id)
            if pre_final_gate["decision"] == "block":
                yield EngineResult(  # type: ignore[misc]
                    terminal=Terminal.error,
                    usage=usage,
                    cost_usd=0.0,
                    error="pre_final_evidence_gate_blocked",
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

    async def _attempt_run_recovery(
        self,
        *,
        error: Exception,
        session_id: str,
        turn_id: str,
        state: "RecoveryAttemptState | None",
    ) -> "tuple[RecoveryAttemptState | None, bool]":
        """Classify a run-invocation error and apply backoff for a retryable one.

        Returns ``(updated_state, recovered)``. ``recovered=True`` means a
        strategy succeeded (e.g. RateLimit slept the Retry-After delay) and the
        caller should RE-INVOKE the run. ``recovered=False`` means the error is
        terminal, is prompt-too-long / context-overflow (NOT blind-retried —
        it would just fail again; PR13 compaction territory), or no strategy
        applied — so the caller surfaces it as a terminal error.

        This activates the EXISTING ``ErrorClassifier`` + ``RecoveryEngine``
        (not a reimplementation). The substitute-the-response
        ``on_model_error_callback`` seam in ``resilience_plugin`` is deliberately
        NOT used for retry (it cannot re-invoke the model); recovery lives here,
        at the genuine run-invocation boundary.
        """

        recovery = self._recovery
        if recovery is None:  # pragma: no cover - guarded by caller
            return state, False

        from magi_agent.runtime.error_recovery import (  # noqa: PLC0415
            ErrorClassifier,
            ErrorKind,
            RecoverableError,
        )

        classified = ErrorClassifier.classify(error)
        if not isinstance(classified, RecoverableError):
            return state, False  # terminal -> propagate
        if classified.kind == ErrorKind.PROMPT_TOO_LONG:
            # Re-issuing the identical (over-long) request would just fail again.
            # Do NOT blind-retry; leave it to propagate (PR13 compaction seam).
            return state, False

        result, new_state = await recovery.engine.attempt_recovery(
            error=classified,
            messages=[],
            session_key=session_id,
            turn_id=turn_id,
            state=state,
        )
        return new_state, bool(result.success)

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

    def _runner_policy_payload(self) -> dict[str, object] | None:
        if self._runner_policy_assembly is None:
            return None
        return self._runner_policy_assembly.to_public_payload()

    def _with_runner_policy_harness_state(self, harness_state: object | None) -> object | None:
        policy_payload = self._runner_policy_payload()
        if policy_payload is None:
            return harness_state
        if harness_state is None:
            return {"runnerPolicyAssembly": policy_payload}
        if isinstance(harness_state, Mapping):
            merged = dict(harness_state)
            merged.setdefault("runnerPolicyAssembly", policy_payload)
            return merged
        return {
            "resolvedHarnessStateType": harness_state.__class__.__name__,
            "runnerPolicyAssembly": policy_payload,
        }

    def _pre_final_gate_payload(
        self,
        *,
        turn_id: str,
        prompt: str,
        harness_state: object | None,
        observed_public_refs: set[str],
    ) -> dict[str, object] | None:
        assembly = self._runner_policy_assembly
        if assembly is None:
            return None
        if not _pre_final_gate_applies(
            assembly=assembly,
            prompt=prompt,
            harness_state=harness_state,
        ):
            return None
        missing_evidence = [
            ref for ref in assembly.evidence_requirements if ref not in observed_public_refs
        ]
        missing_validators = [
            ref for ref in assembly.required_validators if ref not in observed_public_refs
        ]
        decision = "block" if missing_evidence or missing_validators else "pass"
        return {
            "type": "pre_final_evidence_gate",
            "turnId": turn_id,
            "decision": decision,
            "matchedRefs": sorted(observed_public_refs),
            "missingEvidence": missing_evidence,
            "missingValidators": missing_validators,
            "missingEvidenceAction": assembly.missing_evidence_action,
            "repairPolicy": dict(assembly.repair_policy),
            "attachmentFlags": dict(assembly.attachment_flags),
        }

    @staticmethod
    def _collect_public_refs(value: object, refs: set[str]) -> None:
        if isinstance(value, str):
            if value.startswith(("evidence:", "verifier:", "receipt:sha256:", "sha256:")):
                refs.add(value)
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                MagiEngineDriver._collect_public_refs(nested, refs)
            return
        if isinstance(value, list | tuple):
            for nested in value:
                MagiEngineDriver._collect_public_refs(nested, refs)

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


def _non_empty_str(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _str_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if not isinstance(values, list | tuple):
        return ()
    return tuple(str(value) for value in values if str(value))


def _authority_safe_attachment_flags(flags: Mapping[str, bool]) -> dict[str, bool]:
    safe = {str(key): bool(value) for key, value in flags.items()}
    safe["productionWriteAllowed"] = False
    safe["userVisibleOutputAllowed"] = False
    return safe


class _suppress_cancel:
    """Context manager swallowing ``asyncio.CancelledError`` (and others)."""

    def __enter__(self) -> "_suppress_cancel":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (asyncio.CancelledError, Exception)
        )


__all__ = [
    "EngineRecoveryPolicy",
    "MagiEngineDriver",
    "RunnerPolicyAssembly",
    "build_engine_recovery_policy",
]
