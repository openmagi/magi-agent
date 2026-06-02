from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.events import Event

from magi_agent.config.env import parse_gate3a_recorded_replay_env
from magi_agent.shadow.gate3a_bundle import Gate3ARecordedBundle
from magi_agent.shadow.gate3a_replay import (
    Gate3ALocalReplayRunner,
    Gate3AReplayError,
    RecordedOutputToolPolicy,
    run_gate3a_recorded_replay_async,
    validate_gate3a_local_path,
)


def _bundle(*, recorded_tool_call_id: str = "tool-call-1") -> Gate3ARecordedBundle:
    return Gate3ARecordedBundle.model_validate(
        {
            "schemaVersion": "gate3a.recordedBundle.v1",
            "bundleId": "bundle_local_replay_0001",
            "sourceRuntime": "typescript-core-agent",
            "recordingMode": "recorded_redacted",
            "redactionStatus": "verified",
            "createdAt": "2026-05-16T00:00:00Z",
            "sourceProvenance": {
                "sourceKind": "local_fixture",
                "sourcePath": "local-fixtures/replay.json",
                "productionPathIncluded": False,
                "liveCaptureIncluded": False,
            },
            "turn": {
                "sessionRef": "redacted-session",
                "turnId": "turn_local_replay_0001",
                "agentRole": "research",
                "spawnDepth": 0,
                "channel": "local_replay",
            },
            "recipe": {
                "recipeSnapshotId": "recipe_local_replay_v1",
                "packIds": ["openmagi.research"],
                "hardSafetyEnabled": True,
            },
            "transcriptEntries": [{"entryId": "ts-1", "publicText": "hello"}],
            "agentEvents": [{"eventId": "evt-1", "eventType": "text"}],
            "recordedToolResults": [
                {
                    "toolCallId": recorded_tool_call_id,
                    "toolName": "search.readonly",
                    "status": "recorded",
                    "outputMetadata": {"preview": "redacted"},
                    "dispatchedLive": False,
                }
            ],
            "controlEvents": [],
            "evidenceRecords": [],
        }
    )


FakeLocalRunner = Gate3ALocalReplayRunner


def test_gate3a_recorded_replay_flags_are_disabled_by_default() -> None:
    config = parse_gate3a_recorded_replay_env({})

    assert config.enabled is False
    assert config.allow_model_calls is False
    assert config.input_dir is None
    assert config.output_dir is None
    assert config.max_bundles == 1


@pytest.mark.parametrize(
    "path",
    (
        "/data/bots/bot-123/workspace",
        "/workspace/bot-123/transcripts",
        "/mnt/pvc/core-agent",
        "supabase://project/transcripts",
        "s3://prod-bucket/shadow",
        "https://openmagi.ai/shadow",
        "file:///tmp/gate3a",
        "openmagi.ai/local-shadow",
        "./infra/k8s/prod",
        "./k3s/prod",
        "~/.kube/config",
        "./secrets/gate3a",
        "./local/bot-123/replay",
        "./missions/store",
        "./scheduler/store",
        "./mission-store/gate3a",
        "./infra/docker/provisioning-worker/deploy.sh",
    ),
)
def test_gate3a_input_output_dirs_reject_production_path_patterns(path: str) -> None:
    with pytest.raises(ValueError):
        validate_gate3a_local_path(path)


def test_replay_uses_injectable_local_runner_boundary_and_reports_adk_primitives(
    tmp_path: Path,
) -> None:
    runner = FakeLocalRunner(
        (
            SimpleNamespace(
                event_id="evt-1",
                text="hello",
                transcript_refs=("ts-1",),
                tool_call_id="tool-call-1",
            ),
        )
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    assert runner.called_with is not None
    assert report.shadow_runtime == "python-adk"
    assert report.adk_primitives == ("Agent", "Runner", "Event")
    assert report.custom_runtime_loop is False
    assert report.parity.event_projection == "pass"
    assert report.parity.transcript_projection == "pass"
    assert report.parity.evidence_audit == "audit_only"
    assert report.attachment_flags.tool_side_effects_attached is False


def test_replay_accepts_official_event_compatible_local_adk_runner(
    tmp_path: Path,
) -> None:
    runner = FakeLocalRunner(
        (
            Event(
                author="local-adk-runner",
                id="evt-1",
                custom_metadata={
                    "transcriptRefs": ["ts-1"],
                    "toolCallId": "tool-call-1",
                },
            ),
        )
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    assert report.parity.event_projection == "pass"
    assert report.parity.transcript_projection == "pass"
    assert report.parity.tool_projection == "pass"


def test_replay_rejects_unmarked_arbitrary_collect_events_runner_before_invocation(
    tmp_path: Path,
) -> None:
    class ArbitraryRunner:
        called = False

        async def collect_events(self, bundle: Gate3ARecordedBundle) -> list[object]:
            self.called = True
            return []

    runner = ArbitraryRunner()

    with pytest.raises(Gate3AReplayError):
        asyncio.run(
            run_gate3a_recorded_replay_async(
                _bundle(),
                local_runner=runner,
                output_dir=tmp_path,
            )
        )

    assert runner.called is False


def test_replay_rejects_spoofed_marker_only_runner_before_invocation(
    tmp_path: Path,
) -> None:
    class SpoofedMarkerOnlyRunner:
        adk_primitives = ("Agent", "Runner", "Event")
        local_only = True
        live_capture_attached = False
        production_route_attached = False
        production_storage_attached = False
        user_visible_output_attached = False
        telegram_attached = False
        tool_side_effects_attached = False
        evidence_block_mode_attached = False

        def __init__(self) -> None:
            self.called = False

        async def collect_events(self, bundle: Gate3ARecordedBundle) -> list[object]:
            self.called = True
            return []

    runner = SpoofedMarkerOnlyRunner()

    with pytest.raises(Gate3AReplayError):
        asyncio.run(
            run_gate3a_recorded_replay_async(
                _bundle(),
                local_runner=runner,
                output_dir=tmp_path,
            )
        )

    assert runner.called is False


def test_replay_rejects_custom_tool_policy_before_comparison(
    tmp_path: Path,
) -> None:
    class SideEffectingPolicy:
        def __init__(self) -> None:
            self.called = False

        async def resolve_recorded_tool_output(
            self,
            *,
            tool_call_id: str,
            bundle: Gate3ARecordedBundle,
        ) -> dict[str, object]:
            self.called = True
            return {"unsafe": "side-effect path"}

    policy = SideEffectingPolicy()
    runner = FakeLocalRunner(
        (SimpleNamespace(event_id="evt-1", transcript_refs=("ts-1",), tool_call_id="tool-call-1"),)
    )

    with pytest.raises(Gate3AReplayError):
        asyncio.run(
            run_gate3a_recorded_replay_async(
                _bundle(),
                local_runner=runner,
                output_dir=tmp_path,
                tool_policy=policy,  # type: ignore[arg-type]
            )
        )

    assert policy.called is False


def test_recorded_tool_outputs_are_metadata_only_and_unmatched_tool_calls_do_not_dispatch_live_side_effects(
    tmp_path: Path,
) -> None:
    runner = FakeLocalRunner(
        (SimpleNamespace(event_id="evt-extra", tool_call_id="unmatched-tool-call"),)
    )
    policy = RecordedOutputToolPolicy()

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(recorded_tool_call_id="recorded-only"),
            local_runner=runner,
            output_dir=tmp_path,
            tool_policy=policy,
        )
    )

    assert policy.live_dispatch_attempts == 0
    assert report.parity.event_projection == "extra"
    assert report.parity.transcript_projection == "mismatch"
    assert report.parity.tool_projection == "extra"
    assert report.attachment_flags.tool_side_effects_attached is False


def test_replay_sanitizes_runner_exception_details_before_report_failures(
    tmp_path: Path,
) -> None:
    raw_detail = "Bearer abcdefghijklmnop /data/bots/bot-999/workspace"

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=FakeLocalRunner((), failure=RuntimeError(raw_detail)),
            output_dir=tmp_path,
        )
    )

    failures_text = " ".join(report.failures)
    assert "Bearer" not in failures_text
    assert "abcdefghijklmnop" not in failures_text
    assert "/data/bots/bot-999/workspace" not in failures_text
    assert report.parity.event_projection == "runner_failure"


def test_replay_sanitizes_general_absolute_runner_exception_paths(
    tmp_path: Path,
) -> None:
    raw_detail = "failed at /Users/kevin/Desktop/openmagi/private.txt and C:\\Users\\kevin\\secret.txt"

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=FakeLocalRunner((), failure=RuntimeError(raw_detail)),
            output_dir=tmp_path,
        )
    )

    failures_text = " ".join(report.failures)
    assert "/Users/kevin/Desktop/openmagi/private.txt" not in failures_text
    assert "C:\\Users\\kevin\\secret.txt" not in failures_text
    assert report.parity.event_projection == "runner_failure"


def test_replay_sanitizes_runner_exception_credentials_without_validation_escape(
    tmp_path: Path,
) -> None:
    raw_secrets = {
        "ghp": "ghp_1234567890abcdefghij1234567890abcdefgh",
        "gho": "gho_1234567890abcdefghij1234567890abcdefgh",
        "ghu": "ghu_1234567890abcdefghij1234567890abcdefgh",
        "ghs": "ghs_1234567890abcdefghij1234567890abcdefgh",
        "ghr": "ghr_1234567890abcdefghij1234567890abcdefgh",
        "github_pat": "github_pat_11AAAAAAA0BBBBBBBBBBBB_1234567890abcdefghijklmnopqrstuvwxyz",
        "github_env": "GITHUB_TOKEN=ghp_1234567890abcdefghij1234567890abcdefgh",
        "stripe": "STRIPE_SECRET_KEY=sk_live_1234567890abcdefghij",
        "supabase": "SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiJ9.secret.signature",
        "openai": "OPENAI_API_KEY=sk-proj-1234567890abcdefghij",
        "generic_token": "CUSTOM_TOKEN=custom-token-value",
        "generic_secret": "CUSTOM_SECRET=custom-secret-value",
        "generic_password": "DATABASE_PASSWORD=custom-password-value",
        "generic_api_key": "SERVICE_API_KEY=custom-api-key-value",
        "quoted_github": "GITHUB_TOKEN='custom-token-value'",
        "quoted_stripe": 'STRIPE_SECRET_KEY="custom-secret-value"',
    }
    raw_detail = "runner failed with " + " ".join(raw_secrets.values())

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=FakeLocalRunner((), failure=RuntimeError(raw_detail)),
            output_dir=tmp_path,
        )
    )

    artifact = tmp_path / f"{report.shadow_run_id}.comparison.json"
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    report_text = " ".join(report.failures)
    artifact_text = json.dumps(artifact_payload, sort_keys=True)
    combined_text = f"{report_text} {artifact_text}"
    for raw_secret in raw_secrets.values():
        assert raw_secret not in combined_text
    assert "github_pat_" not in combined_text
    assert "GITHUB_TOKEN=" not in combined_text
    assert "STRIPE_SECRET_KEY=" not in combined_text
    assert "SUPABASE_SERVICE_ROLE_KEY=" not in combined_text
    assert "OPENAI_API_KEY=" not in combined_text
    assert "custom-token-value" not in combined_text
    assert "custom-secret-value" not in combined_text
    assert report.parity.event_projection == "runner_failure"


def test_replay_sanitizes_quoted_env_assignments_in_runner_failure_report_and_artifact(
    tmp_path: Path,
) -> None:
    raw_detail = (
        "runner failed with "
        "GITHUB_TOKEN='custom-token-value' "
        'STRIPE_SECRET_KEY="custom-secret-value"'
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=FakeLocalRunner((), failure=RuntimeError(raw_detail)),
            output_dir=tmp_path,
        )
    )

    artifact = tmp_path / f"{report.shadow_run_id}.comparison.json"
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    combined_text = " ".join(
        (
            " ".join(report.failures),
            report.public_summary.preview,
            json.dumps(artifact_payload, sort_keys=True),
        )
    )

    assert "custom-token-value" not in combined_text
    assert "custom-secret-value" not in combined_text
    assert "GITHUB_TOKEN" not in combined_text
    assert "STRIPE_SECRET_KEY" not in combined_text
    assert report.parity.event_projection == "runner_failure"


def test_transcript_projection_detects_mismatch_when_event_projection_passes(
    tmp_path: Path,
) -> None:
    runner = FakeLocalRunner(
        (SimpleNamespace(event_id="evt-1", transcript_refs=("different-ts",), tool_call_id="tool-call-1"),)
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    assert report.parity.event_projection == "pass"
    assert report.parity.transcript_projection == "mismatch"
    assert report.public_summary.status != "pass"


def test_transcript_projection_is_not_applicable_without_comparable_refs(
    tmp_path: Path,
) -> None:
    runner = FakeLocalRunner(
        (SimpleNamespace(event_id="evt-1", tool_call_id="tool-call-1"),)
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    assert report.parity.event_projection == "pass"
    assert report.parity.transcript_projection == "not_applicable"


def test_event_projection_is_order_sensitive(tmp_path: Path) -> None:
    bundle = _bundle()
    payload = bundle.model_dump(by_alias=True, mode="json", warnings=False)
    payload["agentEvents"] = [
        {"eventId": "evt-1", "eventType": "text"},
        {"eventId": "evt-2", "eventType": "text"},
    ]
    ordered_bundle = Gate3ARecordedBundle.model_validate(payload)
    runner = FakeLocalRunner(
        (
            SimpleNamespace(event_id="evt-2", transcript_refs=("ts-1",)),
            SimpleNamespace(event_id="evt-1", transcript_refs=("ts-1",), tool_call_id="tool-call-1"),
        )
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            ordered_bundle,
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    assert report.parity.event_projection == "mismatch"


def test_existing_symlink_target_is_validated(tmp_path: Path) -> None:
    unsafe_target = tmp_path / "mission-store"
    unsafe_target.mkdir()
    symlink = tmp_path / "local-shadow"
    symlink.symlink_to(unsafe_target, target_is_directory=True)

    with pytest.raises(ValueError):
        validate_gate3a_local_path(symlink)


def test_nonexistent_output_child_under_symlink_parent_is_rejected(tmp_path: Path) -> None:
    unsafe_target = tmp_path / "mission-store"
    unsafe_target.mkdir()
    symlink = tmp_path / "safe-link"
    symlink.symlink_to(unsafe_target, target_is_directory=True)

    with pytest.raises(ValueError):
        validate_gate3a_local_path(symlink / "new-output")


def test_replay_rejects_repo_output_dir_before_runner_invocation() -> None:
    class UncalledRunner:
        adk_primitives = ("Agent", "Runner", "Event")
        local_only = True
        live_capture_attached = False
        production_route_attached = False
        production_storage_attached = False
        user_visible_output_attached = False
        telegram_attached = False
        tool_side_effects_attached = False
        evidence_block_mode_attached = False
        called = False

        async def collect_events(self, bundle: Gate3ARecordedBundle) -> list[object]:
            self.called = True
            return []

    runner = UncalledRunner()

    with pytest.raises(Gate3AReplayError, match="output"):
        asyncio.run(
            run_gate3a_recorded_replay_async(
                _bundle(),
                local_runner=runner,
                output_dir=Path(__file__).parents[1],
            )
        )

    assert runner.called is False


def test_replay_writes_sanitized_local_report_artifact(tmp_path: Path) -> None:
    raw_runner_payload = "Bearer abcdefghijklmnop raw tool payload"
    runner = FakeLocalRunner(
        (
            SimpleNamespace(
                event_id="evt-1",
                transcript_refs=("ts-1",),
                tool_call_id="tool-call-1",
                raw_payload=raw_runner_payload,
            ),
        )
    )

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            _bundle(),
            local_runner=runner,
            output_dir=tmp_path,
        )
    )

    artifact = tmp_path / f"{report.shadow_run_id}.comparison.json"
    assert artifact.exists()
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    artifact_text = json.dumps(artifact_payload, sort_keys=True)
    assert artifact_payload["shadowRunId"] == report.shadow_run_id
    assert artifact_payload["storageMode"] == "local_only"
    assert artifact_payload["customRuntimeLoop"] is False
    assert "raw tool payload" not in artifact_text
    assert "Bearer" not in artifact_text


def test_execution_surface_metadata_does_not_trigger_live_execution_or_report_payload(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    payload = bundle.model_dump(by_alias=True, mode="json", warnings=False)
    payload["recordedToolResults"][0]["outputMetadata"] = {
        "executionSurface": "controlled_composable_execution",
        "commandEvidence": "redacted local command transcript",
        "autoExecuted": False,
    }
    metadata_bundle = Gate3ARecordedBundle.model_validate(payload)
    runner = FakeLocalRunner(
        (
            SimpleNamespace(
                event_id="evt-1",
                transcript_refs=("ts-1",),
                tool_call_id="tool-call-1",
            ),
        )
    )
    policy = RecordedOutputToolPolicy()

    report = asyncio.run(
        run_gate3a_recorded_replay_async(
            metadata_bundle,
            local_runner=runner,
            output_dir=tmp_path,
            tool_policy=policy,
        )
    )

    artifact = tmp_path / f"{report.shadow_run_id}.comparison.json"
    artifact_text = artifact.read_text(encoding="utf-8")
    assert policy.live_dispatch_attempts == 0
    assert report.attachment_flags.tool_side_effects_attached is False
    assert "controlled_composable_execution" not in artifact_text
    assert "redacted local command transcript" not in artifact_text


def test_replay_rejects_invalid_bundle_before_runner_construction(tmp_path: Path) -> None:
    invalid_bundle = Gate3ARecordedBundle.model_construct(
        bundle_id="bundle_local_invalid",
        source_runtime="typescript-core-agent",
        recording_mode="recorded_redacted",
        redaction_status="verified",
        recorded_tool_results=(),
    )

    with pytest.raises(Gate3AReplayError):
        asyncio.run(
            run_gate3a_recorded_replay_async(
                invalid_bundle,
                local_runner=FakeLocalRunner(()),
                output_dir=tmp_path,
            )
        )
