from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.genai import types

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.runtime.turn_controller import TurnControllerInput


CONTEXT_FIXTURES = Path(__file__).parent / "fixtures" / "context_continuity"


def _turn_input(
    *,
    session_id: str = "agent:main:app:default",
    turn_id: str = "turn-1",
    message_text: str = "hello",
) -> TurnControllerInput:
    return TurnControllerInput(
        userId="user-1",
        sessionId=session_id,
        turnId=turn_id,
        messageText=message_text,
        harnessState=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=0,
        ),
    )


def _partial_event(text: str, *, invocation_id: str = "turn-1") -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        partial=True,
        invocation_id=invocation_id,
    )


def _final_event(text: str, *, invocation_id: str = "turn-1") -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        turn_complete=True,
        invocation_id=invocation_id,
    )


class _FakeRunner:
    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        error: BaseException | None = None,
        wait_until_cancelled: bool = False,
    ) -> None:
        self.events = events or []
        self.error = error
        self.wait_until_cancelled = wait_until_cancelled
        self.calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.cancelled = False

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
        self.started.set()
        try:
            if self.error is not None:
                raise self.error
            if self.wait_until_cancelled:
                while True:
                    await asyncio.sleep(0.01)
            for event in self.events:
                yield event
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _ContinuityObservingRunner(_FakeRunner):
    app_name = "openmagi"

    def __init__(self, *args: object, session_service: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.session_service = session_service
        self.imported_texts_at_call: list[str] = []

    async def run_async(self, **kwargs: object):
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=str(kwargs["user_id"]),
            session_id=str(kwargs["session_id"]),
        )
        assert session is not None
        for event in session.events:
            if event.content is None or not event.content.parts:
                continue
            text = event.content.parts[0].text
            if text:
                self.imported_texts_at_call.append(text)
        async for event in super().run_async(**kwargs):
            yield event


def _copy_context_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.write_text(
        (CONTEXT_FIXTURES / name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return target


def _runner_new_message_text(runner: _FakeRunner) -> str:
    assert runner.calls
    content = runner.calls[0]["new_message"]
    assert isinstance(content, types.Content)
    assert content.parts
    text = content.parts[0].text
    assert text is not None
    return text


def _assert_no_write_authority(result: object) -> None:
    denials = result.projection_write_denials
    assert {denial.target for denial in denials} == {
        "transcript",
        "sse",
        "control_event",
        "control_request",
    }
    for denial in denials:
        assert denial.allowed is False
        assert denial.durable_write_attempted is False
        assert denial.production_receipt_produced is False
        assert denial.receipt is None
        assert denial.denial.reason_code == "projection_writes_disabled"

    flags = result.authority_flags
    assert flags.user_visible_output_allowed is False
    assert flags.transcript_write_allowed is False
    assert flags.sse_write_allowed is False
    assert flags.control_event_write_allowed is False
    assert flags.control_request_write_allowed is False
    assert flags.production_receipt_allowed is False
    assert flags.tool_host_active is False
    assert flags.memory_provider_active is False
    assert flags.workspace_mutation_allowed is False
    assert flags.child_execution_allowed is False
    assert flags.mission_runtime_allowed is False
    assert flags.durable_write_allowed is False


def _assert_no_context_authority(result: object) -> None:
    context = result.context_continuity
    assert context.response_authority == "none"
    assert context.local_only is True
    assert context.diagnostic_only is True
    assert context.authority_flags.transcript_write_allowed is False
    assert context.authority_flags.sse_write_allowed is False
    assert context.authority_flags.db_write_allowed is False
    assert context.authority_flags.memory_write_allowed is False
    assert context.authority_flags.workspace_mutation_allowed is False
    assert context.authority_flags.child_execution_allowed is False
    assert context.authority_flags.channel_delivery_allowed is False


def test_disabled_default_does_not_invoke_runner_or_claim_authority() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
    )

    runner = _FakeRunner([_final_event("unused")])

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
        )
    )

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert result.runner_invoked is False
    assert result.runner_completed is False
    assert result.model_call_via_adk_runner_attempted is False
    assert result.local_public_events == []
    assert result.response_authority == "none"
    assert result.user_visible_output is None
    assert runner.calls == []
    _assert_no_write_authority(result)


def test_fake_runner_success_emits_local_public_events_and_turn_end_only() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    runner = _FakeRunner(
        [_partial_event("Hello "), _final_event("done")],
    )

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
        )
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.session_key == "agent:main:app:default"
    assert result.turn_id == "turn-1"
    assert result.runner_invoked is True
    assert result.runner_completed is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.event_count == 2
    assert result.local_public_events == [
        {"type": "text_delta", "delta": "Hello "},
        {
            "type": "turn_end",
            "turnId": "turn-1",
            "status": "aborted",
            "reason": "missing_runtime_receipt",
        },
    ]
    assert result.local_transcript_entry_count == 1
    assert result.terminal_metadata.status == "completed"
    assert result.terminal_metadata.error_category is None
    assert result.response_authority == "none"
    assert result.user_visible_output is None
    assert len(runner.calls) == 1
    assert set(runner.calls[0]) <= {
        "user_id",
        "session_id",
        "invocation_id",
        "new_message",
        "run_config",
    }
    assert {
        "user_id",
        "session_id",
        "invocation_id",
        "new_message",
    } <= set(runner.calls[0])
    assert runner.calls[0]["session_id"] == "agent:main:app:default"
    assert runner.calls[0]["invocation_id"] == "turn-1"
    assert "harness_state" not in runner.calls[0]
    assert "state_delta" not in runner.calls[0]
    if "run_config" in runner.calls[0]:
        assert isinstance(runner.calls[0]["run_config"], RunConfig)
        assert runner.calls[0]["run_config"].streaming_mode == StreamingMode.SSE
    _assert_no_write_authority(result)


def test_runner_context_continuity_imports_committed_history_before_runner_call(
    tmp_path: Path,
) -> None:
    from magi_agent.adk_bridge.session_service import WorkspaceSessionService
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )
    from magi_agent.runtime.transcript import TranscriptStore

    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(
        [_final_event("done")],
        session_service=session_service,
    )

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(
                turn_id="turn-followup",
                message_text="아까 말한 그거 어떻게 하면돼?",
            ),
            runner=runner,
            config=RunnerSessionBoundaryConfig(
                enabled=True,
                timeoutMs=500,
                contextContinuity={
                    "enabled": True,
                    "modelVisibleProjectionEnabled": True,
                    "maxImportedEvents": 8,
                },
            ),
            transcript_store=TranscriptStore(
                file_path=_copy_context_fixture(
                    tmp_path,
                    "ambiguous_followup_transcript.jsonl",
                )
            ),
        )
    )

    assert result.status == "completed"
    assert runner.imported_texts_at_call == [
        "We need to fix onboarding step 3 Telegram-to-provisioning handoff.",
        (
            "The key is to make the web app write the bot state that the "
            "in-cluster provisioning worker reconciles. Do not make Vercel "
            "call Kubernetes directly."
        ),
        "Also remember stale Telegram webhooks can break polling.",
        "Right. Clear stale webhooks before relying on polling in core-agent.",
    ]
    new_message_text = _runner_new_message_text(runner)
    assert "<openmagi_context_projection>" in new_message_text
    assert "Telegram-to-provisioning handoff" in new_message_text
    assert "stale Telegram webhooks" in new_message_text
    assert "아까 말한 그거 어떻게 하면돼?" in new_message_text
    assert result.context_continuity.enabled is True
    assert result.context_continuity.imported_event_count == 4
    assert result.context_continuity.rejected_entry_count == 0
    assert result.context_continuity.projection_digest.startswith("sha256:")
    assert result.context_continuity.model_visible_digest.startswith("sha256:")
    assert result.context_continuity.source_transcript_head_digest.startswith("sha256:")
    _assert_no_context_authority(result)
    _assert_no_write_authority(result)


def test_runner_context_continuity_reuses_session_without_duplicate_imports(
    tmp_path: Path,
) -> None:
    from magi_agent.adk_bridge.session_service import WorkspaceSessionService
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )
    from magi_agent.runtime.transcript import TranscriptStore

    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(
        [_final_event("done")],
        session_service=session_service,
    )
    config = RunnerSessionBoundaryConfig(
        enabled=True,
        timeoutMs=500,
        contextContinuity={
            "enabled": True,
            "modelVisibleProjectionEnabled": True,
            "maxImportedEvents": 8,
        },
    )
    transcript_store = TranscriptStore(
        file_path=_copy_context_fixture(
            tmp_path,
            "ambiguous_followup_transcript.jsonl",
        )
    )
    boundary = RunnerSessionBoundary()

    first = asyncio.run(
        boundary.run_turn(
            _turn_input(
                turn_id="turn-followup-1",
                message_text="아까 말한 그거 어떻게 하면돼?",
            ),
            runner=runner,
            config=config,
            transcript_store=transcript_store,
        )
    )
    second = asyncio.run(
        boundary.run_turn(
            _turn_input(
                turn_id="turn-followup-2",
                message_text="아까 말한 그거 다시 확인해줘.",
            ),
            runner=runner,
            config=config,
            transcript_store=transcript_store,
        )
    )

    session = asyncio.run(
        session_service.get_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
        )
    )
    assert session is not None
    imported_texts = [
        event.content.parts[0].text
        for event in session.events
        if event.content is not None and event.content.parts
    ]

    assert first.context_continuity.imported_event_count == 4
    assert second.context_continuity.imported_event_count == 0
    assert second.context_continuity.reason_codes == (
        "committed_history_deduplicated",
    )
    assert imported_texts == [
        "We need to fix onboarding step 3 Telegram-to-provisioning handoff.",
        (
            "The key is to make the web app write the bot state that the "
            "in-cluster provisioning worker reconciles. Do not make Vercel "
            "call Kubernetes directly."
        ),
        "Also remember stale Telegram webhooks can break polling.",
        "Right. Clear stale webhooks before relying on polling in core-agent.",
    ]
    _assert_no_context_authority(second)
    _assert_no_write_authority(second)


def test_runner_context_projection_uses_compacted_session_when_transcript_read_is_stale(
    tmp_path: Path,
) -> None:
    from magi_agent.adk_bridge.session_service import WorkspaceSessionService
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )
    from magi_agent.runtime.transcript import (
        AssistantTextEntry,
        CompactionBoundaryEntry,
        TranscriptStore,
        TurnCommittedEntry,
        UserMessageEntry,
    )

    def _store(path: Path, entries: list[object]) -> TranscriptStore:
        store = TranscriptStore(file_path=path)
        for entry in entries:
            store.append(entry)
        return store

    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(
        [_final_event("done")],
        session_service=session_service,
    )
    config = RunnerSessionBoundaryConfig(
        enabled=True,
        timeoutMs=500,
        contextContinuity={
            "enabled": True,
            "modelVisibleProjectionEnabled": True,
            "maxImportedEvents": 8,
        },
    )
    boundary = RunnerSessionBoundary()
    compacted_store = _store(
        tmp_path / "compacted.jsonl",
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ],
    )
    stale_raw_store = _store(
        tmp_path / "stale-raw.jsonl",
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ],
    )

    first = asyncio.run(
        boundary.run_turn(
            _turn_input(turn_id="turn-followup-1", message_text="first followup"),
            runner=runner,
            config=config,
            transcript_store=compacted_store,
        )
    )
    second = asyncio.run(
        boundary.run_turn(
            _turn_input(turn_id="turn-followup-2", message_text="second followup"),
            runner=runner,
            config=config,
            transcript_store=stale_raw_store,
        )
    )
    second_message = runner.calls[-1]["new_message"]
    assert isinstance(second_message, types.Content)
    assert second_message.parts
    second_text = second_message.parts[0].text
    assert second_text is not None

    assert first.context_continuity.compaction_applied is True
    assert second.context_continuity.compaction_applied is True
    assert "Approved compact summary." in second_text
    assert "Post-boundary question" in second_text
    assert "Post-boundary answer" in second_text
    assert "Raw pre-boundary context" not in second_text
    assert "Raw pre-boundary answer" not in second_text
    _assert_no_context_authority(second)
    _assert_no_write_authority(second)


def test_runner_context_continuity_respects_compaction_boundary(
    tmp_path: Path,
) -> None:
    from magi_agent.adk_bridge.session_service import WorkspaceSessionService
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )
    from magi_agent.runtime.transcript import TranscriptStore

    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(
        [_final_event("done")],
        session_service=session_service,
    )

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(
                turn_id="turn-followup",
                message_text="좀전에 말했던거 이어서 해줘",
            ),
            runner=runner,
            config=RunnerSessionBoundaryConfig(
                enabled=True,
                timeoutMs=500,
                contextContinuity={
                    "enabled": True,
                    "modelVisibleProjectionEnabled": True,
                },
            ),
            transcript_store=TranscriptStore(
                file_path=_copy_context_fixture(
                    tmp_path,
                    "compact_summary_transcript.jsonl",
                )
            ),
        )
    )

    imported = "\n".join(runner.imported_texts_at_call)
    new_message_text = _runner_new_message_text(runner)
    assert result.context_continuity.compaction_applied is True
    assert result.context_continuity.dropped_pre_boundary_count == 2
    assert "vague follow-up prompts resolve" in imported
    assert "Recent detail remains verbatim" in new_message_text
    assert "Raw pre-boundary detail" not in imported
    assert "Raw pre-boundary detail" not in new_message_text
    _assert_no_context_authority(result)


def test_runner_context_continuity_rejects_private_payloads_from_session_and_projection(
    tmp_path: Path,
) -> None:
    from magi_agent.adk_bridge.session_service import WorkspaceSessionService
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )
    from magi_agent.runtime.transcript import TranscriptStore

    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(
        [_final_event("done")],
        session_service=session_service,
    )

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(turn_id="turn-followup", message_text="continue"),
            runner=runner,
            config=RunnerSessionBoundaryConfig(
                enabled=True,
                timeoutMs=500,
                contextContinuity={
                    "enabled": True,
                    "modelVisibleProjectionEnabled": True,
                },
            ),
            transcript_store=TranscriptStore(
                file_path=_copy_context_fixture(
                    tmp_path,
                    "private_payload_rejection.jsonl",
                )
            ),
        )
    )

    imported = "\n".join(runner.imported_texts_at_call)
    new_message_text = _runner_new_message_text(runner)
    dumped = f"{imported}\n{new_message_text}\n{result.model_dump()}"
    assert result.context_continuity.rejected_entry_count >= 1
    assert "Safe user text survives." in imported
    assert "evidence://safe/context-1" in new_message_text
    assert "/workspace/private" not in dumped
    assert "REDACT_ME_SECRET_SENTINEL" not in dumped
    assert "REDACT_ME_COOKIE_SENTINEL" not in dumped
    assert "REDACT_ME_AUTH_SENTINEL" not in dumped
    _assert_no_context_authority(result)


def test_fake_runner_exception_is_classified_without_public_write_authority() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    runner = _FakeRunner(error=RuntimeError("provider failed"))

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
        )
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_invoked is True
    assert result.runner_completed is False
    assert result.terminal_metadata.error_category == "runner_exception"
    assert result.terminal_metadata.ts_error_code == "runner_exception"
    assert result.terminal_metadata.fallback_action == "restore_typescript"
    assert result.local_public_events[-1] == {
        "type": "turn_end",
        "turnId": "turn-1",
        "status": "aborted",
        "reason": "runner_exception",
    }
    assert result.user_visible_output is None
    _assert_no_write_authority(result)


def test_runner_timeout_cancels_fake_runner_and_classifies_timeout() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    runner = _FakeRunner(wait_until_cancelled=True)

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=10),
        )
    )

    assert result.status == "timeout"
    assert result.reason == "runner_timeout"
    assert result.runner_invoked is True
    assert runner.cancelled is True
    assert result.terminal_metadata.error_category == "timeout"
    assert result.terminal_metadata.fallback_action == "restore_typescript"
    _assert_no_write_authority(result)


def test_cancellation_before_run_does_not_invoke_runner() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    runner = _FakeRunner([_final_event("unused")])
    token = RunnerCancellationToken()
    token.cancel()

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
            cancellation_token=token,
        )
    )

    assert result.status == "cancelled"
    assert result.reason == "cancelled_before_run"
    assert result.runner_invoked is False
    assert runner.calls == []
    assert result.terminal_metadata.error_category == "user_interrupt"
    assert result.terminal_metadata.fallback_action == "fail_closed"
    _assert_no_write_authority(result)


def test_cancellation_during_run_cancels_runner_task() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    async def exercise() -> tuple[object, _FakeRunner]:
        runner = _FakeRunner(wait_until_cancelled=True)
        token = RunnerCancellationToken()
        task = asyncio.create_task(
            RunnerSessionBoundary().run_turn(
                _turn_input(),
                runner=runner,
                config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
                cancellation_token=token,
            )
        )
        await runner.started.wait()
        token.cancel()
        return await task, runner

    result, runner = asyncio.run(exercise())

    assert result.status == "cancelled"
    assert result.reason == "cancelled_during_run"
    assert result.runner_invoked is True
    assert runner.cancelled is True
    assert result.terminal_metadata.error_category == "user_interrupt"
    assert result.terminal_metadata.fallback_action == "fail_closed"
    _assert_no_write_authority(result)


def test_cancellation_during_run_returns_when_runner_swallows_cancellation() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    class _CancellationSwallowingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.started = asyncio.Event()
            self.cancelled = False

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            self.started.set()
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.cancelled = True
                await asyncio.sleep(0.75)
                return
            if False:
                yield _final_event("unreachable")

    async def exercise() -> tuple[object, _CancellationSwallowingRunner, float]:
        runner = _CancellationSwallowingRunner()
        token = RunnerCancellationToken()
        task = asyncio.create_task(
            RunnerSessionBoundary().run_turn(
                _turn_input(),
                runner=runner,
                config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
                cancellation_token=token,
            )
        )
        await runner.started.wait()
        started = time.monotonic()
        token.cancel()
        result = await task
        return result, runner, time.monotonic() - started

    result, runner, elapsed_seconds = asyncio.run(exercise())

    assert elapsed_seconds < 0.3
    assert result.status == "cancelled"
    assert result.reason == "cancelled_during_run"
    assert result.runner_invoked is True
    assert result.runner_completed is False
    assert runner.cancelled is True
    assert len(runner.calls) == 1
    assert result.terminal_metadata.error_category == "user_interrupt"
    assert result.terminal_metadata.fallback_action == "fail_closed"
    _assert_no_write_authority(result)


def test_cancellation_keeps_same_session_active_until_swallowed_runner_exits() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    class _CancellationSwallowingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.started = asyncio.Event()
            self.cancelled = False
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            self.started.set()
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.cancelled = True
                await self.release.wait()
                self.finished.set()
                return
            if False:
                yield _final_event("unreachable")

    async def exercise() -> tuple[object, object, object, _CancellationSwallowingRunner]:
        boundary = RunnerSessionBoundary()
        config = RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500)
        runner = _CancellationSwallowingRunner()
        token = RunnerCancellationToken()
        first_task = asyncio.create_task(
            boundary.run_turn(
                _turn_input(turn_id="turn-1"),
                runner=runner,
                config=config,
                cancellation_token=token,
            )
        )
        await runner.started.wait()
        token.cancel()
        first_result = await first_task

        second_result = await boundary.run_turn(
            _turn_input(turn_id="turn-2"),
            runner=_FakeRunner([_final_event("blocked", invocation_id="turn-2")]),
            config=config,
        )

        runner.release.set()
        await runner.finished.wait()
        await asyncio.sleep(0)
        third_result = await boundary.run_turn(
            _turn_input(turn_id="turn-3"),
            runner=_FakeRunner([_final_event("third", invocation_id="turn-3")]),
            config=config,
        )
        return first_result, second_result, third_result, runner

    first_result, second_result, third_result, runner = asyncio.run(exercise())

    assert first_result.status == "cancelled"
    assert first_result.reason == "cancelled_during_run"
    assert runner.cancelled is True
    assert second_result.status == "concurrent_denied"
    assert second_result.reason == "active_session_turn"
    assert second_result.runner_invoked is False
    assert third_result.status == "completed"
    assert third_result.reason == "runner_completed"
    _assert_no_write_authority(second_result)


def test_runner_timeout_returns_when_runner_swallows_cancellation() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    class _CancellationSwallowingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.cancelled = False

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.cancelled = True
                await asyncio.sleep(0.75)
                return
            if False:
                yield _final_event("unreachable")

    async def exercise() -> tuple[object, _CancellationSwallowingRunner, float]:
        runner = _CancellationSwallowingRunner()
        started = time.monotonic()
        result = await RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=10),
        )
        return result, runner, time.monotonic() - started

    result, runner, elapsed_seconds = asyncio.run(exercise())

    assert elapsed_seconds < 0.3
    assert result.status == "timeout"
    assert result.reason == "runner_timeout"
    assert result.runner_invoked is True
    assert result.runner_completed is False
    assert runner.cancelled is True
    assert len(runner.calls) == 1
    assert result.terminal_metadata.error_category == "timeout"
    assert result.terminal_metadata.fallback_action == "restore_typescript"
    _assert_no_write_authority(result)


def test_timeout_keeps_same_session_active_until_swallowed_runner_exits() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    class _CancellationSwallowingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.started = asyncio.Event()
            self.cancelled = False
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            self.started.set()
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.cancelled = True
                await self.release.wait()
                self.finished.set()
                return
            if False:
                yield _final_event("unreachable")

    async def exercise() -> tuple[object, object, object, _CancellationSwallowingRunner]:
        boundary = RunnerSessionBoundary()
        config = RunnerSessionBoundaryConfig(enabled=True, timeoutMs=10)
        runner = _CancellationSwallowingRunner()
        first_result = await boundary.run_turn(
            _turn_input(turn_id="turn-1"),
            runner=runner,
            config=config,
        )

        second_result = await boundary.run_turn(
            _turn_input(turn_id="turn-2"),
            runner=_FakeRunner([_final_event("blocked", invocation_id="turn-2")]),
            config=config,
        )

        runner.release.set()
        await runner.finished.wait()
        await asyncio.sleep(0)
        third_result = await boundary.run_turn(
            _turn_input(turn_id="turn-3"),
            runner=_FakeRunner([_final_event("third", invocation_id="turn-3")]),
            config=config,
        )
        return first_result, second_result, third_result, runner

    first_result, second_result, third_result, runner = asyncio.run(exercise())

    assert first_result.status == "timeout"
    assert first_result.reason == "runner_timeout"
    assert runner.cancelled is True
    assert second_result.status == "concurrent_denied"
    assert second_result.reason == "active_session_turn"
    assert second_result.runner_invoked is False
    assert third_result.status == "completed"
    assert third_result.reason == "runner_completed"
    _assert_no_write_authority(second_result)


def test_same_tick_cancellation_wins_over_runner_completion() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    class _CancelBeforeFinalEventRunner:
        def __init__(self, token: RunnerCancellationToken) -> None:
            self.token = token
            self.calls: list[dict[str, object]] = []

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            self.token.cancel()
            yield _final_event("completed after cancellation")

    token = RunnerCancellationToken()
    runner = _CancelBeforeFinalEventRunner(token)

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(),
            runner=runner,
            config=RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500),
            cancellation_token=token,
        )
    )

    assert result.status == "cancelled"
    assert result.reason == "cancelled_during_run"
    assert result.runner_invoked is True
    assert result.runner_completed is False
    assert result.event_count == 0
    assert result.local_public_events[-1] == {
        "type": "turn_end",
        "turnId": "turn-1",
        "status": "aborted",
        "reason": "user_interrupt",
    }
    assert result.terminal_metadata.error_category == "user_interrupt"
    assert result.terminal_metadata.fallback_action == "fail_closed"
    assert len(runner.calls) == 1
    _assert_no_write_authority(result)


def test_concurrent_same_session_turn_is_denied_without_invoking_second_runner() -> None:
    from magi_agent.runtime.runner_session_boundary import (
        RunnerCancellationToken,
        RunnerSessionBoundary,
        RunnerSessionBoundaryConfig,
    )

    async def exercise() -> tuple[object, object, _FakeRunner, _FakeRunner]:
        boundary = RunnerSessionBoundary()
        config = RunnerSessionBoundaryConfig(enabled=True, timeoutMs=500)
        token = RunnerCancellationToken()
        first_runner = _FakeRunner(wait_until_cancelled=True)
        second_runner = _FakeRunner([_final_event("second", invocation_id="turn-2")])
        first_task = asyncio.create_task(
            boundary.run_turn(
                _turn_input(turn_id="turn-1"),
                runner=first_runner,
                config=config,
                cancellation_token=token,
            )
        )
        await first_runner.started.wait()
        second_result = await boundary.run_turn(
            _turn_input(turn_id="turn-2"),
            runner=second_runner,
            config=config,
        )
        token.cancel()
        first_result = await first_task
        return first_result, second_result, first_runner, second_runner

    first_result, second_result, first_runner, second_runner = asyncio.run(exercise())

    assert second_result.status == "concurrent_denied"
    assert second_result.reason == "active_session_turn"
    assert second_result.runner_invoked is False
    assert second_result.terminal_metadata.error_category == "user_interrupt"
    assert second_runner.calls == []
    assert first_result.status == "cancelled"
    assert first_runner.cancelled is True
    _assert_no_write_authority(second_result)


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Subprocess-based import-boundary probe flakes on some hosts where the "
        "interpreter eagerly loads socket/subprocess/urllib at startup. Tracked "
        "in openmagi/magi-agent CI-baseline quarantine; do not fix in the CI "
        "bootstrap PR."
    ),
)
def test_runner_session_boundary_import_does_not_activate_tools_memory_workspace_or_routes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module(
    "magi_agent.runtime.runner_session_boundary"
)
assert hasattr(module, "RunnerSessionBoundary")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "openai",
    "anthropic",
    "fastapi",
    "uvicorn",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.sse",
    "magi_agent.runtime.control",
)
forbidden_prefixes = (
    "magi_agent.memory",
    "magi_agent.workspace",
    "magi_agent.children",
    "magi_agent.missions",
    "magi_agent.scheduler",
    "magi_agent.transport.chat",
    "magi_agent.transport.routes",
    "magi_agent.app",
    "magi_agent.main",
)
loaded = [
    module_name
    for module_name in set(sys.modules) - before
    if module_name in forbidden_exact
    or any(module_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"runner session boundary loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_runner_session_boundary_source_uses_adapter_bridge_and_no_production_activators() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "runtime"
        / "runner_session_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "OpenMagiRunnerAdapter" in source
    assert "RunnerTurnInput" in source
    assert "OpenMagiEventBridge(live_compatible=True)" in source
    assert "evaluate_projection_write_intent" in source
    assert "classify_adk_runtime_failure" in source
    assert "decide_retry_fallback" in source

    forbidden_fragments = (
        "ToolDispatcher",
        "ToolHost",
        "MemoryService",
        "AgentMemory",
        "WorkspaceIsolation",
        "ChildAgent",
        "Mission",
        "TranscriptStore",
        "InMemorySseWriter",
        "ControlEventLedger",
        "FastAPI(",
        "APIRouter(",
        "@app.",
        "add_api_route",
        "google.adk.runners",
        "google.adk.agents",
        "google.adk.sessions",
        "open(",
        ".write_text(",
        ".write_bytes(",
        "requests.",
        "httpx.",
        "subprocess.",
        "os.system",
        "exec(",
        "eval(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
