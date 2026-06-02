from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent.telemetry.deterministic_events import (
    DeterministicRuntimeEvent,
    RuntimeEventType,
    project_event_for_dashboard,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def _event(**overrides: object) -> DeterministicRuntimeEvent:
    payload = {
        "eventId": "evt-001",
        "runId": "run-001",
        "workflowId": "openmagi.research.cited-market-brief",
        "stepId": "step-001",
        "eventType": "guardrail_result",
        "routeDecision": "python_selected_gate",
        "effectivePolicySnapshotDigest": "sha256:" + "1" * 64,
        "ledgerHeadDigest": "sha256:" + "2" * 64,
        "checkpointId": "checkpoint-001",
        "validatorStatuses": ("validator:quoteExactMatch=pass",),
        "approvalGateRefs": ("approval:none",),
        "repairAttempt": 0,
        "projectionMode": "structured_claims_only",
        "terminalState": None,
        "redactionStatus": "redacted",
    }
    payload.update(overrides)
    return DeterministicRuntimeEvent(**payload)


def test_runtime_event_contains_digest_refs_not_raw_private_data() -> None:
    event = _event()

    projected = project_event_for_dashboard(event)
    assert projected["effectivePolicySnapshotDigest"] == "sha256:" + "1" * 64
    assert "rawPrompt" not in projected
    assert "rawModelOutput" not in projected
    assert "metadata" not in projected


def test_runtime_event_rejects_raw_prompt_or_secret_like_metadata() -> None:
    with pytest.raises(ValidationError, match="raw prompt"):
        _event(
            eventId="evt-002",
            eventType="projection",
            effectivePolicySnapshotDigest="sha256:" + "3" * 64,
            ledgerHeadDigest="sha256:" + "4" * 64,
            checkpointId=None,
            validatorStatuses=(),
            approvalGateRefs=(),
            terminalState="block",
            metadata={"rawPrompt": "sensitive user text"},
        )


def test_runtime_event_rejects_bad_digests_protected_refs_and_coerced_numbers() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _event(effectivePolicySnapshotDigest="raw-policy")
    with pytest.raises(ValidationError, match="workflowId"):
        _event(workflowId="openmagi.to-ken.workflow")
    with pytest.raises(ValidationError, match="repairAttempt"):
        _event(repairAttempt="0")


def test_runtime_event_non_metadata_validation_errors_do_not_echo_rejected_input() -> None:
    rejected_values = {
        "workflowId": "ghp_" + "b" * 32,
        "runId": "sk-" + "a" * 32,
        "routeDecision": "sk-proj-" + "b" * 32,
        "checkpointId": "sk-ant-api03-" + "c" * 32,
        "projectionMode": "AIza" + "d" * 35,
        "terminalState": "rk_live_" + "e" * 24,
        "eventId": "/Users/example/.ssh/id_rsa",
        "effectivePolicySnapshotDigest": "rawPrompt",
        "eventType": "hiddenReasoning",
        "validatorStatuses": ("session-token-ref",),
    }

    for field_name, rejected in rejected_values.items():
        with pytest.raises(ValidationError) as exc_info:
            _event(**{field_name: rejected})
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert str(rejected) not in str(exc_info.value)
        assert str(rejected) not in encoded_error


def test_runtime_event_type_values_are_closed() -> None:
    assert set(RuntimeEventType.__args__) == {
        "route_decision",
        "context_projection",
        "model_call",
        "tool_call",
        "guardrail_result",
        "approval",
        "projection",
        "delivery",
        "checkpoint",
    }


def test_runtime_event_projection_is_sanitized_and_model_copy_update_is_disabled() -> None:
    event = _event(metadata={"safeDiagnosticDigest": "sha256:" + "5" * 64})
    projected = project_event_for_dashboard(event)

    assert projected["eventId"] == "evt-001"
    assert projected["activationEnabled"] is False
    assert "safeDiagnosticDigest" not in json.dumps(projected)
    with pytest.raises(ValueError, match="model_copy update"):
        event.model_copy(update={"metadata": {"rawPrompt": "blocked"}})
    with pytest.raises(ValueError, match="copy update"):
        event.copy(update={"metadata": {"rawPrompt": "blocked"}})
    with pytest.raises(ValidationError, match="activationEnabled"):
        _event(activationEnabled=True)
    with pytest.raises(ValueError, match="model_construct"):
        DeterministicRuntimeEvent.model_construct(activationEnabled=True)


def test_runtime_event_serialization_rejects_bypassed_or_forged_state_without_leakage() -> None:
    event = _event()
    raw_payload = "sensitive user text"

    object.__setattr__(event, "metadata", {"rawPrompt": raw_payload})
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        event.model_dump_json(by_alias=True)
    assert raw_payload not in str(exc_info.value)

    clean_event = _event()
    corrupted = BaseModel.model_copy(
        clean_event,
        update={"metadata": {"rawPrompt": raw_payload}, "activation_enabled": True},
    )
    with pytest.raises((ValidationError, ValueError)) as copied_exc_info:
        corrupted.model_dump_json(by_alias=True)
    assert raw_payload not in str(copied_exc_info.value)
    with pytest.raises((ValidationError, ValueError)) as projection_exc_info:
        project_event_for_dashboard(corrupted)
    assert raw_payload not in str(projection_exc_info.value)


def test_runtime_event_metadata_cannot_be_mutated_after_validation() -> None:
    event = _event(metadata={"safeDiagnosticDigest": "sha256:" + "5" * 64})

    with pytest.raises(TypeError):
        event.metadata["rawPrompt"] = "sensitive user text"  # type: ignore[index]
    assert "rawPrompt" not in event.model_dump(by_alias=True, mode="json")["metadata"]


def test_runtime_event_metadata_accepts_only_digest_or_ref_shaped_values() -> None:
    _event(metadata={"safeDiagnosticDigest": "sha256:" + "5" * 64, "attempt": 1, "enabled": False})
    with pytest.raises(ValidationError, match="metadata"):
        _event(metadata={"diagnostic": "complete generated answer text without marker"})


def test_runtime_event_metadata_rejects_secret_shaped_refs_without_echoing_input() -> None:
    secret_shaped_values = (
        "sk_" + "live_" + "a" * 24,
        "sk-" + "a" * 32,
        "sk-proj-" + "b" * 32,
        "sk-ant-api03-" + "c" * 32,
        "AIza" + "d" * 35,
        "rk_live_" + "e" * 24,
        "ghp_" + "b" * 32,
        "AKIA" + "C" * 16,
        "xoxb-" + "D" * 24,
        "a" * 24 + "." + "b" * 24 + "." + "c" * 24,
    )

    for value in secret_shaped_values:
        with pytest.raises(ValidationError) as exc_info:
            _event(metadata={"diagnosticRef": value})
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert value not in str(exc_info.value)
        assert value not in encoded_error


def test_runtime_event_metadata_validation_errors_do_not_echo_rejected_input() -> None:
    rejected = "complete generated answer text without marker"

    with pytest.raises(ValidationError) as exc_info:
        _event(metadata={"diagnostic": rejected})

    assert rejected not in str(exc_info.value)
    assert rejected not in json.dumps(exc_info.value.errors(), default=str)


def test_runtime_event_metadata_serialization_is_canonical() -> None:
    first = _event(metadata={"zRef": "public.ref", "aDigest": "sha256:" + "6" * 64})
    second = _event(metadata={"aDigest": "sha256:" + "6" * 64, "zRef": "public.ref"})

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert list(first.model_dump(by_alias=True, mode="json")["metadata"]) == ["aDigest", "zRef"]


def test_runtime_event_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "deterministic_event.json").read_text())
    event = DeterministicRuntimeEvent.model_validate(payload["event"])
    projected = project_event_for_dashboard(event)

    assert projected == payload["expectedDashboardProjection"]
    encoded_values = " ".join(_string_values(payload)).lower()
    forbidden_fragments = (
        "rawprompt",
        "rawmodeloutput",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
        "/users/",
        ".env",
    )
    assert all(fragment not in encoded_values for fragment in forbidden_fragments)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
