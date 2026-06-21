from __future__ import annotations

import importlib
import json
import sys

import pytest
from pydantic import ValidationError

from magi_agent.ops import (
    RuntimeMetricRecord,
    RuntimeMetricsSnapshot,
    RuntimeOperationEvent,
    RuntimeOpsAttachmentFlags,
    build_runtime_metrics_snapshot,
    default_runtime_ops_health_metadata,
    safe_metadata,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _event(**overrides: object) -> RuntimeOperationEvent:
    payload = {
        "eventId": "event-001",
        "traceId": "trace-001",
        "operationId": "operation-001",
        "sequence": 0,
        "eventType": "tool_observed",
        "status": "accepted",
        "policySnapshotDigest": _digest("1"),
        "ledgerHeadDigest": _digest("2"),
        "contextProjectionDigest": _digest("3"),
        "metadata": {
            "toolRef": "FileRead",
            "durationMs": 12,
            "sourceDigest": _digest("4"),
        },
    }
    payload.update(overrides)
    return RuntimeOperationEvent(**payload)


@pytest.mark.parametrize(
    "metadata",
    (
        {"rawPrompt": "sample-ref"},
        {"hiddenReasoning": "reason-ref"},
        {"rawToolOutput": "tool-output-ref"},
        {"authHeaderRef": "header-ref"},
        {"cookieRef": "cookie-ref"},
        {"privatePathRef": "path-ref"},
        {"toolOutputDigest": _digest("5")},
        {"nested": {"arg": "value"}},
    ),
)
def test_runtime_operation_event_rejects_forbidden_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(metadata=metadata)


def test_runtime_operation_event_rejects_private_values_without_echoing_them() -> None:
    rejected_values = (
        "/Users/example/.ssh/id_rsa",
        "bearer:abcd1234",
        "raw.prompt.ref",
        "raw.output.ref",
        "hidden.reasoning.ref",
        "private.path.ref",
        "private.ref",
        "private:ref",
        "private.key",
        "private:key",
        "tool.output.ref",
        "credential.ref",
        "token.ref",
        "secret.value.ref",
        "api.key.ref",
        "password.ref",
        "trace:.env",
        "trace:.ssh",
        "op:.kube",
        "operation:.config",
        "sk-" + "a" * 32,
        "ghp_" + "b" * 32,
        "AKIA" + "C" * 16,
    )

    for rejected in rejected_values:
        with pytest.raises(ValidationError) as exc_info:
            _event(metadata={"diagnosticRef": rejected})
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("rejected", ("private.ref", "trace:.env"))
def test_metric_validation_errors_do_not_echo_private_inputs(rejected: str) -> None:
    probes = (
        lambda: RuntimeMetricRecord(
            metricName="ops.event.accepted",
            value=1,
            unit="count",
            traceDigest=_digest("0"),
            policySnapshotDigest=_digest("1"),
            dimensions={"diagnosticRef": rejected},
        ),
        lambda: RuntimeMetricsSnapshot(counts={rejected: 1}),
        lambda: RuntimeMetricsSnapshot(eventTypeCounts={rejected: 1}),
    )

    for probe in probes:
        with pytest.raises(ValidationError) as exc_info:
            probe()
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("rejected", ("private.ref", "private/ref", "trace:.env"))
def test_ops_validation_error_locations_do_not_echo_private_extra_keys(rejected: str) -> None:
    probes = (
        lambda: RuntimeOperationEvent(
            eventId="event-001",
            traceId="trace-001",
            operationId="operation-001",
            sequence=0,
            eventType="tool_observed",
            status="accepted",
            policySnapshotDigest=_digest("1"),
            ledgerHeadDigest=_digest("2"),
            contextProjectionDigest=_digest("3"),
            **{rejected: "x"},
        ),
        lambda: RuntimeOpsAttachmentFlags(**{rejected: "x"}),
        lambda: RuntimeMetricRecord(
            metricName="ops.event.accepted",
            value=1,
            unit="count",
            traceDigest=_digest("4"),
            policySnapshotDigest=_digest("5"),
            **{rejected: "x"},
        ),
        lambda: RuntimeMetricsSnapshot(**{rejected: "x"}),
    )

    for probe in probes:
        with pytest.raises(ValidationError) as exc_info:
            probe()
        encoded_error = json.dumps(exc_info.value.errors(), default=str)
        assert rejected not in str(exc_info.value)
        assert rejected not in encoded_error


@pytest.mark.parametrize("field_name", ("eventId", "traceId", "operationId"))
def test_runtime_operation_event_rejects_private_refs_in_public_identifiers(field_name: str) -> None:
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(**{field_name: "private.ref"})
    with pytest.raises(ValidationError, match="runtime operation validation failed"):
        _event(**{field_name: "trace:.env"})


def test_safe_metadata_is_canonical_and_digest_or_ref_only() -> None:
    clean = safe_metadata(
        {
            "zRef": "public.ref",
            "aDigest": _digest("6"),
            "attempt": 2,
            "enabled": False,
        }
    )

    assert list(clean) == ["aDigest", "attempt", "enabled", "zRef"]
    assert clean["aDigest"] == _digest("6")


def test_metric_records_and_attachment_flags_cannot_enable_live_authority() -> None:
    forged_flags = RuntimeOpsAttachmentFlags(
        liveToolExecutionAttached=True,
        productionStorageAttached=True,
        rawPromptAttached=True,
    )
    metric = RuntimeMetricRecord(
        metricName="ops.event.accepted",
        value=1,
        unit="count",
        traceDigest=_digest("7"),
        policySnapshotDigest=_digest("8"),
        dimensions={"status": "accepted"},
        attachmentFlags=forged_flags,
    )
    public = metric.public_projection()

    assert public["attachmentFlags"]["liveToolExecutionAttached"] is False
    assert public["attachmentFlags"]["productionStorageAttached"] is False
    assert public["attachmentFlags"]["promptPayloadAttached"] is False
    assert "rawPromptAttached" not in json.dumps(public, sort_keys=True)
    # C-4 PR-I raise-to-coerce: model_copy/copy/model_construct route through
    # model_validate (kernel) -- forged Literal[False] assertions are coerced
    # back to False instead of raising. The force-false invariant is preserved.
    copied = forged_flags.model_copy(update={"liveToolExecutionAttached": True})
    assert copied.live_tool_execution_attached is False
    copied_alt = forged_flags.copy(update={"liveToolExecutionAttached": True})
    assert copied_alt.live_tool_execution_attached is False
    constructed = RuntimeOpsAttachmentFlags.model_construct(liveToolExecutionAttached=True)
    assert constructed.live_tool_execution_attached is False


def test_metric_models_cannot_be_constructed_or_copied_into_authority() -> None:
    metric = RuntimeMetricRecord(
        metricName="ops.event.accepted",
        value=1,
        unit="count",
        traceDigest=_digest("9"),
        policySnapshotDigest=_digest("a"),
        dimensions={"status": "accepted"},
    )
    snapshot = RuntimeMetricsSnapshot(
        counts={"accepted": 1},
        eventTypeCounts={"tool_observed": 1},
        metricRecords=(metric,),
    )

    # C-4 PR-I: model_copy / copy / model_construct now route through
    # model_validate (kernel). The "source" field is a Literal so pydantic
    # rejects the forged value with a ValidationError (not the old
    # ValueError("model_copy update is disabled") gate). The class-level
    # `_MetricsModel` sanitize wrapper passes the ValidationError through.
    with pytest.raises(ValidationError):
        metric.model_copy(update={"source": "remote"})
    with pytest.raises(ValidationError):
        metric.copy(update={"source": "remote"})
    with pytest.raises(ValidationError):
        RuntimeMetricRecord.model_construct(source="remote")
    # `runtimeOperationsEnabled` is a plain bool (not Literal[False]); the
    # kernel does not pin it, so an explicit update is now accepted. The
    # original "all-fields-immutable" raise was the pre-kernel _JobQueueModel
    # blanket gate; the kernel narrows the contract to force-false-only.
    flipped = snapshot.model_copy(update={"runtimeOperationsEnabled": True})
    assert flipped.runtime_operations_enabled is True
    # Counts get validated via `_validate_counts` (rejects private refs), so
    # the bad-counts payload still fails -- but with ValidationError now.
    with pytest.raises(ValidationError):
        snapshot.copy(update={"runtime_operations_enabled": True, "counts": {"private.ref": 1}})
    with pytest.raises(ValidationError):
        RuntimeMetricsSnapshot.model_construct(source="remote")


def test_runtime_operation_event_cannot_be_constructed_or_copied_into_authority() -> None:
    event = _event()
    # C-4 PR-I: model_copy / copy / model_construct route through model_validate.
    # `activationEnabled` (Literal[False]) is coerced back to False by the kernel
    # _force_false validator, not raised as a "model_copy update is disabled"
    # gate -- but the force-false invariant survives identically.
    copied = event.model_copy(update={"activationEnabled": True})
    assert copied.activation_enabled is False
    # `event_id` is a string with require_safe_ref validation; the forged value
    # is rejected by the field_validator (ValidationError now, not the
    # _JobQueueModel-style "copy update is disabled" gate).
    with pytest.raises(ValidationError):
        event.copy(update={"event_id": "private.ref"})
    # model_construct now routes through model_validate; without the required
    # `event_id` / `trace_id` / digests the validation fails (the malicious
    # `activationEnabled=True` would have been coerced to False if the rest
    # were present -- see ``test_force_false_invariant_holds_in_current_tree``
    # in the C-4 PR-A golden harness for the coerce path).
    with pytest.raises(ValidationError):
        RuntimeOperationEvent.model_construct(activationEnabled=True)
    # Direct construction with the full payload + a forged `activationEnabled`
    # now coerces back to False (raise-to-coerce on Literal[False] is the
    # kernel's contract). The pre-kernel `_force_false` model_validator raised
    # via pydantic's Literal validator on True; now the kernel's
    # `_force_false` rewrites True->False BEFORE pydantic's Literal check.
    coerced_event = _event(activationEnabled=True)
    assert coerced_event.activation_enabled is False


def test_runtime_ops_health_metadata_is_default_off() -> None:
    metadata = default_runtime_ops_health_metadata()

    assert metadata["enabled"] is False
    assert metadata["liveToolExecutionAttached"] is False
    assert metadata["productionStorageAttached"] is False
    assert metadata["productionQueueAttached"] is False


# Removed dead trace-stack modules; telemetry/ is the single trace seam.
# Names are assembled so repo-wide greps for the dead modules stay clean.
@pytest.mark.parametrize(
    "removed_submodule",
    ("recorder", "traces", "contracts", "scheduler_metrics", "runtime_events"),
)
def test_dead_ops_trace_modules_are_removed(removed_submodule: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"magi_agent.ops.{removed_submodule}")


def test_runtime_operation_event_lives_in_metrics_module() -> None:
    metrics = importlib.import_module("magi_agent.ops.metrics")

    assert metrics.RuntimeOperationEvent is RuntimeOperationEvent
    assert RuntimeOperationEvent.__module__ == "magi_agent.ops.metrics"


def test_build_runtime_metrics_snapshot_aggregates_runtime_operation_events() -> None:
    events = (
        _event(eventId="event-001", sequence=0, eventType="tool_observed", status="accepted"),
        _event(eventId="event-002", sequence=1, eventType="guardrail_observed", status="rejected"),
    )

    snapshot = build_runtime_metrics_snapshot(events)
    public = snapshot.public_projection()

    assert isinstance(snapshot, RuntimeMetricsSnapshot)
    assert snapshot.runtime_operations_enabled is False
    assert snapshot.counts == {"accepted": 1, "rejected": 1}
    assert snapshot.event_type_counts == {"guardrail_observed": 1, "tool_observed": 1}
    metric_names = {record.metric_name for record in snapshot.metric_records}
    assert metric_names == {"ops.event.accepted", "ops.event.rejected"}
    assert all(record.unit == "count" for record in snapshot.metric_records)
    assert all(event.event_digest.startswith("sha256:") for event in events)
    assert public["attachmentFlags"]["liveToolExecutionAttached"] is False
    assert public["attachmentFlags"]["productionStorageAttached"] is False
    assert "rawPromptAttached" not in json.dumps(public, sort_keys=True)


def test_ops_import_boundary_does_not_load_live_runtime_paths() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("magi_agent.ops"):
            sys.modules.pop(module_name)

    before = set(sys.modules)
    importlib.import_module("magi_agent.ops.metrics")
    newly_loaded = set(sys.modules) - before
    forbidden_prefixes = (
        "google.adk.runners",
        "google.adk.models",
        "magi_agent.tools.kernel",
        "magi_agent.memory.adapters",
        "magi_agent.providers",
        "magi_agent.transport.chat",
        "magi_agent.workspace",
        "magi_agent.channels.telegram_adapter",
    )

    assert not any(name.startswith(prefix) for prefix in forbidden_prefixes for name in newly_loaded)
