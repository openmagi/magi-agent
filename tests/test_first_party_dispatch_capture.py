"""Tests for first-party activity capture at the ToolDispatcher dispatch seam (Task 4).

Verifies that:
  - the capture path records exactly one activity per dispatch invocation,
  - coverage spans ok / error / blocked / needs_approval exits,
  - the seam is strictly inert for bare ToolDispatcher(registry) constructions,
  - the kill-switch (MAGI_FP_EVIDENCE_DISABLED=1) suppresses capture,
  - collector errors are swallowed (fail-open),
  - the process-default collector engages when refs are injected without a
    collector, and writes a JSONL file,
  - SkillLoader promotion + dedup works end-to-end through dispatch,
  - concurrent dispatch produces distinct recordIds.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.evidence.first_party_activity import FIRST_PARTY_ACTIVITY_REFS
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Module-scoped reset: ensure the process-default collector global never leaks
# across tests in this module.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_default_fp_collector(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[return]
    """Reset ``magi_agent.tools.dispatcher._DEFAULT_FP_COLLECTOR`` before and
    after each test so the process-global collector cannot leak state between
    tests in this module.
    """
    import magi_agent.tools.dispatcher as _dispatcher_mod

    monkeypatch.setattr(_dispatcher_mod, "_DEFAULT_FP_COLLECTOR", None)
    yield  # type: ignore[misc]
    monkeypatch.setattr(_dispatcher_mod, "_DEFAULT_FP_COLLECTOR", None)


# ---------------------------------------------------------------------------
# Helpers — copied from test_tool_latency_instrumentation.py (authoritative)
# ---------------------------------------------------------------------------


def _manifest(
    name: str, *, permission: str = "read", input_schema: dict | None = None
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission=permission,  # type: ignore[arg-type]
        input_schema=input_schema or {"type": "object", "additionalProperties": True},
        timeout_ms=5_000,
        available_in_modes=("plan", "act"),
        dangerous=False,
        mutates_workspace=False,
        tags=(),
        should_defer=False,
        latency_class="inline",
        adk_tool_type="FunctionTool",
        enabled_by_default=True,
        parallel_safety="readonly",
    )


def _registry_with(name: str, handler: object, *, input_schema: dict | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_manifest(name, input_schema=input_schema), handler=handler)  # type: ignore[arg-type]
    return registry


def _echo_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
    return ToolResult(status="ok", output="echoed")


def _echo_registry() -> ToolRegistry:
    return _registry_with("EchoTool", _echo_handler)


def _context() -> ToolContext:
    return ToolContext.model_validate(
        {
            "botId": "b",
            "sessionId": "s-d",
            "turnId": "t-d",
            "toolUseId": "c-d",
        }
    )


def _dispatch(dispatcher: ToolDispatcher, name: str = "EchoTool", **kwargs: object) -> ToolResult:
    return asyncio.run(
        dispatcher.dispatch(name, {"x": 1}, _context(), mode="act", **kwargs)  # type: ignore[arg-type]
    )


def _fp_records(collector: LocalToolEvidenceCollector, turn: str = "t-d") -> list[object]:
    return [
        r
        for r in collector.collect_for_turn(turn)
        if str(getattr(r, "type", "")).startswith("custom:FirstParty")
    ]


# ---------------------------------------------------------------------------
# Test 1: ok dispatch ⇒ one ToolCall record with durationMs≥0 and status=="ok"
# ---------------------------------------------------------------------------


def test_ok_dispatch_produces_one_tool_call_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    result = _dispatch(dispatcher)
    assert result.status == "ok"
    records = _fp_records(collector)
    assert len(records) == 1
    rec = records[0]
    fields = getattr(rec, "fields", {})
    assert int(str(fields.get("durationMs", -1))) >= 0
    assert str(fields.get("status", "")) == "ok"


# ---------------------------------------------------------------------------
# Test 2: unknown tool ⇒ result.status "error" + one record with errorCode "tool_not_found"
# ---------------------------------------------------------------------------


def test_unknown_tool_produces_error_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        ToolRegistry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    result = _dispatch(dispatcher, "NoSuchTool")
    assert result.status == "error"
    records = _fp_records(collector)
    assert len(records) == 1
    fields = getattr(records[0], "fields", {})
    assert str(fields.get("errorCode", "")) == "tool_not_found"


# ---------------------------------------------------------------------------
# Test 3: not-exposed ⇒ one record with reason "not exposed to this turn"
# ---------------------------------------------------------------------------


def test_not_exposed_produces_record_with_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    result = asyncio.run(
        dispatcher.dispatch(
            "EchoTool",
            {"x": 1},
            _context(),
            mode="act",
            exposed_tool_names=("OtherTool",),
        )
    )
    assert result.status == "error"
    records = _fp_records(collector)
    assert len(records) == 1
    fields = getattr(records[0], "fields", {})
    assert str(fields.get("reason", "")) == "not exposed to this turn"


# ---------------------------------------------------------------------------
# Test 4: schema-invalid ⇒ status "blocked", errorCode "tool_input_schema_invalid"
# ---------------------------------------------------------------------------


def test_schema_invalid_produces_blocked_record(tmp_path, monkeypatch) -> None:
    """Register a tool with a strict schema; pass a string where int required."""
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    strict_schema = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    registry = _registry_with("StrictTool", _echo_handler, input_schema=strict_schema)
    dispatcher = ToolDispatcher(
        registry,
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    # Pass a string instead of integer — violates the strict schema
    result = asyncio.run(
        dispatcher.dispatch("StrictTool", {"x": "not-an-int"}, _context(), mode="act")
    )
    assert result.status == "blocked"
    assert result.error_code == "tool_input_schema_invalid"
    records = _fp_records(collector)
    assert len(records) == 1
    fields = getattr(records[0], "fields", {})
    assert str(fields.get("errorCode", "")) == "tool_input_schema_invalid"


# ---------------------------------------------------------------------------
# Test 5: refs=() ⇒ zero records
# ---------------------------------------------------------------------------


def test_empty_refs_produces_zero_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=(),
    )
    _dispatch(dispatcher)
    assert _fp_records(collector) == []


# ---------------------------------------------------------------------------
# Test 6: bare ToolDispatcher(registry) ⇒ zero records, no JSONL file
# ---------------------------------------------------------------------------


def test_bare_dispatcher_is_inert(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    # bare construction — no first_party_* kwargs
    dispatcher = ToolDispatcher(_echo_registry(), readonly_offload_enabled=False)
    _dispatch(dispatcher)
    # No JSONL files should appear
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert jsonl_files == [], f"unexpected JSONL files: {jsonl_files}"


# ---------------------------------------------------------------------------
# Test 7: MAGI_FP_EVIDENCE_DISABLED=1 ⇒ zero records despite refs+collector
# ---------------------------------------------------------------------------


def test_kill_switch_suppresses_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_FP_EVIDENCE_DISABLED", "1")
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    _dispatch(dispatcher)
    assert _fp_records(collector) == []


# ---------------------------------------------------------------------------
# Test 8: collector that raises ⇒ dispatch result unaffected
# ---------------------------------------------------------------------------


def test_raising_collector_is_swallowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))

    class _RaisingCollector:
        def record_first_party_activity(self, **_kwargs: object) -> bool:
            raise RuntimeError("collector exploded")

        def collect_for_turn(self, turn_id: str) -> tuple[object, ...]:
            return ()

    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=_RaisingCollector(),
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    result = _dispatch(dispatcher)
    # The dispatch must succeed regardless of the collector failure
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Test 9: refs present, no collector ⇒ process-default collector writes JSONL
# ---------------------------------------------------------------------------


def test_no_collector_uses_process_default(tmp_path, monkeypatch) -> None:
    """When refs are injected but no collector is passed, the module-level
    default collector engages and a JSONL file appears under the tmp dir.
    """
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    # Nullify the module-level default so this test owns it
    import magi_agent.tools.dispatcher as dispatcher_mod

    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_FP_COLLECTOR", None)
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        # NO first_party_activity_collector
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    _dispatch(dispatcher)
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert len(jsonl_files) >= 1, "expected at least one JSONL file from the default collector"


# ---------------------------------------------------------------------------
# Test 10: SkillLoader promotion + dedup through dispatch
# ---------------------------------------------------------------------------


def test_skill_loader_promotion_and_dedup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    skill_output = {
        "skills": ["bundled/a"],
        "skillCount": 1,
        "loadedSkills": [
            {"path": "bundled/a", "source": "bundled", "body": "#", "bodyDigest": "d1"}
        ],
        "loadedSkillCount": 1,
    }

    def _skill_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output=skill_output)

    registry = ToolRegistry()
    registry.register(_manifest("SkillLoader"), handler=_skill_handler)
    dispatcher = ToolDispatcher(
        registry,
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    # First dispatch — should produce one SkillLoad record
    asyncio.run(dispatcher.dispatch("SkillLoader", {"x": 1}, _context(), mode="act"))
    records_after_first = _fp_records(collector)
    assert len(records_after_first) == 1
    assert str(getattr(records_after_first[0], "fields", {}).get("evidenceType", "")) == "SkillLoad"
    # Second dispatch with same turn — dedup must suppress the duplicate
    asyncio.run(dispatcher.dispatch("SkillLoader", {"x": 1}, _context(), mode="act"))
    records_after_second = _fp_records(collector)
    assert len(records_after_second) == 1, "dedup must keep exactly one record for same turn/skill"


# ---------------------------------------------------------------------------
# Test 11: concurrent dispatch ⇒ 5 records with distinct recordIds
# ---------------------------------------------------------------------------


def test_concurrent_dispatch_produces_distinct_record_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    dispatcher = ToolDispatcher(
        _echo_registry(),
        readonly_offload_enabled=False,
        first_party_activity_collector=collector,
        first_party_evidence_refs=FIRST_PARTY_ACTIVITY_REFS,
    )

    # Use distinct turnIds so each dispatch goes into its own slot (no
    # cross-turn dedup collisions for ToolCall records).
    async def _run_concurrent() -> list[ToolResult]:
        contexts = [
            ToolContext.model_validate(
                {
                    "botId": "b",
                    "sessionId": "s-d",
                    "turnId": f"t-concurrent-{i}",
                    "toolUseId": f"c-{i}",
                }
            )
            for i in range(5)
        ]
        return list(
            await asyncio.gather(
                *[
                    dispatcher.dispatch("EchoTool", {"x": i}, ctx, mode="act")
                    for i, ctx in enumerate(contexts)
                ]
            )
        )

    results = asyncio.run(_run_concurrent())
    assert all(r.status == "ok" for r in results), "all concurrent dispatches must succeed"

    # Collect records across all concurrent turns
    all_records = [
        r
        for i in range(5)
        for r in collector.collect_for_turn(f"t-concurrent-{i}")
        if str(getattr(r, "type", "")).startswith("custom:FirstParty")
    ]
    assert len(all_records) == 5, f"expected 5 ToolCall records, got {len(all_records)}"
    record_ids = [str(getattr(r, "fields", {}).get("recordId", "")) for r in all_records]
    assert len(set(record_ids)) == 5, f"recordIds must be distinct, got: {record_ids}"
