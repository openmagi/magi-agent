"""Regression tests for root cause 2: FileRead's sourceProjection reaching the
collector on the LIVE path.

Live-instrumented finding: the CLI FileRead does NOT execute through
``LocalReadOnlyToolHost`` (which attaches ``metadata['sourceProjection']``).
It runs through ``Gate5BFullToolHost`` and the result is rebuilt by
``core_toolhost._tool_result_from_outcome``, which only carries
``gate5bFullToolhostReceipt`` — never a ``sourceProjection``. So a real
FileRead reached ``record_tool_result`` with ``hasSourceProj=False`` and the
source-ledger gate had no SourceInspection record to match.

The fix synthesizes a ``sourceProjection`` for read-only source tools in
``_tool_result_from_outcome`` ONLY when the default-OFF
``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED`` flag is on, so a real FileRead now
carries the metadata the collector's projector reads. Flag OFF stays
byte-identical.
"""

from __future__ import annotations

from magi_agent.evidence.local_tool_collector import (
    _projected_source_inspection_records,
)
from magi_agent.tools.core_toolhost import _tool_result_from_outcome


def _make_source_outcome(
    tool_name: str = "FileRead",
    path: str = "README.md",
    call_digest: str = "b",
):
    """Build a Gate5B outcome for a read-only source tool.

    ``call_digest`` seeds the ``toolCallDigest`` so distinct source reads (which
    have distinct tool call digests in production) can be simulated in tests.
    """
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolOutcome,
        Gate5BFullToolReceipt,
    )

    receipt = Gate5BFullToolReceipt.model_validate(
        {
            "requestDigest": "sha256:" + "a" * 64,
            "toolCallDigest": "sha256:" + (call_digest * 64)[:64],
            "toolName": tool_name,
            "status": "ok",
            "boundedOutputDigest": "sha256:" + "c" * 64,
            "outputByteCount": 42,
        }
    )
    return Gate5BFullToolOutcome.model_validate(
        {
            "status": "ok",
            "reason": "ok",
            "receipt": receipt,
            "outputPreview": {"path": path, "content": "# hi"},
            "handlerCalled": True,
        }
    )


def _make_filread_outcome():
    return _make_source_outcome()


def test_gate5b_fileread_outcome_carries_source_projection_when_flag_on(
    monkeypatch,
) -> None:
    """RED→GREEN: with the source-ledger gate flag ON a Gate5B FileRead outcome
    must carry a ``sourceProjection`` that yields a SourceInspection record."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    result = _tool_result_from_outcome(_make_filread_outcome())

    assert "sourceProjection" in result.metadata, (
        "live FileRead must carry sourceProjection metadata when the gate is on"
    )
    projected = _projected_source_inspection_records(result.metadata)
    assert projected, "expected a SourceInspection projected record"
    assert all(
        getattr(rec, "type", None) == "SourceInspection" for rec in projected
    )


def test_gate5b_fileread_outcome_no_source_projection_when_flag_off(
    monkeypatch,
) -> None:
    """Regression guard: flag OFF (default) -> no sourceProjection key, so the
    metadata shape is byte-identical to main."""
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    result = _tool_result_from_outcome(_make_filread_outcome())
    assert "sourceProjection" not in result.metadata


def test_gate5b_nonsource_tool_no_source_projection_when_flag_on(
    monkeypatch,
) -> None:
    """A non-source tool (e.g. Bash) must NOT get a synthesized sourceProjection
    even with the flag on — only read-only source tools inspect sources."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolOutcome,
        Gate5BFullToolReceipt,
    )

    receipt = Gate5BFullToolReceipt.model_validate(
        {
            "requestDigest": "sha256:" + "a" * 64,
            "toolCallDigest": "sha256:" + "b" * 64,
            "toolName": "Bash",
            "status": "ok",
            "boundedOutputDigest": "sha256:" + "c" * 64,
            "outputByteCount": 10,
        }
    )
    outcome = Gate5BFullToolOutcome.model_validate(
        {
            "status": "ok",
            "reason": "ok",
            "receipt": receipt,
            "outputPreview": {"stdout": "ok"},
            "handlerCalled": True,
        }
    )
    result = _tool_result_from_outcome(outcome)
    assert "sourceProjection" not in result.metadata


def test_collector_projects_source_inspection_from_fileread_shaped_result(
    monkeypatch,
) -> None:
    """Task-2 acceptance: a FileRead-shaped result (with the synthesized
    sourceProjection) yields a SourceInspection projected record through
    ``record_tool_result`` end-to-end."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    result = _tool_result_from_outcome(_make_filread_outcome())
    collector = LocalToolEvidenceCollector()
    records = collector.record_tool_result(
        session_id="s",
        turn_id="t",
        tool_call_id="c",
        tool_name="FileRead",
        result=result,
    )
    source_records = [
        rec for rec in records if getattr(rec, "type", None) == "SourceInspection"
    ]
    assert source_records, "record_tool_result must project a SourceInspection record"


def test_projection_allocates_distinct_ids_for_distinct_sources_legacy_flag_on(
    monkeypatch,
) -> None:
    """The old src_1 hardcode collision is RESOLVED even on the legacy-only path.

    Two DISTINCT source reads (distinct tool call digests) now get DISTINCT
    projection ids, and the same read stays stable (deterministic, no counter),
    when only MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED is on.
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_SOURCE_CITATION_ENABLED", raising=False)

    result1 = _tool_result_from_outcome(
        _make_source_outcome(path="README.md", call_digest="b")
    )
    result2 = _tool_result_from_outcome(
        _make_source_outcome(path="src/main.py", call_digest="d")
    )
    id1 = result1.metadata["sourceProjection"]["sources"][0]["sourceId"]
    id2 = result2.metadata["sourceProjection"]["sources"][0]["sourceId"]

    assert not (id1 == "src_1" and id2 == "src_1"), "id must no longer be hardcoded"
    assert id1 != id2, "distinct sources must get distinct projection ids"

    # Same read (same tool call digest) must be stable across evaluations.
    result1_again = _tool_result_from_outcome(
        _make_source_outcome(path="README.md", call_digest="b")
    )
    id1_again = result1_again.metadata["sourceProjection"]["sources"][0]["sourceId"]
    assert id1 == id1_again, "same source read must get a stable, deterministic id"


def test_gitdiff_projection_kind_is_external_repo(monkeypatch) -> None:
    """A GitDiff projection must carry kind='external_repo', not the old
    hardcoded 'file' (which was the source of the both-flags-on remap miss)."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    result = _tool_result_from_outcome(
        _make_source_outcome(tool_name="GitDiff", path=".", call_digest="e")
    )
    source = result.metadata["sourceProjection"]["sources"][0]
    assert source["kind"] == "external_repo"


def test_both_flags_on_distinct_ids_across_fileread_and_gitdiff(
    monkeypatch,
) -> None:
    """Invariant: no two DISTINCT sources ever share an id in ANY flag combo.

    With BOTH the legacy source-ledger gate and the citation flag on, two
    FileReads plus one GitDiff driven through record_tool_result yield distinct
    projected SourceInspection source ids (the old src_1 collision, including the
    GitDiff kind mismatch that the removed remap could not resolve, is gone).
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    outcomes = [
        _make_source_outcome(tool_name="FileRead", path="a.md", call_digest="b"),
        _make_source_outcome(tool_name="FileRead", path="b.md", call_digest="d"),
        _make_source_outcome(tool_name="GitDiff", path=".", call_digest="e"),
    ]

    source_ids: list[str] = []
    for i, outcome in enumerate(outcomes):
        result = _tool_result_from_outcome(outcome)
        records = collector.record_tool_result(
            session_id="sess-both",
            turn_id="turn-1",
            tool_call_id=f"c{i}",
            tool_name=outcome.receipt.tool_name,
            result=result,
        )
        for rec in records:
            if getattr(rec, "type", None) != "SourceInspection":
                continue
            fields = getattr(rec, "fields", None)
            get_fn = getattr(fields, "get", None)
            if callable(get_fn):
                sid = get_fn("sourceId")
                if isinstance(sid, str):
                    source_ids.append(sid)

    assert len(source_ids) == 3, f"expected 3 SourceInspection ids, got {source_ids}"
    assert len(set(source_ids)) == 3, f"ids must be distinct, got {source_ids}"


def test_citation_capture_produces_unique_ids_for_multiple_fileread_calls(
    monkeypatch,
) -> None:
    """GREEN: with MAGI_SOURCE_CITATION_ENABLED ON and distinct file paths,
    two FileRead calls in the same session get unique source ids via the
    session registry (not via _synthesized_source_projection).
    """
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.tools.result import ToolResult

    collector = LocalToolEvidenceCollector()
    result = ToolResult(status="ok", output={"content": "hello"}, metadata={})

    records1 = collector.record_tool_result(
        session_id="sess-1",
        turn_id="turn-1",
        tool_call_id="c1",
        tool_name="FileRead",
        result=result,
        arguments={"path": "/workspace/README.md"},
    )
    records2 = collector.record_tool_result(
        session_id="sess-1",
        turn_id="turn-1",
        tool_call_id="c2",
        tool_name="FileRead",
        result=result,
        arguments={"path": "/workspace/src/main.py"},
    )

    # Each call should produce at least one citation evidence record
    citation1 = [
        r for r in records1
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    citation2 = [
        r for r in records2
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    assert citation1, "first FileRead must produce a citation record"
    assert citation2, "second FileRead must produce a citation record"

    # Extract source ids from the evidence records
    def _get_source_id(rec: object) -> str | None:
        fields = getattr(rec, "fields", None)
        get_fn = getattr(fields, "get", None)
        if callable(get_fn):
            sid = get_fn("sourceId")
            if isinstance(sid, str):
                return sid
            sids = get_fn("sourceIds")
            if isinstance(sids, (list, tuple)) and sids:
                return str(sids[0])
        return None

    id1 = _get_source_id(citation1[0])
    id2 = _get_source_id(citation2[0])
    assert id1 is not None, "first citation record must have a sourceId"
    assert id2 is not None, "second citation record must have a sourceId"
    assert id1 != id2, f"expected unique ids, got {id1!r} and {id2!r}"
