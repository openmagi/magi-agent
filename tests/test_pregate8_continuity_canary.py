from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.runtime.runner_session_boundary import (
    RunnerSessionBoundary,
    RunnerSessionBoundaryConfig,
)
from magi_agent.runtime.transcript import TranscriptStore
from magi_agent.runtime.turn_controller import TurnControllerInput


CONTEXT_FIXTURES = Path(__file__).parent / "fixtures" / "context_continuity"


class _ContinuityObservingRunner:
    app_name = "openmagi"

    def __init__(self, *, session_service: WorkspaceSessionService) -> None:
        self.session_service = session_service
        self.calls: list[dict[str, object]] = []
        self.imported_texts_at_call: list[str] = []

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
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
        yield Event(
            author="model",
            content=types.Content(role="model", parts=[types.Part(text="context seen")]),
            turn_complete=True,
            invocation_id=str(kwargs["invocation_id"]),
        )


def _turn_input(*, turn_id: str, message_text: str) -> TurnControllerInput:
    return TurnControllerInput(
        userId="user-1",
        sessionId="agent:main:app:default",
        turnId=turn_id,
        messageText=message_text,
        harnessState=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=0,
        ),
    )


def _copy_context_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.write_text(
        (CONTEXT_FIXTURES / name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return target


def _new_message_text(runner: _ContinuityObservingRunner) -> str:
    assert runner.calls
    content = runner.calls[0]["new_message"]
    assert isinstance(content, types.Content)
    assert content.parts
    text = content.parts[0].text
    assert text is not None
    return text


def _run_continuity_turn(
    tmp_path: Path,
    *,
    fixture_name: str,
    message_text: str,
) -> tuple[object, _ContinuityObservingRunner, str]:
    session_service = WorkspaceSessionService(app_name="openmagi")
    runner = _ContinuityObservingRunner(session_service=session_service)

    result = asyncio.run(
        RunnerSessionBoundary().run_turn(
            _turn_input(turn_id="turn-followup", message_text=message_text),
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
                file_path=_copy_context_fixture(tmp_path, fixture_name)
            ),
        )
    )
    return result, runner, _new_message_text(runner)


def test_pregate8_canary_records_ambiguous_followup_context_as_digest_only_evidence(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    result, runner, model_visible_message = _run_continuity_turn(
        tmp_path,
        fixture_name="ambiguous_followup_transcript.jsonl",
        message_text="아까 말한 그거 어떻게 하면돼?",
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        result,
        adk_session_texts=runner.imported_texts_at_call,
        model_visible_message=model_visible_message,
        expected_antecedent="Telegram-to-provisioning handoff",
        current_followup="아까 말한 그거",
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.imported_event_count == 4
    assert evidence.antecedent_present_in_adk_session is True
    assert evidence.antecedent_present_in_model_visible_projection is True
    assert evidence.current_followup_present_in_model_visible_message is True
    assert evidence.projection_digest.startswith("sha256:")
    assert evidence.model_visible_digest.startswith("sha256:")
    assert evidence.source_transcript_head_digest.startswith("sha256:")
    assert evidence.observed_adk_session_digest.startswith("sha256:")
    assert evidence.observed_model_visible_digest.startswith("sha256:")
    assert evidence.response_authority == "none"
    assert evidence.local_only is True
    assert evidence.diagnostic_only is True
    assert evidence.private_payload_rejected is False
    assert evidence.authority_flags.transcript_write_allowed is False

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Telegram-to-provisioning" not in serialized
    assert "아까 말한 그거" not in serialized
    assert "web app write the bot state" not in serialized
    assert "context seen" not in serialized


def test_pregate8_canary_proves_compaction_suppresses_pre_boundary_raw_transcript(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    result, runner, model_visible_message = _run_continuity_turn(
        tmp_path,
        fixture_name="compact_summary_transcript.jsonl",
        message_text="좀전에 말했던거 이어서 해줘",
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        result,
        adk_session_texts=runner.imported_texts_at_call,
        model_visible_message=model_visible_message,
        expected_antecedent="vague follow-up prompts resolve",
        current_followup="좀전에 말했던거",
        forbidden_payloads=("Raw pre-boundary detail",),
        require_compaction_applied=True,
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.compaction_applied is True
    assert evidence.compaction_boundary_respected is True
    assert evidence.forbidden_payload_observed is False
    assert evidence.reason_codes == (
        "runner_completed",
        "antecedent_present",
        "followup_present",
        "compaction_boundary_respected",
        "forbidden_payload_absent",
        "fallback_none",
    )

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Raw pre-boundary detail" not in serialized
    assert "vague follow-up prompts resolve" not in serialized


def test_pregate8_canary_records_private_payload_rejection_without_leaking_raw_values(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    result, runner, model_visible_message = _run_continuity_turn(
        tmp_path,
        fixture_name="private_payload_rejection.jsonl",
        message_text="continue",
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        result,
        adk_session_texts=runner.imported_texts_at_call,
        model_visible_message=model_visible_message,
        expected_antecedent="Safe user text survives.",
        current_followup="continue",
        forbidden_payloads=(
            "/workspace/private",
            "REDACT_ME_SECRET_SENTINEL",
            "REDACT_ME_COOKIE_SENTINEL",
            "REDACT_ME_AUTH_SENTINEL",
        ),
        require_rejected_entries=True,
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.rejected_entry_count >= 1
    assert evidence.private_payload_rejected is True
    assert evidence.forbidden_payload_observed is False
    assert evidence.antecedent_present_in_adk_session is True

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Safe user text survives." not in serialized
    assert "/workspace/private" not in serialized
    assert "REDACT_ME_SECRET_SENTINEL" not in serialized
    assert "REDACT_ME_COOKIE_SENTINEL" not in serialized
    assert "REDACT_ME_AUTH_SENTINEL" not in serialized


def test_pregate8_canary_fails_closed_when_antecedent_is_missing(tmp_path: Path) -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    result, runner, model_visible_message = _run_continuity_turn(
        tmp_path,
        fixture_name="ambiguous_followup_transcript.jsonl",
        message_text="아까 말한 그거 어떻게 하면돼?",
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        result,
        adk_session_texts=runner.imported_texts_at_call,
        model_visible_message=model_visible_message,
        expected_antecedent="missing antecedent label",
        current_followup="아까 말한 그거",
        fallback_status="none",
    )

    assert evidence.status == "fail"
    assert "antecedent_missing" in evidence.reason_codes
    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "missing antecedent label" not in serialized


def test_pregate8_canary_evidence_cannot_forge_authority_flags() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        PreGate8ContinuityCanaryEvidence,
    )

    forged = PreGate8ContinuityCanaryEvidence.model_construct(
        status="pass",
        localOnly=False,
        diagnosticOnly=False,
        responseAuthority="python",
        fallbackStatus="none",
        importedEventCount=1,
        rejectedEntryCount=0,
        compactionApplied=False,
        projectionDigest="sha256:" + "1" * 64,
        modelVisibleDigest="sha256:" + "2" * 64,
        sourceTranscriptHeadDigest="sha256:" + "3" * 64,
        observedAdkSessionDigest="sha256:" + "4" * 64,
        observedModelVisibleDigest="sha256:" + "5" * 64,
        authorityFlags={
            "transcriptWriteAllowed": True,
            "sseWriteAllowed": True,
            "dbWriteAllowed": True,
            "memoryWriteAllowed": True,
            "workspaceMutationAllowed": True,
            "childExecutionAllowed": True,
            "channelDeliveryAllowed": True,
        },
    )

    assert forged.local_only is True
    assert forged.diagnostic_only is True
    assert forged.response_authority == "none"
    assert forged.authority_flags.transcript_write_allowed is False
    assert forged.authority_flags.memory_write_allowed is False
    assert forged.authority_flags.channel_delivery_allowed is False


def test_pregate8_canary_evidence_rejects_raw_text_in_digest_fields() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        PreGate8ContinuityCanaryEvidence,
    )

    base = {
        "status": "pass",
        "fallbackStatus": "none",
        "importedEventCount": 1,
        "rejectedEntryCount": 0,
        "compactionApplied": False,
        "projectionDigest": "sha256:" + "1" * 64,
        "modelVisibleDigest": "sha256:" + "2" * 64,
        "sourceTranscriptHeadDigest": "sha256:" + "3" * 64,
        "observedAdkSessionDigest": "sha256:" + "4" * 64,
        "observedModelVisibleDigest": "sha256:" + "5" * 64,
        "antecedentDigest": "sha256:" + "6" * 64,
        "currentFollowupDigest": "sha256:" + "7" * 64,
    }

    for field in (
        "projectionDigest",
        "modelVisibleDigest",
        "sourceTranscriptHeadDigest",
        "observedAdkSessionDigest",
        "observedModelVisibleDigest",
        "antecedentDigest",
        "currentFollowupDigest",
    ):
        with pytest.raises(ValueError, match="sha256 digests"):
            PreGate8ContinuityCanaryEvidence.model_validate(
                base | {field: "raw antecedent or prompt text"}
            )


def test_pregate8_canary_import_boundary_is_pure_local_contract_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.gates.pregate8_continuity_canary")
assert hasattr(module, "build_pre_gate8_continuity_canary_evidence")

forbidden_prefixes = (
    "fastapi",
    "starlette",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "google.adk",
    "google.genai",
    "magi_agent.transport",
    "magi_agent.tools",
    "magi_agent.toolhost",
    "magi_agent.memory",
    "magi_agent.workspace",
    "magi_agent.browser",
    "magi_agent.channels",
)

loaded = tuple(sys.modules)
violations = [
    name
    for name in loaded
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
]
assert not violations, violations
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
