from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from openmagi_core_agent.shadow.fixture_runner import (
    Gate2ShadowFixtureInput,
    Gate2ShadowOutputFlags,
    Gate2ShadowFixtureReport,
    load_gate2_shadow_fixture,
    run_gate2_shadow_fixture,
    run_gate2_shadow_fixture_async,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate2"


class _FalseyMapping(dict[str, object]):
    def __bool__(self) -> bool:
        return False


ADDITIONAL_CREDENTIAL_METADATA_CASES = (
    pytest.param({"accessKey": "fixture"}, id="access-key-camel"),
    pytest.param({"access_key": "fixture"}, id="access-key-snake"),
    pytest.param({"access-key": "fixture"}, id="access-key-kebab"),
    pytest.param({"accesskey": "fixture"}, id="collapsed-access-key"),
    pytest.param({"awsAccessKeyId": "fixture"}, id="aws-access-key-id-camel"),
    pytest.param({"aws_access_key_id": "fixture"}, id="aws-access-key-id-snake"),
    pytest.param({"aws-access-key-id": "fixture"}, id="aws-access-key-id-kebab"),
    pytest.param({"awsaccesskeyid": "fixture"}, id="collapsed-aws-access-key-id"),
    pytest.param({"nested": {"accessKey": "fixture"}}, id="nested-access-key"),
    pytest.param(
        {"nested": [{"awsAccessKeyId": "fixture"}]},
        id="nested-list-aws-access-key-id",
    ),
    pytest.param({"slacktoken": "fixture"}, id="collapsed-slack-token"),
    pytest.param({"refreshtoken": "fixture"}, id="collapsed-refresh-token"),
    pytest.param({"providertoken": "fixture"}, id="collapsed-provider-token"),
    pytest.param({"credential": "fixture"}, id="credential"),
    pytest.param({"credentials": "fixture"}, id="credentials"),
    pytest.param({"credentialKey": "fixture"}, id="credential-key-camel"),
    pytest.param({"credentialsKey": "fixture"}, id="credentials-key-camel"),
    pytest.param({"basicauth": "fixture"}, id="collapsed-basic-auth"),
    pytest.param({"nested": {"credential": "fixture"}}, id="nested-credential"),
    pytest.param({"nested": [{"credentialsKey": "fixture"}]}, id="nested-list-credentials-key"),
    pytest.param({"nested": [{"basicauth": "fixture"}]}, id="nested-list-basic-auth"),
)

COMPOUND_OUTPUT_ATTACHMENT_METADATA_CASES = (
    pytest.param({"userOutput": True}, id="user-output-camel"),
    pytest.param({"useroutput": True}, id="user-output-collapsed"),
    pytest.param({"publicOutput": False}, id="public-output-camel"),
    pytest.param({"publicoutput": False}, id="public-output-collapsed"),
    pytest.param({"visibleSse": False}, id="visible-sse-camel"),
    pytest.param({"networkOutput": False}, id="network-output-camel"),
    pytest.param({"networkoutput": False}, id="network-output-collapsed"),
    pytest.param({"routeOutput": False}, id="route-output-camel"),
    pytest.param({"routeoutput": False}, id="route-output-collapsed"),
    pytest.param({"trafficOutput": False}, id="traffic-output-camel"),
    pytest.param({"trafficoutput": False}, id="traffic-output-collapsed"),
    pytest.param({"canaryOutput": False}, id="canary-output-camel"),
    pytest.param({"canaryoutput": False}, id="canary-output-collapsed"),
    pytest.param({"productionAttachment": False}, id="production-attachment-camel"),
    pytest.param({"productionattachment": False}, id="production-attachment-collapsed"),
    pytest.param({"outputAttached": False}, id="output-attached-camel"),
    pytest.param({"outputsAttached": False}, id="outputs-attached-camel"),
    pytest.param({"attachmentOutput": False}, id="attachment-output-camel"),
    pytest.param({"nested": {"public-visible": False}}, id="nested-public-visible-kebab"),
    pytest.param(
        {"nested": [{"network_output": False}]},
        id="nested-list-network-output-snake",
    ),
)

COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES = (
    pytest.param({"routeattached": False}, id="route-attached-compact"),
    pytest.param({"trafficattached": False}, id="traffic-attached-compact"),
    pytest.param({"canaryattached": False}, id="canary-attached-compact"),
    pytest.param({"productionattached": False}, id="production-attached-compact"),
    pytest.param({"networksse": False}, id="network-sse-compact"),
    pytest.param({"uservisible": False}, id="user-visible-compact"),
    pytest.param({"outputattached": False}, id="output-attached-compact"),
    pytest.param({"outputsattached": False}, id="outputs-attached-compact"),
    pytest.param({"attachmentoutput": False}, id="attachment-output-compact"),
    pytest.param(
        {"productiontranscriptappend": False},
        id="production-transcript-append-compact",
    ),
    pytest.param({"outputflags": False}, id="output-flags-compact"),
    pytest.param({"runtimeoutput": False}, id="runtime-output-compact"),
)

COMPACT_OUTPUT_ATTACHMENT_STRING_VALUES = (
    pytest.param("networksse", id="network-sse-compact"),
    pytest.param("uservisible", id="user-visible-compact"),
    pytest.param(
        "productiontranscriptappend",
        id="production-transcript-append-compact",
    ),
    pytest.param("outputflags", id="output-flags-compact"),
    pytest.param("runtimeoutput", id="runtime-output-compact"),
    pytest.param("routeattached", id="route-attached-compact"),
    pytest.param("trafficattached", id="traffic-attached-compact"),
    pytest.param("canaryattached", id="canary-attached-compact"),
    pytest.param("productionattached", id="production-attached-compact"),
)

COMPOSED_COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES = (
    pytest.param({"routeattachedoutput": False}, id="route-attached-output"),
    pytest.param({"trafficattachedoutput": False}, id="traffic-attached-output"),
    pytest.param({"networkoutputattached": False}, id="network-output-attached"),
    pytest.param(
        {"productiontranscriptappendoutput": False},
        id="production-transcript-append-output",
    ),
    pytest.param({"useroutputvisible": False}, id="user-output-visible"),
)

COMPACT_LIVE_BOUNDARY_VALUES = (
    pytest.param("signedexternalack", id="signed-external-ack-compact"),
    pytest.param(
        "signedexternalacknowledgement",
        id="signed-external-acknowledgement-compact",
    ),
    pytest.param(
        "signedexternalackingestion",
        id="signed-external-ack-ingestion-compact",
    ),
    pytest.param("livemissioncreation", id="live-mission-creation-compact"),
    pytest.param("typescriptruntime", id="typescript-runtime-compact"),
    pytest.param("childexecution", id="child-execution-compact"),
    pytest.param("workspacemutation", id="workspace-mutation-compact"),
    pytest.param("workspaceadoption", id="workspace-adoption-compact"),
    pytest.param("customextractor", id="custom-extractor-compact"),
    pytest.param("evidenceblockmode", id="evidence-block-mode-compact"),
    pytest.param("blockfinalanswer", id="block-final-answer-compact"),
    pytest.param("schedulerresume", id="scheduler-resume-compact"),
    pytest.param("schedulerrun", id="scheduler-run-compact"),
)

COMPACT_LIVE_SUFFIX_BOUNDARY_VALUES = (
    pytest.param("signedexternalackdisabled", id="signed-external-ack-disabled"),
    pytest.param("typescriptruntimeenabled", id="typescript-runtime-enabled"),
    pytest.param("livemissioncreationblocked", id="live-mission-creation-blocked"),
    pytest.param("schedulerresumeack", id="scheduler-resume-ack"),
)

COLLAPSED_BACKGROUND_BOUNDARY_VALUES = (
    pytest.param("backgroundresume", id="background-resume-collapsed"),
    pytest.param("backgroundrun", id="background-run-collapsed"),
    pytest.param("backgroundtaskresume", id="background-task-resume-collapsed"),
)


def _valid_gate2_report_payload(comparison_metadata: dict[str, object]) -> dict[str, object]:
    return {
        "inputSource": "synthetic_local",
        "turnId": "gate2-turn-report-metadata",
        "projectedAdkEventIds": (),
        "transcriptRefs": (),
        "sseRefs": (),
        "comparisonMetadata": comparison_metadata,
    }


def test_gate2_shadow_fixture_file_validates_as_committed() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")

    assert fixture.comparison_metadata == {"gate1Fixture": "simple_assistant_text"}


def test_gate2_shadow_fixture_loader_resolves_relative_paths_under_allowed_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(FIXTURES)

    fixture = load_gate2_shadow_fixture(
        "synthetic_text_turn.json",
        fixture_root=FIXTURES,
    )

    assert fixture.turn_id == "gate2-turn-text"


def test_gate2_shadow_fixture_loader_rejects_relative_escape_before_opening(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="fixture_root"):
        load_gate2_shadow_fixture("../escaped.json", fixture_root=root)


def test_gate2_shadow_fixture_loader_rejects_symlink_escape_before_opening(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text("not-json", encoding="utf-8")
    (root / "escape.json").symlink_to(escaped)

    with pytest.raises(ValueError, match="fixture_root"):
        load_gate2_shadow_fixture(root / "escape.json", fixture_root=root)


def test_gate2_shadow_fixture_runner_replays_local_fixture_as_diagnostic_only() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")

    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    assert report.source_runtime == "TypeScript"
    assert report.shadow_runtime == "Python ADK"
    assert report.input_source == "synthetic_local"
    assert report.mode == "fixture_shadow_audit"
    assert report.adk_primitives == ("Agent", "Runner", "Event")
    assert report.custom_runtime_loop is False
    assert report.output_flags == Gate2ShadowOutputFlags()
    assert report.projected_adk_event_ids == ("evt-text-partial", "evt-text-final")
    assert report.transcript_refs == ("gate1/simple_assistant_text.jsonl",)
    assert report.sse_refs == ("gate1/simple_assistant_text.sse",)
    assert report.comparison_metadata["status"] == "diagnostic_only"
    assert report.comparison_metadata["localRunnerStatus"] == "provider_blocked"
    assert report.comparison_metadata["runnerAdapterCollectEventsCalled"] is True
    assert report.comparison_metadata["projectedAdkEventIds"] == [
        "evt-text-partial",
        "evt-text-final",
    ]
    assert report.comparison_metadata["transcriptComparisons"] == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }
    assert report.comparison_metadata["sseComparisons"] == {
        "gate1/simple_assistant_text.sse": "matched"
    }


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_top_level_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    with pytest.raises(TypeError):
        report.comparison_metadata["userVisible"] = True

    assert "userVisible" not in report.comparison_metadata
    assert report.comparison_metadata["status"] == "diagnostic_only"


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_top_level_union_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    with pytest.raises(TypeError):
        report.comparison_metadata |= {"userVisible": True}

    assert "userVisible" not in report.comparison_metadata
    assert report.comparison_metadata["status"] == "diagnostic_only"


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_top_level_dict_base_mutators() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    assert not isinstance(report.comparison_metadata, dict)

    with pytest.raises(TypeError):
        dict.__setitem__(report.comparison_metadata, "userVisible", True)
    with pytest.raises(TypeError):
        dict.update(report.comparison_metadata, {"networkSse": True})

    assert "userVisible" not in report.comparison_metadata
    assert "networkSse" not in report.comparison_metadata
    assert report.comparison_metadata["status"] == "diagnostic_only"


def test_gate2_shadow_fixture_report_comparison_metadata_blocks_top_level_internal_data_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    metadata = report.comparison_metadata

    if hasattr(metadata, "_data"):
        try:
            metadata._data["userVisible"] = True  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    assert "userVisible" not in metadata
    assert metadata["status"] == "diagnostic_only"


def test_gate2_shadow_fixture_report_comparison_metadata_blocks_top_level_internal_non_json_injection() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    metadata = report.comparison_metadata
    non_json_value = object()

    if hasattr(metadata, "_data"):
        try:
            metadata._data["fixtureOnly"] = non_json_value  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    assert metadata.get("fixtureOnly") is not non_json_value
    assert "fixtureOnly" not in report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_nested_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    with pytest.raises(TypeError):
        report.comparison_metadata["transcriptComparisons"]["userVisible"] = True

    with pytest.raises(AttributeError):
        report.comparison_metadata["projectedAdkEventIds"].append("secretKey")

    assert report.comparison_metadata["transcriptComparisons"] == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }
    assert report.comparison_metadata["projectedAdkEventIds"] == [
        "evt-text-partial",
        "evt-text-final",
    ]


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_nested_union_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    with pytest.raises(TypeError):
        report.comparison_metadata["transcriptComparisons"] |= {"userVisible": "matched"}

    assert report.comparison_metadata["transcriptComparisons"] == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }


def test_gate2_shadow_fixture_report_comparison_metadata_rejects_nested_dict_base_mutators() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    transcript_comparisons = report.comparison_metadata["transcriptComparisons"]

    assert not isinstance(transcript_comparisons, dict)

    with pytest.raises(TypeError):
        dict.__setitem__(transcript_comparisons, "userVisible", "matched")
    with pytest.raises(TypeError):
        dict.update(transcript_comparisons, {"networkSse": "matched"})

    assert transcript_comparisons == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }


def test_gate2_shadow_fixture_report_comparison_metadata_blocks_nested_internal_data_mutation() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    transcript_comparisons = report.comparison_metadata["transcriptComparisons"]

    if hasattr(transcript_comparisons, "_data"):
        try:
            transcript_comparisons._data["userVisible"] = "matched"  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    assert transcript_comparisons == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }


def test_gate2_shadow_fixture_report_comparison_metadata_blocks_top_level_object_setattr_replacement() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    metadata = report.comparison_metadata
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    with pytest.raises(AttributeError):
        object.__setattr__(metadata, "_items", (("userVisible", True),))

    assert report.comparison_metadata == metadata
    assert "userVisible" not in report.comparison_metadata
    assert report.comparison_metadata["status"] == "diagnostic_only"
    assert report.model_dump(by_alias=True, mode="json")["comparisonMetadata"] == before_dump


def test_gate2_shadow_fixture_report_comparison_metadata_blocks_nested_object_setattr_replacement() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    transcript_comparisons = report.comparison_metadata["transcriptComparisons"]
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    with pytest.raises(AttributeError):
        object.__setattr__(
            transcript_comparisons,
            "_items",
            (("gate1/simple_assistant_text.jsonl", "mutated"),),
        )

    assert transcript_comparisons == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }
    assert report.model_dump(by_alias=True, mode="json")["comparisonMetadata"] == before_dump


def test_gate2_shadow_fixture_report_serialization_ignores_object_setattr_metadata_replacement() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    object.__setattr__(
        report,
        "comparison_metadata",
        {"userVisible": True, "fixtureOnly": object()},
    )

    dumped = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    assert dumped == before_dump
    assert "userVisible" not in dumped
    assert "fixtureOnly" not in dumped


def test_gate2_shadow_fixture_report_serialization_ignores_dunder_dict_metadata_replacement() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]
    mutated_dict = dict(report.__dict__)
    mutated_dict["comparison_metadata"] = {"userVisible": True, "fixtureOnly": object()}

    object.__setattr__(report, "__dict__", mutated_dict)

    dumped = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    assert dumped == before_dump
    assert "userVisible" not in dumped
    assert "fixtureOnly" not in dumped


@pytest.mark.parametrize(
    ("field_name", "alias", "forged_value", "expected_value"),
    (
        pytest.param("source_runtime", "sourceRuntime", "Python ADK", "TypeScript", id="source-runtime"),
        pytest.param("shadow_runtime", "shadowRuntime", "TypeScript", "Python ADK", id="shadow-runtime"),
        pytest.param("input_source", "inputSource", "redacted_ts_bundle", "synthetic_local", id="input-source"),
        pytest.param("turn_id", "turnId", "bot-1234567890", "gate2-turn-text", id="turn-id"),
        pytest.param("mode", "mode", "production_shadow", "fixture_shadow_audit", id="mode"),
        pytest.param(
            "adk_primitives",
            "adkPrimitives",
            ("Agent", "Runner", "Event", "ProductionRoute"),
            ["Agent", "Runner", "Event"],
            id="adk-primitives",
        ),
        pytest.param("custom_runtime_loop", "customRuntimeLoop", True, False, id="custom-runtime-loop"),
        pytest.param(
            "projected_adk_event_ids",
            "projectedAdkEventIds",
            ("forged-event",),
            ["evt-text-partial", "evt-text-final"],
            id="projected-adk-event-ids",
        ),
        pytest.param(
            "transcript_refs",
            "transcriptRefs",
            ("gate1/forged.jsonl",),
            ["gate1/simple_assistant_text.jsonl"],
            id="transcript-refs",
        ),
        pytest.param(
            "sse_refs",
            "sseRefs",
            ("gate1/forged.sse",),
            ["gate1/simple_assistant_text.sse"],
            id="sse-refs",
        ),
    ),
)
def test_gate2_shadow_fixture_report_serialization_ignores_object_setattr_field_replacement(
    field_name: str,
    alias: str,
    forged_value: object,
    expected_value: object,
) -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    object.__setattr__(report, field_name, forged_value)

    dumped = report.model_dump(by_alias=True, mode="json")

    assert dumped[alias] == expected_value
    assert dumped[alias] != forged_value


@pytest.mark.parametrize(
    ("field_name", "alias", "forged_value", "expected_value"),
    (
        pytest.param("source_runtime", "sourceRuntime", "Python ADK", "TypeScript", id="source-runtime"),
        pytest.param("shadow_runtime", "shadowRuntime", "TypeScript", "Python ADK", id="shadow-runtime"),
        pytest.param("input_source", "inputSource", "redacted_ts_bundle", "synthetic_local", id="input-source"),
        pytest.param("turn_id", "turnId", "bot-1234567890", "gate2-turn-text", id="turn-id"),
        pytest.param("mode", "mode", "production_shadow", "fixture_shadow_audit", id="mode"),
        pytest.param(
            "adk_primitives",
            "adkPrimitives",
            ("Agent", "Runner", "Event", "ProductionRoute"),
            ["Agent", "Runner", "Event"],
            id="adk-primitives",
        ),
        pytest.param("custom_runtime_loop", "customRuntimeLoop", True, False, id="custom-runtime-loop"),
        pytest.param(
            "projected_adk_event_ids",
            "projectedAdkEventIds",
            ("forged-event",),
            ["evt-text-partial", "evt-text-final"],
            id="projected-adk-event-ids",
        ),
        pytest.param(
            "transcript_refs",
            "transcriptRefs",
            ("gate1/forged.jsonl",),
            ["gate1/simple_assistant_text.jsonl"],
            id="transcript-refs",
        ),
        pytest.param(
            "sse_refs",
            "sseRefs",
            ("gate1/forged.sse",),
            ["gate1/simple_assistant_text.sse"],
            id="sse-refs",
        ),
    ),
)
def test_gate2_shadow_fixture_report_serialization_ignores_dunder_dict_field_replacement(
    field_name: str,
    alias: str,
    forged_value: object,
    expected_value: object,
) -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    mutated_dict = dict(report.__dict__)
    mutated_dict[field_name] = forged_value

    object.__setattr__(report, "__dict__", mutated_dict)

    dumped = report.model_dump(by_alias=True, mode="json")

    assert dumped[alias] == expected_value
    assert dumped[alias] != forged_value


@pytest.mark.parametrize(
    ("replacement", "blocked_key"),
    (
        pytest.param({"userVisible": True}, "userVisible", id="user-visible"),
        pytest.param({"fixtureOnly": object()}, "fixtureOnly", id="non-json-object"),
        pytest.param({"token": "fixture"}, "token", id="credential-key"),
    ),
)
def test_gate2_shadow_fixture_report_serialization_blocks_private_canonical_metadata_replacement(
    replacement: object,
    blocked_key: str,
) -> None:
    report = Gate2ShadowFixtureReport.model_validate(
        _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    )
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    object.__setattr__(report, "_canonical_comparison_metadata", replacement)

    try:
        dumped = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]
    except (TypeError, ValueError, PydanticSerializationError):
        return

    assert dumped == before_dump
    assert blocked_key not in dumped


def test_gate2_shadow_fixture_report_serialization_ignores_benign_private_canonical_metadata_replacement() -> None:
    report = Gate2ShadowFixtureReport.model_validate(
        _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    )
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    object.__setattr__(report, "_canonical_comparison_metadata", {"note": "changed"})

    dumped = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    assert dumped == before_dump
    assert dumped["note"] == "fixture-only diagnostic comparison"


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"localRunnerStatus": "completed:0"}, id="local-runner-status"),
        pytest.param(
            {"runnerAdapterCollectEventsCalled": True},
            id="runner-adapter-collect-events-called",
        ),
        pytest.param(
            {"projectedAdkEventIds": ["evt-claimed"]},
            id="projected-adk-event-ids",
        ),
        pytest.param(
            {"transcriptComparisons": {"gate1/simple_assistant_text.jsonl": "matched"}},
            id="transcript-comparisons",
        ),
        pytest.param(
            {"sseComparisons": {"gate1/simple_assistant_text.sse": "matched"}},
            id="sse-comparisons",
        ),
    ),
)
def test_gate2_shadow_fixture_report_rejects_report_owned_comparison_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"status": "diagnostic_only"}, id="status-default"),
        pytest.param({"sourceRuntime": "TypeScript"}, id="source-runtime-default"),
        pytest.param({"shadowRuntime": "Python ADK"}, id="shadow-runtime-default"),
    ),
)
def test_gate2_shadow_fixture_report_rejects_safe_default_metadata_without_runner_context(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOUND_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_report_rejects_compound_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_report_rejects_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize("unsafe_value", COMPACT_OUTPUT_ATTACHMENT_STRING_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
    ),
)
def test_gate2_shadow_fixture_report_rejects_compact_output_aliases_in_string_fields(
    field: str,
    unsafe_value: str,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    if field == "turnId":
        payload["turnId"] = unsafe_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{unsafe_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{unsafe_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{unsafe_value}.sse",)
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOSED_COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_report_rejects_composed_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize(
    "adk_primitives",
    (
        pytest.param(("Agent", "Runner"), id="subset"),
        pytest.param(("Runner", "Agent", "Event"), id="reordered"),
        pytest.param(("Agent", "Runner", "Event", "Agent"), id="duplicate-extra"),
        pytest.param(("Agent", "Runner", "Event", "ProductionRoute"), id="unknown-extra"),
    ),
)
def test_gate2_shadow_fixture_report_requires_exact_adk_primitives(
    adk_primitives: tuple[str, ...],
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    payload["adkPrimitives"] = adk_primitives

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


def test_gate2_shadow_fixture_report_context_token_cannot_forge_report_owned_metadata() -> None:
    from openmagi_core_agent.shadow import fixture_runner

    context: dict[str, object] = {}
    context_key = getattr(fixture_runner, "_RUNNER_GENERATED_REPORT_CONTEXT_KEY", None)
    context_token = getattr(fixture_runner, "_RUNNER_GENERATED_REPORT_CONTEXT_TOKEN", None)
    if context_key is not None and context_token is not None:
        context[str(context_key)] = context_token

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(
                {
                    "localRunnerStatus": "completed:0",
                    "runnerAdapterCollectEventsCalled": True,
                    "projectedAdkEventIds": ["evt-forged"],
                    "transcriptComparisons": {
                        "gate1/simple_assistant_text.jsonl": "matched",
                    },
                    "sseComparisons": {"gate1/simple_assistant_text.sse": "matched"},
                }
            ),
            context=context,
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param(
            {"status": "diagnostic_only", "nested": {"status": "diagnostic_only"}},
            id="nested-safe-default-status",
        ),
        pytest.param(
            {
                "localRunnerStatus": "provider_blocked",
                "nested": {"localRunnerStatus": "provider_blocked"},
            },
            id="nested-local-runner-status",
        ),
        pytest.param(
            {
                "runnerAdapterCollectEventsCalled": True,
                "nested": [{"runnerAdapterCollectEventsCalled": True}],
            },
            id="nested-list-runner-adapter-flag",
        ),
        pytest.param(
            {
                "projectedAdkEventIds": ["evt-actual"],
                "nested": {"projectedAdkEventIds": ["evt-forged"]},
            },
            id="nested-projected-event-ids",
        ),
        pytest.param(
            {
                "transcriptComparisons": {"gate1/simple_assistant_text.jsonl": "matched"},
                "nested": [
                    {"transcriptComparisons": {"gate1/forged.jsonl": "matched"}}
                ],
            },
            id="nested-list-transcript-comparisons",
        ),
    ),
)
def test_gate2_shadow_fixture_runner_generated_report_rejects_nested_report_owned_metadata(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    build_report = getattr(
        fixture_runner,
        "_build_runner_generated_gate2_shadow_fixture_report",
    )

    with pytest.raises(ValueError):
        build_report(
            {
                "inputSource": "synthetic_local",
                "turnId": "gate2-turn-generated-report-nested-metadata",
                "outputFlags": Gate2ShadowOutputFlags(),
                "projectedAdkEventIds": ("evt-actual",),
                "transcriptRefs": ("gate1/simple_assistant_text.jsonl",),
                "sseRefs": ("gate1/simple_assistant_text.sse",),
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOUND_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_runner_generated_report_rejects_compound_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    build_report = getattr(
        fixture_runner,
        "_build_runner_generated_gate2_shadow_fixture_report",
    )

    with pytest.raises(ValueError):
        build_report(
            {
                "inputSource": "synthetic_local",
                "turnId": "gate2-turn-generated-report-compound-metadata",
                "outputFlags": Gate2ShadowOutputFlags(),
                "projectedAdkEventIds": ("evt-actual",),
                "transcriptRefs": (),
                "sseRefs": (),
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_runner_generated_report_rejects_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    build_report = getattr(
        fixture_runner,
        "_build_runner_generated_gate2_shadow_fixture_report",
    )

    with pytest.raises(ValueError):
        build_report(
            {
                "inputSource": "synthetic_local",
                "turnId": "gate2-turn-generated-report-compact-metadata",
                "outputFlags": Gate2ShadowOutputFlags(),
                "projectedAdkEventIds": ("evt-actual",),
                "transcriptRefs": (),
                "sseRefs": (),
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOSED_COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_runner_generated_report_rejects_composed_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    build_report = getattr(
        fixture_runner,
        "_build_runner_generated_gate2_shadow_fixture_report",
    )

    with pytest.raises(ValueError):
        build_report(
            {
                "inputSource": "synthetic_local",
                "turnId": "gate2-turn-generated-report-composed-compact-metadata",
                "outputFlags": Gate2ShadowOutputFlags(),
                "projectedAdkEventIds": ("evt-actual",),
                "transcriptRefs": (),
                "sseRefs": (),
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"publicOutput": True}, id="public-output-camel"),
        pytest.param({"useroutput": True}, id="user-output-collapsed"),
        pytest.param({"publicoutput": True}, id="public-output-collapsed"),
        pytest.param({"networkoutput": True}, id="network-output-collapsed"),
        pytest.param({"routeoutput": True}, id="route-output-collapsed"),
        pytest.param({"trafficoutput": True}, id="traffic-output-collapsed"),
        pytest.param({"canaryoutput": True}, id="canary-output-collapsed"),
        pytest.param({"productionattachment": True}, id="production-attachment-collapsed"),
    ),
)
def test_gate2_shadow_fixture_runner_generated_report_serialization_rejects_compound_output_attachment_snapshot(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    report = run_gate2_shadow_fixture(
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-serialization-compound-metadata",
            userPrompt="hello",
        )
    )
    snapshot_store = getattr(
        fixture_runner,
        "_REPORT_COMPARISON_METADATA_SNAPSHOTS",
    )
    snapshot_store[id(report)] = (comparison_metadata, True)

    with pytest.raises((TypeError, ValueError, PydanticSerializationError)):
        report.model_dump(by_alias=True, mode="json")


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_runner_generated_report_serialization_rejects_compact_output_attachment_snapshot(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    report = run_gate2_shadow_fixture(
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-serialization-compact-metadata",
            userPrompt="hello",
        )
    )
    snapshot_store = getattr(
        fixture_runner,
        "_REPORT_COMPARISON_METADATA_SNAPSHOTS",
    )
    snapshot_store[id(report)] = (comparison_metadata, True)

    with pytest.raises((TypeError, ValueError, PydanticSerializationError)):
        report.model_dump(by_alias=True, mode="json")


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOSED_COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_runner_generated_report_serialization_rejects_composed_compact_output_attachment_snapshot(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    report = run_gate2_shadow_fixture(
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-serialization-composed-compact-metadata",
            userPrompt="hello",
        )
    )
    snapshot_store = getattr(
        fixture_runner,
        "_REPORT_COMPARISON_METADATA_SNAPSHOTS",
    )
    snapshot_store[id(report)] = (comparison_metadata, True)

    with pytest.raises((TypeError, ValueError, PydanticSerializationError)):
        report.model_dump(by_alias=True, mode="json")


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    (
        pytest.param("turn_id", "signedexternalack", id="turn-id-compact-live"),
        pytest.param("turn_id", "networksse", id="turn-id-compact-output"),
        pytest.param(
            "projected_adk_event_ids",
            ("evt-useroutputvisible",),
            id="projected-ids-composed-output",
        ),
        pytest.param(
            "transcript_refs",
            ("gate1/productiontranscriptappendoutput.jsonl",),
            id="transcript-ref-composed-output",
        ),
    ),
)
def test_gate2_shadow_fixture_report_serialization_rejects_unsafe_output_snapshot_values(
    field_name: str,
    unsafe_value: object,
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    report = run_gate2_shadow_fixture(
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-serialization-output-snapshot",
            userPrompt="hello",
        )
    )
    snapshot_store = getattr(fixture_runner, "_REPORT_OUTPUT_SNAPSHOTS")
    snapshot = dict(snapshot_store[id(report)])
    snapshot[field_name] = unsafe_value
    snapshot_store[id(report)] = snapshot

    with pytest.raises((TypeError, ValueError, PydanticSerializationError)):
        report.model_dump(by_alias=True, mode="json")


@pytest.mark.parametrize(
    ("replacement", "blocked_key"),
    (
        pytest.param({"localRunnerStatus": "completed:0"}, "localRunnerStatus", id="local-runner-status"),
        pytest.param(
            {"runnerAdapterCollectEventsCalled": True},
            "runnerAdapterCollectEventsCalled",
            id="runner-adapter-collect-events-called",
        ),
        pytest.param(
            {"projectedAdkEventIds": ["evt-claimed"]},
            "projectedAdkEventIds",
            id="projected-adk-event-ids",
        ),
        pytest.param(
            {"transcriptComparisons": {"gate1/simple_assistant_text.jsonl": "matched"}},
            "transcriptComparisons",
            id="transcript-comparisons",
        ),
        pytest.param(
            {"sseComparisons": {"gate1/simple_assistant_text.sse": "matched"}},
            "sseComparisons",
            id="sse-comparisons",
        ),
    ),
)
def test_gate2_shadow_fixture_report_serialization_blocks_private_canonical_report_owned_replacement(
    replacement: dict[str, object],
    blocked_key: str,
) -> None:
    report = Gate2ShadowFixtureReport.model_validate(
        _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    )
    before_dump = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]

    object.__setattr__(report, "_canonical_comparison_metadata", replacement)

    try:
        dumped = report.model_dump(by_alias=True, mode="json")["comparisonMetadata"]
    except (TypeError, ValueError, PydanticSerializationError):
        return

    assert dumped == before_dump
    assert blocked_key not in dumped


def test_gate2_shadow_fixture_report_metadata_nested_list_rejects_attribute_injection() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)
    projected_ids = report.comparison_metadata["projectedAdkEventIds"]

    with pytest.raises(AttributeError):
        object.__setattr__(projected_ids, "_items", ("secretKey",))

    assert projected_ids == ["evt-text-partial", "evt-text-final"]


def test_gate2_shadow_fixture_report_comparison_metadata_dumps_as_plain_json_like_mapping() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    report = run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)

    dumped = report.model_dump(by_alias=True, mode="json")

    assert isinstance(dumped["comparisonMetadata"], dict)
    assert isinstance(dumped["comparisonMetadata"]["transcriptComparisons"], dict)
    assert dumped["comparisonMetadata"]["status"] == "diagnostic_only"
    assert dumped["comparisonMetadata"]["projectedAdkEventIds"] == [
        "evt-text-partial",
        "evt-text-final",
    ]
    assert dumped["comparisonMetadata"]["transcriptComparisons"] == {
        "gate1/simple_assistant_text.jsonl": "matched"
    }


def test_gate2_shadow_fixture_comparison_fails_when_gate1_expected_output_differs(
    tmp_path: Path,
) -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    gate1 = tmp_path / "gate1"
    gate1.mkdir()
    (gate1 / "simple_assistant_text.jsonl").write_text("wrong\n", encoding="utf-8")
    (gate1 / "simple_assistant_text.sse").write_text("wrong\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="transcript fixture mismatch"):
        run_gate2_shadow_fixture(fixture, base_fixture_dir=tmp_path)


def test_gate2_shadow_fixture_requires_projected_events_for_claimed_projected_ids() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-missing-events",
        userPrompt="hello",
        projectedAdkEventIds=("claimed-event",),
    )

    with pytest.raises(ValueError, match="projectedAdkEvents"):
        run_gate2_shadow_fixture(fixture)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "transcriptRefs": ("gate1/simple_assistant_text.jsonl",),
        },
        {
            "sseRefs": ("gate1/simple_assistant_text.sse",),
        },
    ),
)
def test_gate2_shadow_fixture_requires_projected_events_for_output_refs(
    payload: dict[str, object],
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-ref-without-events",
            "userPrompt": "hello",
            **payload,
        }
    )

    with pytest.raises(ValueError, match="projectedAdkEvents"):
        run_gate2_shadow_fixture(fixture, base_fixture_dir=FIXTURES.parent)


def test_gate2_shadow_fixture_requires_base_dir_when_output_refs_exist() -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")

    with pytest.raises(ValueError, match="base_fixture_dir"):
        run_gate2_shadow_fixture(fixture)


def test_gate2_shadow_fixture_rejects_expected_output_symlink_escape(
    tmp_path: Path,
) -> None:
    fixture = load_gate2_shadow_fixture(FIXTURES / "synthetic_text_turn.json")
    base = tmp_path / "base"
    escaped_gate1 = tmp_path / "escaped" / "gate1"
    base.mkdir()
    escaped_gate1.mkdir(parents=True)
    (escaped_gate1 / "simple_assistant_text.jsonl").write_text(
        (FIXTURES.parent / "gate1" / "simple_assistant_text.jsonl").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (escaped_gate1 / "simple_assistant_text.sse").write_text(
        (FIXTURES.parent / "gate1" / "simple_assistant_text.sse").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (base / "gate1").symlink_to(escaped_gate1, target_is_directory=True)

    with pytest.raises(ValueError, match="base_fixture_dir"):
        run_gate2_shadow_fixture(fixture, base_fixture_dir=base)


def test_gate2_shadow_fixture_report_uses_actual_projected_ids_not_claimed_ids() -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-unclaimed-events",
            "userPrompt": "hello",
            "projectedAdkEvents": [
                {
                    "id": "evt-actual",
                    "author": "model",
                    "role": "model",
                    "text": "hello",
                    "invocationId": "gate2-turn-unclaimed-events",
                }
            ],
        }
    )

    report = run_gate2_shadow_fixture(fixture)

    assert report.projected_adk_event_ids == ("evt-actual",)
    assert report.comparison_metadata["projectedAdkEventIds"] == ["evt-actual"]


def test_gate2_shadow_fixture_sync_runner_fails_cleanly_inside_active_event_loop() -> None:
    async def run_inside_active_loop() -> None:
        fixture = Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-active-loop",
            userPrompt="hello",
        )

        with pytest.raises(RuntimeError, match="run_gate2_shadow_fixture_async"):
            run_gate2_shadow_fixture(fixture)

    asyncio.run(run_inside_active_loop())


def test_gate2_shadow_fixture_async_runner_works_inside_active_event_loop() -> None:
    async def run_inside_active_loop() -> None:
        from openmagi_core_agent.shadow.fixture_runner import run_gate2_shadow_fixture_async

        fixture = Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-async",
            userPrompt="hello",
        )

        report = await run_gate2_shadow_fixture_async(fixture)

        assert report.comparison_metadata["localRunnerStatus"] == "provider_blocked"

    asyncio.run(run_inside_active_loop())


@pytest.mark.parametrize(
    "source",
    (
        "golden_fixture",
        "redacted_ts_bundle",
        "synthetic_local",
    ),
)
def test_gate2_shadow_fixture_accepts_fixture_like_sources_only(source: str) -> None:
    fixture = Gate2ShadowFixtureInput(
        source=source,
        turnId="gate2-turn-allowed",
        userPrompt="hello",
    )

    assert fixture.source == source


@pytest.mark.parametrize(
    "source",
    (
        "production_route",
        "live_capture",
        "telegram",
        "proxy",
        "api",
        "dashboard",
        "provisioning",
        "k8s",
        "deploy",
        "runtime_selector",
    ),
)
def test_gate2_shadow_fixture_rejects_live_and_production_sources(source: str) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput(
            source=source,
            turnId="gate2-turn-rejected",
            userPrompt="hello",
        )


@pytest.mark.parametrize(
    "flag",
    (
        "userVisible",
        "productionTranscriptAppend",
        "networkSse",
        "routeAttached",
        "trafficAttached",
        "canaryAttached",
        "productionAttached",
    ),
)
def test_gate2_shadow_output_flags_cannot_be_enabled_by_alias_model_copy(flag: str) -> None:
    flags = Gate2ShadowOutputFlags()

    with pytest.raises(ValidationError):
        flags.model_copy(update={flag: True})


@pytest.mark.parametrize(
    "flag",
    (
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "route_attached",
        "traffic_attached",
        "canary_attached",
        "production_attached",
    ),
)
def test_gate2_shadow_output_flags_cannot_be_enabled_by_field_model_copy(flag: str) -> None:
    flags = Gate2ShadowOutputFlags()

    with pytest.raises(ValidationError):
        flags.model_copy(update={flag: True})


def test_gate2_shadow_fixture_rejects_enabled_output_flag_claims() -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-attachment-claim",
            userPrompt="hello",
            outputFlags={
                "userVisible": True,
                "routeAttached": True,
                "trafficAttached": True,
            },
        )


def test_gate2_shadow_fixture_rejects_constructed_enabled_output_flag_claims() -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-constructed-attachment-claim",
            userPrompt="hello",
            outputFlags=Gate2ShadowOutputFlags.model_construct(user_visible=True),
        )


def test_gate2_shadow_fixture_runner_rejects_falsey_output_flags_raw_extra_attachment_claim() -> None:
    flags = Gate2ShadowOutputFlags()
    object.__setattr__(
        flags,
        "__pydantic_extra__",
        _FalseyMapping({"outputAttached": True}),
    )

    with pytest.raises((ValueError, ValidationError), match="output flags|raw extra"):
        fixture = Gate2ShadowFixtureInput(
            source="synthetic_local",
            turnId="gate2-turn-falsey-output-flags-extra-claim",
            userPrompt="hello",
            outputFlags=flags,
        )
        run_gate2_shadow_fixture(fixture)


def test_gate2_shadow_fixture_runner_rejects_mutated_output_flag_claims() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-mutated-attachment-claim",
        userPrompt="hello",
    )
    object.__setattr__(fixture.output_flag_claims, "user_visible", True)

    with pytest.raises(ValueError, match="output flags"):
        run_gate2_shadow_fixture(fixture)


def test_gate2_shadow_fixture_runner_rejects_dunder_dict_output_flags_alias_injection() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-raw-alias-attachment-claim",
        userPrompt="hello",
    )
    mutated_state = dict(fixture.__dict__)
    mutated_state["outputFlags"] = Gate2ShadowOutputFlags.model_construct(
        user_visible=True,
    )
    object.__setattr__(fixture, "__dict__", mutated_state)

    with pytest.raises((ValueError, ValidationError), match="output flags|outputFlags"):
        run_gate2_shadow_fixture(fixture)


def test_gate2_shadow_fixture_runner_rejects_pydantic_extra_output_flags_alias_injection() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-raw-extra-attachment-claim",
        userPrompt="hello",
    )
    object.__setattr__(
        fixture,
        "__pydantic_extra__",
        {
            "outputFlags": Gate2ShadowOutputFlags.model_construct(
                user_visible=True,
            ),
        },
    )

    with pytest.raises((ValueError, ValidationError), match="output flags|outputFlags"):
        run_gate2_shadow_fixture(fixture)


def test_gate2_shadow_fixture_runner_rejects_falsey_pydantic_extra_output_flags_alias_injection() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-falsey-raw-extra-attachment-claim",
        userPrompt="hello",
    )
    object.__setattr__(
        fixture,
        "__pydantic_extra__",
        _FalseyMapping(
            {
                "outputFlags": Gate2ShadowOutputFlags.model_construct(
                    user_visible=True,
                ),
            }
        ),
    )

    with pytest.raises((ValueError, ValidationError), match="output flags|outputFlags"):
        run_gate2_shadow_fixture(fixture)


@pytest.mark.parametrize(
    "output_flags",
    (
        {"userVisible": 0},
        {"productionTranscriptAppend": 0},
        {"networkSse": 0},
        {"routeAttached": 0},
        {"trafficAttached": 0},
        {"canaryAttached": 0},
        {"productionAttached": 0},
        {"user_visible": 0},
        {"production_transcript_append": 0},
        {"network_sse": 0},
        {"route_attached": 0},
        {"traffic_attached": 0},
        {"canary_attached": 0},
        {"production_attached": 0},
    ),
)
def test_gate2_shadow_output_flags_reject_numeric_zero(
    output_flags: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowOutputFlags.model_validate(output_flags)

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-numeric-zero-flags",
                "userPrompt": "hello",
                "outputFlags": output_flags,
            }
        )


def test_gate2_shadow_fixture_report_serialization_ignores_mutated_output_flag_internals() -> None:
    report = Gate2ShadowFixtureReport.model_validate(
        _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    )
    object.__setattr__(report.output_flags, "user_visible", True)
    mutated_dict = dict(report.output_flags.__dict__)
    mutated_dict["network_sse"] = 0
    object.__setattr__(report.output_flags, "__dict__", mutated_dict)

    dumped = report.model_dump(by_alias=True, mode="json")["outputFlags"]

    assert dumped == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }


def test_gate2_shadow_output_flags_serializes_canonical_false_after_object_setattr_forgery() -> None:
    flags = Gate2ShadowOutputFlags()
    object.__setattr__(flags, "user_visible", True)
    object.__setattr__(flags, "network_sse", 0)

    assert flags.model_dump(by_alias=True, mode="json") == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }


@pytest.mark.parametrize(
    "replacement",
    (
        pytest.param(object(), id="invalid-object"),
        pytest.param(
            Gate2ShadowOutputFlags.model_construct(user_visible=True),
            id="constructed-true-flag",
        ),
        pytest.param(
            Gate2ShadowOutputFlags.model_construct(network_sse=0),
            id="constructed-numeric-zero-flag",
        ),
    ),
)
def test_gate2_shadow_fixture_report_serialization_blocks_private_canonical_output_flag_replacement(
    replacement: object,
) -> None:
    report = Gate2ShadowFixtureReport.model_validate(
        _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    )

    object.__setattr__(report, "_canonical_output_flags", replacement)

    try:
        dumped = report.model_dump(by_alias=True, mode="json")["outputFlags"]
    except (TypeError, ValueError, PydanticSerializationError):
        return

    assert dumped == {
        "userVisible": False,
        "productionTranscriptAppend": False,
        "networkSse": False,
        "routeAttached": False,
        "trafficAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
    }


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        {"status": "diagnostic_only"},
        {"status": "authoritative"},
        {"status": "production_ready"},
        {"sourceRuntime": "TypeScript"},
        {"sourceRuntime": "Python ADK"},
        {"shadowRuntime": "Python ADK"},
        {"shadowRuntime": "TypeScript"},
        {"localRunnerStatus": "completed:1"},
        {"runnerAdapterCollectEventsCalled": False},
        {"projectedAdkEventIds": ["claimed-from-fixture"]},
        {"transcriptComparisons": {"gate1/simple_assistant_text.jsonl": "matched"}},
        {"sseComparisons": {"gate1/simple_assistant_text.sse": "matched"}},
        {"userVisible": True},
        {"networkSse": True},
        {"outputFlags": {"userVisible": True}},
        {"outputFlags": {"networkSse": True}},
        {"runtimeOutput": {"userVisible": True}},
        {"route": "attached"},
        {"traffic": "attached"},
        {"canary": "attached"},
        {"production": "attached"},
    ),
)
def test_gate2_shadow_fixture_rejects_reserved_comparison_metadata_claims(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-reserved-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"bundleKind": "redacted_ts_capture"}, id="camel"),
        pytest.param({"bundlekind": "redacted_ts_capture"}, id="compact"),
        pytest.param(
            {"nested": [{"bundle_kind": "redacted_ts_capture"}]},
            id="nested-snake",
        ),
    ),
)
def test_gate2_shadow_fixture_rejects_bundle_kind_comparison_metadata_claims(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="bundleKind"):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-bundle-kind-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"bundleKind": "redacted_ts_capture"}, id="camel"),
        pytest.param({"bundlekind": "redacted_ts_capture"}, id="compact"),
        pytest.param(
            {"nested": [{"bundle_kind": "redacted_ts_capture"}]},
            id="nested-snake",
        ),
    ),
)
def test_gate2_shadow_fixture_report_rejects_bundle_kind_comparison_metadata_claims(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="bundleKind"):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


def test_gate2_shadow_fixture_runner_rejects_mutated_bundle_kind_comparison_metadata() -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-mutated-bundle-kind-metadata",
            "userPrompt": "hello",
            "comparisonMetadata": {"note": "fixture-only diagnostic comparison"},
        }
    )
    fixture.comparison_metadata["bundleKind"] = "redacted_ts_capture"

    with pytest.raises(ValidationError, match="bundleKind"):
        run_gate2_shadow_fixture(fixture)


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"bundleKind": "redacted_ts_capture"}, id="camel"),
        pytest.param({"bundlekind": "redacted_ts_capture"}, id="compact"),
        pytest.param(
            {"nested": [{"bundle_kind": "redacted_ts_capture"}]},
            id="nested-snake",
        ),
    ),
)
def test_gate2_shadow_fixture_runner_generated_report_rejects_bundle_kind_metadata(
    comparison_metadata: dict[str, object],
) -> None:
    from openmagi_core_agent.shadow import fixture_runner

    build_report = getattr(
        fixture_runner,
        "_build_runner_generated_gate2_shadow_fixture_report",
    )

    with pytest.raises(ValueError, match="bundleKind"):
        build_report(
            {
                "inputSource": "synthetic_local",
                "turnId": "gate2-turn-generated-report-bundle-kind-metadata",
                "outputFlags": Gate2ShadowOutputFlags(),
                "projectedAdkEventIds": (),
                "transcriptRefs": (),
                "sseRefs": (),
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOUND_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_input_rejects_compound_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-compound-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        {"note": "fixture-only diagnostic comparison"},
        {"gate1Fixture": "simple_assistant_text"},
    ),
)
def test_gate2_shadow_fixture_accepts_benign_comparison_metadata_claims(
    comparison_metadata: dict[str, object],
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-safe-metadata",
            "userPrompt": "hello",
            "comparisonMetadata": comparison_metadata,
        }
    )

    assert fixture.comparison_metadata == comparison_metadata


def test_gate2_shadow_fixture_runner_rejects_mutated_reserved_comparison_metadata() -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-mutated-metadata",
            "userPrompt": "hello",
            "comparisonMetadata": {"note": "fixture-only diagnostic comparison"},
        }
    )
    fixture.comparison_metadata["userVisible"] = True
    fixture.comparison_metadata["networkSse"] = True
    fixture.comparison_metadata["routeAttached"] = True
    fixture.comparison_metadata["status"] = "authoritative"

    with pytest.raises(ValidationError):
        run_gate2_shadow_fixture(fixture)


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"token": "fixture"}, id="token"),
        pytest.param({"accessToken": "fixture"}, id="access-token-camel"),
        pytest.param({"access_token": "fixture"}, id="access-token-snake"),
        pytest.param({"access-token": "fixture"}, id="access-token-kebab"),
        pytest.param({"refreshToken": "fixture"}, id="refresh-token-camel"),
        pytest.param({"clientSecret": "fixture"}, id="client-secret-camel"),
        pytest.param({"providerKey": "fixture"}, id="provider-key-camel"),
        pytest.param({"providerToken": "fixture"}, id="provider-token"),
        pytest.param({"authToken": "fixture"}, id="auth-token"),
        pytest.param({"secret": "fixture"}, id="secret"),
        pytest.param({"password": "fixture"}, id="password"),
        pytest.param({"authorization": "fixture"}, id="authorization"),
        pytest.param({"nested": {"providerToken": "fixture"}}, id="nested-provider-token"),
        pytest.param({"nested": {"clientSecret": "fixture"}}, id="nested-client-secret"),
        pytest.param({"nested": [{"authToken": "fixture"}]}, id="nested-list-auth-token"),
        pytest.param({"nested": [{"provider-key": "fixture"}]}, id="nested-list-provider-key"),
        pytest.param({"apiKey": "fixture"}, id="api-key-camel"),
        pytest.param({"slackToken": "fixture"}, id="slack-token-camel"),
        pytest.param({"sessionToken": "fixture"}, id="session-token-camel"),
        pytest.param({"secretKey": "fixture"}, id="secret-key-camel"),
        pytest.param({"authorizationBasic": "fixture"}, id="authorization-basic-camel"),
        pytest.param({"nested": {"apiKey": "fixture"}}, id="nested-api-key"),
        pytest.param({"nested": [{"sessionToken": "fixture"}]}, id="nested-list-session-token"),
        pytest.param(
            {"nested": [{"authorizationBasic": "fixture"}]},
            id="nested-list-authorization-basic",
        ),
        pytest.param({"apikey": "fixture"}, id="collapsed-api-key"),
        pytest.param({"accesstoken": "fixture"}, id="collapsed-access-token"),
        pytest.param({"clientsecret": "fixture"}, id="collapsed-client-secret"),
        pytest.param({"providerkey": "fixture"}, id="collapsed-provider-key"),
        pytest.param({"authtoken": "fixture"}, id="collapsed-auth-token"),
        pytest.param({"sessiontoken": "fixture"}, id="collapsed-session-token"),
        pytest.param({"secretkey": "fixture"}, id="collapsed-secret-key"),
        pytest.param({"authorizationbasic": "fixture"}, id="collapsed-authorization-basic"),
        pytest.param({"nested": {"apikey": "fixture"}}, id="nested-collapsed-api-key"),
        pytest.param(
            {"nested": [{"clientsecret": "fixture"}]},
            id="nested-list-collapsed-client-secret",
        ),
        *ADDITIONAL_CREDENTIAL_METADATA_CASES,
    ),
)
def test_gate2_shadow_fixture_rejects_credential_comparison_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-credential-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize("comparison_metadata", ADDITIONAL_CREDENTIAL_METADATA_CASES)
def test_gate2_shadow_fixture_report_rejects_credential_comparison_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(
            _valid_gate2_report_payload(comparison_metadata)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("turnId", "bot-1234567890", id="turn-id-bot-id"),
        pytest.param("turnId", "production", id="turn-id-production-marker"),
        pytest.param("turnId", "https://example.com/turn", id="turn-id-url"),
        pytest.param("turnId", "session key production-key", id="turn-id-session-key"),
        pytest.param(
            "projectedAdkEventIds",
            ("evt-ok", "telegram"),
            id="event-id-telegram",
        ),
        pytest.param(
            "projectedAdkEventIds",
            ("https://example.com/event",),
            id="event-id-url",
        ),
        pytest.param(
            "projectedAdkEventIds",
            ("session key production-key",),
            id="event-id-session-key",
        ),
        pytest.param(
            "transcriptRefs",
            ("production",),
            id="transcript-ref-production-marker",
        ),
        pytest.param(
            "transcriptRefs",
            ("gate1/production.jsonl",),
            id="transcript-ref-production-file",
        ),
        pytest.param(
            "transcriptRefs",
            ("gate1/../../secret.jsonl",),
            id="transcript-ref-traversal",
        ),
        pytest.param(
            "transcriptRefs",
            ("https://example.com/transcript.jsonl",),
            id="transcript-ref-url",
        ),
        pytest.param(
            "transcriptRefs",
            ("gate1/runtime_selector.jsonl",),
            id="transcript-ref-runtime-selector",
        ),
        pytest.param("sseRefs", ("wss://example.com/events",), id="sse-ref-url"),
        pytest.param("sseRefs", ("production",), id="sse-ref-production-marker"),
        pytest.param("sseRefs", ("gate1/production.sse",), id="sse-ref-production-file"),
        pytest.param(
            "sseRefs",
            ("gate1/telegram.sse",),
            id="sse-ref-telegram",
        ),
        pytest.param(
            "sseRefs",
            ("gate1/session key production-key.sse",),
            id="sse-ref-session-key",
        ),
    ),
)
def test_gate2_shadow_fixture_report_rejects_production_looking_report_fields(
    field: str,
    value: object,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("turnId", "signedExternalAck", id="turn-id-signed-external-ack"),
        pytest.param(
            "turnId",
            "signedExternalAckIngestion",
            id="turn-id-signed-external-ack-ingestion",
        ),
        pytest.param("turnId", "typescriptRuntime", id="turn-id-typescript-runtime"),
        pytest.param("turnId", "TypeScriptRuntime", id="turn-id-typescript-runtime-brand-camel"),
        pytest.param("turnId", "APIProxy", id="turn-id-api-proxy-acronym-camel"),
        pytest.param("turnId", "DBProvisioning", id="turn-id-db-provisioning-acronym-camel"),
        pytest.param("turnId", "liveMissionCreation", id="turn-id-live-mission-creation"),
        pytest.param("turnId", "backgroundResume", id="turn-id-background-resume-camel"),
        pytest.param("turnId", "background_resume", id="turn-id-background-resume-snake"),
        pytest.param("turnId", "backgroundRun", id="turn-id-background-run-camel"),
        pytest.param(
            "turnId",
            "backgroundTaskResume",
            id="turn-id-background-task-resume-camel",
        ),
        pytest.param("turnId", "schedulerRun", id="turn-id-scheduler-run"),
        pytest.param("turnId", "workspaceAdoption", id="turn-id-workspace-adoption"),
        pytest.param(
            "projectedAdkEventIds",
            ("evt-signedExternalAck",),
            id="event-id-signed-external-ack",
        ),
        pytest.param(
            "transcriptRefs",
            ("gate1/typescriptRuntime.jsonl",),
            id="transcript-ref-typescript-runtime",
        ),
        pytest.param(
            "transcriptRefs",
            ("gate1/liveMissionCreation.jsonl",),
            id="transcript-ref-live-mission-creation",
        ),
        pytest.param(
            "sseRefs",
            ("gate1/schedulerRun.sse",),
            id="sse-ref-scheduler-run",
        ),
        pytest.param(
            "sseRefs",
            ("gate1/workspaceAdoption.sse",),
            id="sse-ref-workspace-adoption",
        ),
        pytest.param(
            "sseRefs",
            ("gate1/backgroundTaskResume.sse",),
            id="sse-ref-background-task-resume",
        ),
        pytest.param(
            "comparisonMetadata",
            {"backgroundResume": False},
            id="metadata-background-resume-key",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "background_task_resume disabled"},
            id="metadata-background-task-resume-value",
        ),
    ),
)
def test_gate2_shadow_fixture_report_rejects_hard_boundary_aliases_in_fields(
    field: str,
    value: object,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COLLAPSED_BACKGROUND_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
        "comparisonMetadataKey",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_report_rejects_collapsed_background_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{boundary_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{boundary_value}.sse",)
    elif field == "comparisonMetadataKey":
        payload["comparisonMetadata"] = {boundary_value: False}
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": f"{boundary_value} disabled"}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COMPACT_LIVE_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "projectedAdkEventIds",
        "comparisonMetadataKey",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_report_rejects_compact_live_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "comparisonMetadataKey":
        payload["comparisonMetadata"] = {boundary_value: False}
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": f"{boundary_value} disabled"}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COMPACT_LIVE_SUFFIX_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_report_rejects_suffixed_compact_live_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload = _valid_gate2_report_payload({"note": "fixture-only diagnostic comparison"})
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{boundary_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{boundary_value}.sse",)
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": boundary_value}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureReport.model_validate(payload)


@pytest.mark.parametrize(
    "mutated_value",
    (
        pytest.param({"accessToken"}, id="set-credential-key"),
        pytest.param(frozenset({"refreshToken"}), id="frozenset-credential-key"),
        pytest.param({"productionRoute"}, id="set-live-surface"),
        pytest.param(frozenset({"trafficAttached"}), id="frozenset-live-surface"),
    ),
)
def test_gate2_shadow_fixture_runner_rejects_mutated_set_metadata_before_report_output(
    mutated_value: object,
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-mutated-set-metadata",
            "userPrompt": "hello",
            "comparisonMetadata": {"note": "fixture-only diagnostic comparison"},
        }
    )
    fixture.comparison_metadata["mutated"] = mutated_value

    with pytest.raises(ValidationError):
        run_gate2_shadow_fixture(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("comparisonMetadata", {"productionRoute": "attached"}),
        ("comparisonMetadata", {"production-route": "attached"}),
        ("comparisonMetadata", {"liveCapture": "enabled"}),
        ("comparisonMetadata", {"runtimeSelector": "prod"}),
        ("comparisonMetadata", {"canaryAttached": False}),
        ("comparisonMetadata", {"production-attached": False}),
        ("comparisonMetadata", {"route_attached": False}),
        ("comparisonMetadata", {"trafficAttached": False}),
        ("outputFlags", {"production-attached": False}),
    ),
)
def test_gate2_shadow_fixture_rejects_normalized_attachment_and_live_surface_keys(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-safe",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_rejects_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-compact-output-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize(
    "comparison_metadata",
    COMPOSED_COMPACT_OUTPUT_ATTACHMENT_METADATA_CASES,
)
def test_gate2_shadow_fixture_rejects_composed_compact_output_attachment_metadata_keys(
    comparison_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-composed-compact-output-metadata",
                "userPrompt": "hello",
                "comparisonMetadata": comparison_metadata,
            }
        )


@pytest.mark.parametrize("unsafe_value", COMPACT_OUTPUT_ATTACHMENT_STRING_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "userPrompt",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
    ),
)
def test_gate2_shadow_fixture_rejects_compact_output_aliases_in_string_fields(
    field: str,
    unsafe_value: str,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-compact-output-string-field",
        "userPrompt": "hello",
    }
    if field == "turnId":
        payload["turnId"] = unsafe_value
    elif field == "userPrompt":
        payload["userPrompt"] = unsafe_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{unsafe_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{unsafe_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{unsafe_value}.sse",)
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    "output_flags",
    (
        {"userVisible": False},
        {"productionTranscriptAppend": False},
        {"networkSse": False},
        {"routeAttached": False},
        {"trafficAttached": False},
        {"canaryAttached": False},
        {"productionAttached": False},
    ),
)
def test_gate2_shadow_fixture_accepts_strict_false_only_output_flags(
    output_flags: dict[str, bool],
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-false-flags",
            "userPrompt": "hello",
            "outputFlags": output_flags,
        }
    )

    assert fixture.output_flag_claims == Gate2ShadowOutputFlags.model_validate(output_flags)


def test_gate2_shadow_fixture_input_model_copy_revalidates_source() -> None:
    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-copy",
        userPrompt="hello",
    )

    with pytest.raises(ValidationError):
        fixture.model_copy(update={"source": "live_capture"})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("userPrompt", "Cookie: sessionid=secret-cookie"),
        ("userPrompt", "Authorization Basic"),
        ("userPrompt", "Authorization: Basic dXNlcjpwYXNz"),
        ("userPrompt", "Authorization Basic dXNlcjpwYXNz"),
        ("userPrompt", "SUPABASE_SERVICE_ROLE_KEY=raw-service-role"),
        ("userPrompt", "service_role=raw-service-role"),
        ("userPrompt", "-----BEGIN PRIVATE KEY-----"),
        ("userPrompt", "-----BEGIN RSA PRIVATE KEY-----"),
        ("userPrompt", "xoxb-1234567890-secret"),
        ("userPrompt", "clawy.pro/api/shadow"),
        ("userPrompt", "evidenceBlockMode enabled"),
        ("userPrompt", "blockFinalAnswer enabled"),
        ("userPrompt", "customExtractor enabled"),
        ("userPrompt", "signedExternalAcknowledgement accepted"),
        ("userPrompt", "childExecution enabled"),
        ("userPrompt", "workspaceMutation enabled"),
        ("userPrompt", "schedulerResume enabled"),
        ("userPrompt", "backgroundResume enabled"),
        ("userPrompt", "background_resume enabled"),
        ("userPrompt", "backgroundRun enabled"),
        ("userPrompt", "backgroundTaskResume enabled"),
        ("comparisonMetadata", {"Cookie": "sid=secret-cookie"}),
        ("comparisonMetadata", {"authorization": "Basic dXNlcjpwYXNz"}),
        ("comparisonMetadata", {"nested": {"serviceRole": "raw-service-role"}}),
        ("comparisonMetadata", {"privateKey": "-----BEGIN PRIVATE KEY-----"}),
        ("comparisonMetadata", {"slackToken": "xoxb-1234567890-secret"}),
        ("comparisonMetadata", {"host": "clawy.pro"}),
        ("comparisonMetadata", {"evidenceBlockMode": False}),
        ("comparisonMetadata", {"blockFinalAnswer": False}),
        ("comparisonMetadata", {"customExtractor": "disabled"}),
        ("comparisonMetadata", {"signedExternalAcknowledgement": "fixture"}),
        ("comparisonMetadata", {"childExecution": False}),
        ("comparisonMetadata", {"workspaceMutation": False}),
        ("comparisonMetadata", {"schedulerResume": False}),
        ("comparisonMetadata", {"backgroundResume": False}),
        ("comparisonMetadata", {"background_run": False}),
        ("comparisonMetadata", {"note": "background_task_resume disabled"}),
    ),
)
def test_gate2_shadow_fixture_rejects_secret_and_out_of_scope_strings(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-secret-surface",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COLLAPSED_BACKGROUND_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "userPrompt",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
        "comparisonMetadataKey",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_rejects_collapsed_background_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-collapsed-background-boundary",
        "userPrompt": "hello",
    }
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "userPrompt":
        payload["userPrompt"] = f"{boundary_value} disabled"
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{boundary_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{boundary_value}.sse",)
    elif field == "comparisonMetadataKey":
        payload["comparisonMetadata"] = {boundary_value: False}
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": f"{boundary_value} disabled"}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COMPACT_LIVE_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "userPrompt",
        "projectedAdkEventIds",
        "comparisonMetadataKey",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_rejects_compact_live_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-compact-live-boundary",
        "userPrompt": "hello",
    }
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "userPrompt":
        payload["userPrompt"] = f"{boundary_value} disabled"
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "comparisonMetadataKey":
        payload["comparisonMetadata"] = {boundary_value: False}
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": f"{boundary_value} disabled"}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize("boundary_value", COMPACT_LIVE_SUFFIX_BOUNDARY_VALUES)
@pytest.mark.parametrize(
    "field",
    (
        "turnId",
        "userPrompt",
        "projectedAdkEventIds",
        "transcriptRefs",
        "sseRefs",
        "comparisonMetadataValue",
    ),
)
def test_gate2_shadow_fixture_rejects_suffixed_compact_live_boundary_aliases_in_fields(
    field: str,
    boundary_value: str,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-suffixed-compact-live-boundary",
        "userPrompt": "hello",
    }
    if field == "turnId":
        payload["turnId"] = boundary_value
    elif field == "userPrompt":
        payload["userPrompt"] = boundary_value
    elif field == "projectedAdkEventIds":
        payload["projectedAdkEventIds"] = (f"evt-{boundary_value}",)
    elif field == "transcriptRefs":
        payload["transcriptRefs"] = (f"gate1/{boundary_value}.jsonl",)
    elif field == "sseRefs":
        payload["sseRefs"] = (f"gate1/{boundary_value}.sse",)
    elif field == "comparisonMetadataValue":
        payload["comparisonMetadata"] = {"note": boundary_value}
    else:
        raise AssertionError(f"unexpected field: {field}")

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("token budget", id="token-budget"),
        pytest.param("secret plan", id="secret-plan"),
        pytest.param("API compatibility fixture", id="api-compatibility-fixture"),
        pytest.param("database schema fixture", id="database-schema-fixture"),
    ),
)
def test_gate2_shadow_fixture_accepts_benign_local_fixture_phrases(
    value: str,
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-benign-fixture-text",
            "userPrompt": value,
            "comparisonMetadata": {"note": value},
        }
    )

    assert fixture.user_prompt == value
    assert fixture.comparison_metadata == {"note": value}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param(
            "userPrompt",
            "AIzaSyD-fixtureCredential1234567890abcdef",
            id="user-google-api-key",
        ),
        pytest.param(
            "userPrompt",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.signaturePart",
            id="user-jwt-token",
        ),
        pytest.param(
            "userPrompt",
            "xoxp-1234567890-1234567890-abcdef",
            id="user-slack-user-token",
        ),
        pytest.param(
            "userPrompt",
            "xoxa-2-1234567890-1234567890-abcdef",
            id="user-slack-app-token",
        ),
        pytest.param(
            "userPrompt",
            "xoxr-1234567890-abcdef",
            id="user-slack-refresh-token",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "AIzaSyD-fixtureCredential1234567890abcdef"},
            id="metadata-google-api-key",
        ),
        pytest.param(
            "comparisonMetadata",
            {
                "nested": {
                    "fixtureValue": (
                        "eyJhbGciOiJIUzI1NiJ9."
                        "eyJzdWIiOiJmaXh0dXJlIn0.signaturePart"
                    )
                }
            },
            id="metadata-jwt-token",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "xoxp-1234567890-1234567890-abcdef"},
            id="metadata-slack-user-token",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "xoxa-2-1234567890-1234567890-abcdef"},
            id="metadata-slack-app-token",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "xoxr-1234567890-abcdef"},
            id="metadata-slack-refresh-token",
        ),
    ),
)
def test_gate2_shadow_fixture_rejects_live_credential_value_shapes(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-live-credential-value",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("userPrompt", "github_pat_1234567890abcdef", id="user-github-pat"),
        pytest.param("userPrompt", "access key production-key", id="user-access-key-space"),
        pytest.param(
            "userPrompt",
            "awsAccessKeyId production-key",
            id="user-aws-access-key-id-camel-space",
        ),
        pytest.param(
            "userPrompt",
            "aws_access_key_id production-key",
            id="user-aws-access-key-id-snake-space",
        ),
        pytest.param(
            "userPrompt",
            "aws-access-key-id production-key",
            id="user-aws-access-key-id-kebab-space",
        ),
        pytest.param(
            "userPrompt",
            "awsaccesskeyid production-key",
            id="user-aws-access-key-id-collapsed-space",
        ),
        pytest.param("userPrompt", "basic auth production-basic", id="user-basic-auth-space"),
        pytest.param("userPrompt", "client secret production-secret", id="user-client-secret-space"),
        pytest.param("userPrompt", "provider key production-key", id="user-provider-key-space"),
        pytest.param("userPrompt", "service key production-key", id="user-service-key-space"),
        pytest.param("userPrompt", "session key production-key", id="user-session-key-space"),
        pytest.param("userPrompt", "token production-token", id="user-token-space"),
        pytest.param("userPrompt", "password production-password", id="user-password-space"),
        pytest.param("userPrompt", "secret production-secret", id="user-secret-space"),
        pytest.param(
            "comparisonMetadata",
            {"note": "github_pat_1234567890abcdef"},
            id="metadata-github-pat",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "access key production-key"},
            id="metadata-access-key-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "awsAccessKeyId production-key"},
            id="metadata-aws-access-key-id-camel-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "aws_access_key_id production-key"},
            id="metadata-aws-access-key-id-snake-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "aws-access-key-id production-key"},
            id="metadata-aws-access-key-id-kebab-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "awsaccesskeyid production-key"},
            id="metadata-aws-access-key-id-collapsed-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "basic auth production-basic"},
            id="metadata-basic-auth-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "client secret production-secret"},
            id="metadata-client-secret-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "provider key production-key"},
            id="metadata-provider-key-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "service key production-key"},
            id="metadata-service-key-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "session key production-key"},
            id="metadata-session-key-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "token production-token"},
            id="metadata-token-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "password production-password"},
            id="metadata-password-space",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "secret production-secret"},
            id="metadata-secret-space",
        ),
    ),
)
def test_gate2_shadow_fixture_rejects_space_separated_credential_shaped_strings(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-space-separated-secret",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("userPrompt", "sk_live_1234567890abcdef", id="user-stripe-secret"),
        pytest.param("userPrompt", "rk_live_1234567890abcdef", id="user-stripe-restricted"),
        pytest.param("userPrompt", "whsec_1234567890abcdef", id="user-stripe-webhook"),
        pytest.param(
            "comparisonMetadata",
            {"note": "sk_live_1234567890abcdef"},
            id="metadata-stripe-secret",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "rk_live_1234567890abcdef"},
            id="metadata-stripe-restricted",
        ),
        pytest.param(
            "comparisonMetadata",
            {"note": "whsec_1234567890abcdef"},
            id="metadata-stripe-webhook",
        ),
    ),
)
def test_gate2_shadow_fixture_rejects_stripe_style_secret_prefixes(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-stripe-secret-prefix",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("task-sk-fixture", id="task-sk-middle"),
        pytest.param("risk-sk-fixture", id="risk-sk-middle"),
        pytest.param("ask-sk-fixture", id="ask-sk-middle"),
        pytest.param("desk-sk-fixture", id="desk-sk-middle"),
        pytest.param("task-", id="task-ending-dash"),
        pytest.param("risk-", id="risk-ending-dash"),
        pytest.param("ask-", id="ask-ending-dash"),
        pytest.param("desk-", id="desk-ending-dash"),
    ),
)
def test_gate2_shadow_fixture_accepts_benign_words_containing_sk_dash(
    value: str,
) -> None:
    fixture = Gate2ShadowFixtureInput.model_validate(
        {
            "source": "synthetic_local",
            "turnId": "gate2-turn-benign-sk",
            "userPrompt": value,
            "comparisonMetadata": {"note": value},
        }
    )

    assert fixture.user_prompt == value
    assert fixture.comparison_metadata == {"note": value}


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("sk-1234567890abcdef", id="bare-sk-token"),
        pytest.param("prefix sk-1234567890abcdef suffix", id="embedded-bare-sk-token"),
    ),
)
def test_gate2_shadow_fixture_rejects_token_like_bare_sk_dash_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(
            {
                "source": "synthetic_local",
                "turnId": "gate2-turn-real-sk-token",
                "userPrompt": value,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("turnId", "bot-1234567890"),
        ("userPrompt", "/data/workspace/agent-pvc/session"),
        ("userPrompt", "../workspace/file.txt"),
        ("userPrompt", "/Users/kevin/secrets.txt"),
        ("userPrompt", "local absolute path /etc/passwd"),
        ("userPrompt", "see C:\\Users\\kevin\\secret.txt"),
        ("userPrompt", "log at C:/prod/workspace/file"),
        ("userPrompt", "https://clawy.pro/api/proxy/chat"),
        ("userPrompt", "see https://example.com/path"),
        ("userPrompt", "stream wss://example.com/events"),
        ("userPrompt", "database postgresql://user:pass@localhost/db"),
        ("userPrompt", "db fixture"),
        ("userPrompt", "provisioning fixture"),
        ("userPrompt", "plain supabase fixture"),
        ("userPrompt", "local file://tmp/fixture.json"),
        ("userPrompt", "project supabase://project-ref"),
        ("userPrompt", "Bearer production-token"),
        ("userPrompt", "ghp_1234567890abcdef"),
        ("userPrompt", "sk-1234567890abcdef"),
        ("userPrompt", "api_key=production-key"),
        ("userPrompt", "access key: production-key"),
        ("userPrompt", "accessKey=production-key"),
        ("userPrompt", "access_key=production-key"),
        ("userPrompt", "awsAccessKeyId=production-key"),
        ("userPrompt", "aws_access_key_id=production-key"),
        ("userPrompt", "awsaccesskeyid=production-key"),
        ("userPrompt", "aws access key id: production-key"),
        ("userPrompt", "clientSecret=production-secret"),
        ("userPrompt", "refreshToken=production-token"),
        ("userPrompt", "authorizationBasic=production-basic"),
        ("userPrompt", "basic auth: production-basic"),
        ("userPrompt", "basicAuth=production-basic"),
        ("userPrompt", "credentials: production-credentials"),
        ("userPrompt", "providerKey=production-key"),
        ("userPrompt", "provider key: production-key"),
        ("userPrompt", "credentialKey=production-key"),
        ("userPrompt", "credentialsKey=production-key"),
        ("userPrompt", "serviceKey=production-key"),
        ("userPrompt", "sessionKey=production-key"),
        ("userPrompt", "token=production-token"),
        ("userPrompt", "password=production-password"),
        ("userPrompt", "secret=production-secret"),
        ("userPrompt", "api_key: production-key"),
        ("userPrompt", "accessKey: production-key"),
        ("userPrompt", "access_key: production-key"),
        ("userPrompt", "clientSecret: production-secret"),
        ("userPrompt", "refreshToken: production-token"),
        ("userPrompt", "authorizationBasic: production-basic"),
        ("userPrompt", "basicAuth: production-basic"),
        ("userPrompt", "token: production-token"),
        ("userPrompt", "password: production-password"),
        ("userPrompt", "secret: production-secret"),
        ("projectedAdkEventIds", ("evt-ok", "telegram")),
        ("transcriptRefs", ("gate1/../../secret.jsonl",)),
        ("sseRefs", ("gate1/runtime_selector.sse",)),
        ("comparisonMetadata", {"ref": "production_route"}),
        ("comparisonMetadata", {"production_route": "ref"}),
        ("comparisonMetadata", {"nested": {"api_key=production-key": "ref"}}),
        ("comparisonMetadata", {"note": "accessKey=production-key"}),
        ("comparisonMetadata", {"note": "access_key=production-key"}),
        ("comparisonMetadata", {"note": "access key: production-key"}),
        ("comparisonMetadata", {"note": "awsAccessKeyId=production-key"}),
        ("comparisonMetadata", {"note": "aws_access_key_id=production-key"}),
        ("comparisonMetadata", {"note": "awsaccesskeyid=production-key"}),
        ("comparisonMetadata", {"note": "aws access key id: production-key"}),
        ("comparisonMetadata", {"note": "clientSecret=production-secret"}),
        ("comparisonMetadata", {"note": "refreshToken=production-token"}),
        ("comparisonMetadata", {"note": "authorizationBasic=production-basic"}),
        ("comparisonMetadata", {"note": "basicAuth=production-basic"}),
        ("comparisonMetadata", {"note": "basic auth: production-basic"}),
        ("comparisonMetadata", {"note": "credentials: production-credentials"}),
        ("comparisonMetadata", {"note": "providerKey=production-key"}),
        ("comparisonMetadata", {"note": "provider key: production-key"}),
        ("comparisonMetadata", {"note": "credentialKey=production-key"}),
        ("comparisonMetadata", {"note": "credentialsKey=production-key"}),
        ("comparisonMetadata", {"note": "serviceKey=production-key"}),
        ("comparisonMetadata", {"note": "sessionKey=production-key"}),
        ("outputFlags", {"note": "live_capture"}),
        ("outputFlags", {"telegram": "claimed"}),
        ("outputFlags", {"userVisible": True}),
        ("outputFlags", {"unexpectedFlag": False}),
    ),
)
def test_gate2_shadow_fixture_rejects_production_looking_content(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "source": "synthetic_local",
        "turnId": "gate2-turn-safe",
        "userPrompt": "hello",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        Gate2ShadowFixtureInput.model_validate(payload)


def test_gate2_shadow_fixture_restores_local_runner_env_before_async_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openmagi_core_agent.adk_bridge import local_runner
    from openmagi_core_agent.adk_bridge.runner_adapter import RunnerTurnInput
    from openmagi_core_agent.shadow import fixture_runner

    adapter_calls: list[object] = []

    class DummySessionService:
        async def create_session(self, **_: object) -> None:
            assert os.environ.get("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER") is None

    class DummyAdapter:
        def __init__(self, *, runner: object) -> None:
            self.runner = runner

        async def collect_events(self, turn_input: object) -> list[object]:
            assert os.environ.get("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER") is None
            assert isinstance(turn_input, RunnerTurnInput)
            assert turn_input.harness_state.run_on == "main"
            assert turn_input.harness_state.spawn_depth == 0
            adapter_calls.append(self.runner)
            return []

    def build_dummy_bundle() -> object:
        assert os.environ.get("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER") == "1"
        return SimpleNamespace(
            runner=SimpleNamespace(app_name="gate2-shadow-local"),
            session_service=DummySessionService(),
        )

    monkeypatch.delenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", raising=False)
    monkeypatch.setattr(local_runner, "build_local_adk_runner", build_dummy_bundle)
    monkeypatch.setattr(fixture_runner, "OpenMagiRunnerAdapter", DummyAdapter)

    fixture = Gate2ShadowFixtureInput(
        source="synthetic_local",
        turnId="gate2-turn-env-restore",
        userPrompt="hello",
    )

    report = asyncio.run(run_gate2_shadow_fixture_async(fixture))

    assert report.comparison_metadata["localRunnerStatus"] == "completed:0"
    assert os.environ.get("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER") is None
    assert [getattr(runner, "app_name", None) for runner in adapter_calls] == [
        "gate2-shadow-local"
    ]


def test_gate2_shadow_module_import_boundary_stays_route_infra_and_ts_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.shadow.fixture_runner")
assert hasattr(module, "run_gate2_shadow_fixture")

forbidden_prefixes = (
    "openmagi_core_agent.api",
    "openmagi_core_agent.app",
    "openmagi_core_agent.dashboard",
    "openmagi_core_agent.database",
    "openmagi_core_agent.db",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.supabase",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.api",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.transport.plugins",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.web",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "openmagi_core_agent.proxy",
    "openmagi_core_agent.provisioning",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.telegram",
    "openmagi_core_agent.runtime_selector",
    "src.",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(module_name == prefix or module_name.startswith(prefix) for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"shadow fixture runner loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_gate2_shadow_fixture_run_in_fresh_process_does_not_load_forbidden_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import sys
from pathlib import Path

from openmagi_core_agent.shadow.fixture_runner import (
    load_gate2_shadow_fixture,
    run_gate2_shadow_fixture,
)

fixture = load_gate2_shadow_fixture(Path({str(FIXTURES / "synthetic_text_turn.json")!r}))
report = run_gate2_shadow_fixture(fixture, base_fixture_dir=Path({str(FIXTURES.parent)!r}))
assert report.projected_adk_event_ids == ("evt-text-partial", "evt-text-final")

forbidden = (
    "openmagi_core_agent.evidence",
    "openmagi_core_agent.harness.audit",
    "openmagi_core_agent.harness.engine",
    "openmagi_core_agent.hooks",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.app",
    "openmagi_core_agent.main",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.api",
    "openmagi_core_agent.transport.plugins",
    "openmagi_core_agent.transport.tools",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"forbidden production modules loaded: {{loaded}}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
