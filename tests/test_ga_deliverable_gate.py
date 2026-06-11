"""A4 — GA deliverable completion gate promoted onto the live pre-final seam.

The Track 19 PR3 task-completion verifier was dormant: its only enforcement
seam (``completion_repair_decision`` -> ``handle_stop_reason(completion_gate=...)``)
has no production caller because the live turn loop is ADK's, not
``runtime.turn_policy``. This suite covers the promotion:

* ``MAGI_GA_DELIVERABLE_GATE_ENABLED`` (strict default-OFF) promotes the
  receipt-grounded artifact deliverable check onto the engine's LIVE
  pre-final evidence gate (``_pre_final_gate_payload``), appending a clear
  ``ga_deliverable:artifactRef`` blocked-reason to ``missingEvidence``.
* With the flag ON, ``LocalToolEvidenceCollector`` keeps the spreadsheet
  write tool's ``localArtifactReceipt`` visible in its receipt projection so
  a delivered artifact satisfies (not false-blocks) the gate.
* Flag OFF keeps every payload byte-identical to main.

The snapshot half (``ENFORCE_SNAPSHOT_REQUIREMENT`` / ``requires_snapshot_ref``)
was DELETED, not promoted: no first-party recipe label ever contained
``"snapshot"`` and no production path writes a snapshot ref into any ledger
the verifier reads.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.config.env import parse_ga_deliverable_gate_enabled
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.general_automation import task_completion
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
    missing_deliverable_labels,
    required_deliverable_evidence_from_labels,
)
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Flag — strict default OFF
# ---------------------------------------------------------------------------


def test_flag_default_off() -> None:
    assert parse_ga_deliverable_gate_enabled({}) is False
    assert parse_ga_deliverable_gate_enabled({"MAGI_GA_DELIVERABLE_GATE_ENABLED": ""}) is False
    assert parse_ga_deliverable_gate_enabled({"MAGI_GA_DELIVERABLE_GATE_ENABLED": "0"}) is False


def test_flag_explicit_on() -> None:
    assert parse_ga_deliverable_gate_enabled({"MAGI_GA_DELIVERABLE_GATE_ENABLED": "1"}) is True


def test_flag_never_defaults_on_in_full_profile() -> None:
    # Unlike profile-aware MAGI_*_ENABLED flags, the full runtime profile must
    # NOT flip this gate on implicitly.
    assert (
        parse_ga_deliverable_gate_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is False
    )


# ---------------------------------------------------------------------------
# Requirement derivation from assembly evidence labels (shared helper)
# ---------------------------------------------------------------------------


def test_required_from_labels_artifact() -> None:
    required = required_deliverable_evidence_from_labels(
        ("artifact_delivery_ref", "source_ledger")
    )
    assert required.requires_artifact_ref is True
    assert required.is_empty() is False


def test_required_from_labels_empty_without_artifact_label() -> None:
    required = required_deliverable_evidence_from_labels(("source_ledger", "git_diff"))
    assert required.is_empty() is True
    assert required_deliverable_evidence_from_labels(()).is_empty() is True


# ---------------------------------------------------------------------------
# Receipt-grounded missing computation over generic evidence entries
# ---------------------------------------------------------------------------


def _required_artifact() -> RequiredDeliverableEvidence:
    return RequiredDeliverableEvidence(requires_artifact_ref=True)


def test_missing_over_empty_entries() -> None:
    assert missing_deliverable_labels(_required_artifact(), ()) == ("artifactRef",)


def test_missing_empty_requirement_is_inert() -> None:
    assert missing_deliverable_labels(RequiredDeliverableEvidence(), ()) == ()


def test_satisfied_by_nested_local_artifact_receipt_mapping() -> None:
    # Shape produced by the live collector path: the receipt projection dict
    # carries metadata["localArtifactReceipt"]["artifactRef"].
    record = {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "receipts": {
            "localArtifactReceipt": {"artifactRef": "artifact:csv:abc123"},
        },
    }
    assert missing_deliverable_labels(_required_artifact(), (record,)) == ()


def test_satisfied_by_tool_result_artifact_refs() -> None:
    result = ToolResult(
        status="ok",
        output={"result": "written"},
        artifactRefs=("artifact:docx:abc123",),
    )

    assert missing_deliverable_labels(_required_artifact(), (result,)) == ()


def test_satisfied_by_ledger_entry_objects() -> None:
    class _Entry:
        payload = {"kind": "local_csv_artifact"}
        metadata = {"localArtifactReceipt": {"artifactRef": "artifact:csv:abc123"}}

    assert missing_deliverable_labels(_required_artifact(), (_Entry(),)) == ()


def test_blank_artifact_ref_does_not_satisfy() -> None:
    record = {"receipts": {"localArtifactReceipt": {"artifactRef": "  "}}}
    assert missing_deliverable_labels(_required_artifact(), (record,)) == (
        "artifactRef",
    )


# ---------------------------------------------------------------------------
# Snapshot plumbing deleted (promote-or-delete: delete)
# ---------------------------------------------------------------------------


def test_snapshot_enforcement_constant_deleted() -> None:
    assert not hasattr(task_completion, "ENFORCE_SNAPSHOT_REQUIREMENT")


def test_required_deliverable_evidence_has_no_snapshot_field() -> None:
    fields = RequiredDeliverableEvidence.__dataclass_fields__
    assert "requires_snapshot_ref" not in fields
    assert list(fields) == ["requires_artifact_ref"]


# ---------------------------------------------------------------------------
# Collector — localArtifactReceipt visibility is flag-gated
# ---------------------------------------------------------------------------


def _spreadsheet_write_result() -> ToolResult:
    return ToolResult(
        status="ok",
        output={"artifactRef": "artifact:csv:abc123"},
        artifactRefs=("artifact:csv:abc123",),
        metadata={
            "toolName": "spreadsheet_write",
            "localArtifactReceipt": {
                "kind": "local_csv_artifact",
                "artifactRef": "artifact:csv:abc123",
            },
        },
    )


def _recorded_receipts(collector: LocalToolEvidenceCollector) -> list[dict[str, object]]:
    records = collector.collect_for_turn("turn-1")
    return [record for record in records if isinstance(record, dict)]


def test_collector_drops_deliverable_receipt_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", raising=False)
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="spreadsheet_write",
        result=_spreadsheet_write_result(),
    )
    for record in _recorded_receipts(collector):
        assert "localArtifactReceipt" not in record.get("receipts", {})


def test_collector_keeps_deliverable_receipt_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="spreadsheet_write",
        result=_spreadsheet_write_result(),
    )
    receipts = [
        record.get("receipts", {}) for record in _recorded_receipts(collector)
    ]
    assert any(
        entry.get("localArtifactReceipt", {}).get("artifactRef")
        == "artifact:csv:abc123"
        for entry in receipts
        if isinstance(entry, dict)
    )
    # The promoted check must be satisfied by the collector's own records.
    assert (
        missing_deliverable_labels(
            _required_artifact(), collector.collect_for_turn("turn-1")
        )
        == ()
    )


def test_collector_accepts_documentwrite_tool_artifact_refs(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="DocumentWrite",
        result=ToolResult(
            status="ok",
            output={"path": "report.docx"},
            artifactRefs=("artifact:docx:abc123",),
        ),
    )

    assert (
        missing_deliverable_labels(
            _required_artifact(), collector.collect_for_turn("turn-1")
        )
        == ()
    )


def test_collector_accepts_documentwrite_output_artifact_ref(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="DocumentWrite",
        result=ToolResult(
            status="ok",
            output={"artifactRef": "artifact:docx:abc123"},
        ),
    )

    assert (
        missing_deliverable_labels(
            _required_artifact(), collector.collect_for_turn("turn-1")
        )
        == ()
    )


def test_collector_accepts_filedeliver_output_artifact_refs(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="FileDeliver",
        result=ToolResult(
            status="ok",
            output={"artifactRefs": ["artifact:delivery:abc123"]},
        ),
    )

    assert (
        missing_deliverable_labels(
            _required_artifact(), collector.collect_for_turn("turn-1")
        )
        == ()
    )


def test_collector_accepts_filedeliver_output_artifact_ref(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="FileDeliver",
        result=ToolResult(
            status="ok",
            output={"artifactRef": "artifact:delivery:abc123"},
        ),
    )

    assert (
        missing_deliverable_labels(
            _required_artifact(), collector.collect_for_turn("turn-1")
        )
        == ()
    )


def test_collector_ignores_unsafe_output_artifact_ref(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="DocumentWrite",
        result=ToolResult(
            status="ok",
            output={"artifactRef": "/Users/kevin/private/report.docx"},
        ),
    )

    assert missing_deliverable_labels(
        _required_artifact(), collector.collect_for_turn("turn-1")
    ) == ("artifactRef",)


def test_collector_still_blocks_when_required_artifact_ref_missing(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="DocumentWrite",
        result=ToolResult(status="ok", output={"path": "report.docx"}),
    )

    assert missing_deliverable_labels(
        _required_artifact(), collector.collect_for_turn("turn-1")
    ) == ("artifactRef",)


# ---------------------------------------------------------------------------
# Engine — live pre-final seam consumes the promoted check
# ---------------------------------------------------------------------------


class _NoopRunner:
    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:
            yield kwargs


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[object]) -> None:
        self.role = role
        self.parts = parts


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


class _RunnerInput:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeAdapter:
    def __init__(self, *, runner: object) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        if False:
            yield object()


class _FakeBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del adk_event, turn_id
        return type("Projection", (), {"agent_events": []})()


def _fake_engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _FakeBridge,
        "OpenMagiRunnerAdapter": _FakeAdapter,
        "RunnerTurnInput": _RunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


def _ga_policy_assembly() -> RunnerPolicyAssembly:
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-opus-4-1",
        selectedPackIds=("openmagi.artifact-delivery",),
        evidenceRequirements=("artifact_delivery_ref",),
        requiredValidators=(),
        missingEvidenceAction="block",
        repairPolicy={"action": "block", "source": "recipe-materializer"},
        attachmentFlags={
            "providerCalled": False,
            "routeAttached": False,
            "adkRunnerInvoked": False,
            "productionWriteAllowed": False,
            "userVisibleOutputAllowed": False,
            "livePolicyCallbackAttached": True,
        },
    )


def _drive_gate_event(
    *, evidence_records: tuple[object, ...]
) -> tuple[dict[str, object], object]:
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_ga_policy_assembly(),
        evidence_collector=lambda turn_id: evidence_records,
    )

    async def _drive() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={
                    "prompt": "write the spreadsheet",
                    "session_id": "s1",
                    "turn_id": "turn-1",
                },
                cancel=asyncio.Event(),
            )
        ]

    items = asyncio.run(_drive())
    events = [item for item in items if isinstance(item, RuntimeEvent)]
    gate_events = [
        event.payload
        for event in events
        if event.payload.get("type") == "pre_final_evidence_gate"
    ]
    assert gate_events, "expected a pre_final_evidence_gate status event"
    return gate_events[-1], items[-1]


def test_engine_flag_off_emits_no_ga_deliverable_entry(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)

    gate_event, _terminal = _drive_gate_event(evidence_records=())
    assert gate_event["missingEvidence"] == ["artifact_delivery_ref"]
    assert not any(
        str(ref).startswith("ga_deliverable:")
        for ref in gate_event["missingEvidence"]
    )


def test_engine_flag_on_blocks_with_actionable_ga_deliverable_reason(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)

    gate_event, terminal = _drive_gate_event(evidence_records=())
    assert gate_event["decision"] == "block"
    assert "ga_deliverable:artifactRef" in gate_event["missingEvidence"]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"


def test_engine_flag_on_satisfied_by_artifact_receipt_record(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)

    record = {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "receipts": {
            "localArtifactReceipt": {"artifactRef": "artifact:csv:abc123"},
        },
    }
    gate_event, _terminal = _drive_gate_event(evidence_records=(record,))
    assert gate_event["decision"] == "pass"
    assert gate_event["missingEvidence"] == []


def test_engine_flag_on_satisfied_by_tool_result_artifact_refs(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)

    gate_event, _terminal = _drive_gate_event(
        evidence_records=(
            ToolResult(status="ok", artifactRefs=("artifact:docx:abc123",)),
        )
    )
    assert gate_event["decision"] == "pass"
    assert gate_event["missingEvidence"] == []


def test_engine_flag_on_inert_without_artifact_label(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)

    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="anthropic",
            modelLabel="anthropic/claude-opus-4-1",
            selectedPackIds=("openmagi.research",),
            evidenceRequirements=(),
            requiredValidators=(),
            missingEvidenceAction="block",
            repairPolicy={"action": "block", "source": "recipe-materializer"},
            attachmentFlags={
                "providerCalled": False,
                "routeAttached": False,
                "adkRunnerInvoked": False,
                "productionWriteAllowed": False,
                "userVisibleOutputAllowed": False,
                "livePolicyCallbackAttached": True,
            },
        ),
        evidence_collector=lambda turn_id: (),
    )

    async def _drive() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={
                    "prompt": "summarize",
                    "session_id": "s1",
                    "turn_id": "turn-1",
                },
                cancel=asyncio.Event(),
            )
        ]

    items = asyncio.run(_drive())
    events = [item for item in items if isinstance(item, RuntimeEvent)]
    gate_events = [
        event.payload
        for event in events
        if event.payload.get("type") == "pre_final_evidence_gate"
    ]
    assert gate_events
    assert gate_events[-1]["decision"] == "pass"
    assert gate_events[-1]["missingEvidence"] == []
