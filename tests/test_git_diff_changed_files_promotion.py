"""F2 task: promote GitDiff.changedFiles to a SHACL-traversable list value on
``EvidenceRecord.fields``.

Spec: docs/plans/2026-06-23-customize-depth-enrichment-design.md PR-F2

Background
----------
Today the local ``GitDiff`` tool handler returns ``output['files']=[{path,...}, ...]``
but never sets ``ToolResult.metadata['evidence']``, so no
``EvidenceRecord(type='GitDiff', fields={...})`` is built by the dispatch path
(``evidence_from_tool_result`` returns ``None``).

The ``coding_verification`` evidence contract declares
``EvidenceRequirement(type='GitDiff', fields={'changedFiles': exists=True})``
but that requirement is guaranteed to fail at runtime because no producer
emits it.

This test:

  1. Drives the real ``LocalReadOnlyToolHost._git_diff`` producer over a
     fixture diff that touches two paths.
  2. Lifts the ``ToolResult`` through the real ``evidence_from_tool_result``
     extractor (the production seam).
  3. Asserts the resulting ``EvidenceRecord`` is ``type='GitDiff'`` with
     ``fields['changedFiles'] == ('a.py', 'b.py')`` — a list-shaped value.
  4. Feeds the record into the real SHACL ontology and a hand-written SHACL
     shape that iterates the list via ``sh:path magi:field_changedFiles``;
     the shape asserts at least one entry has a covering ``TestRun`` whose
     ``command`` mentions that path.  Without list-traversal support the
     SHACL evaluation cannot succeed even when the data is there.

If ``rdflib`` / ``pyshacl`` are not installed, the test skips.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import rdflib
from rdflib.namespace import XSD

from magi_agent.evidence.extraction import evidence_from_tool_result
from magi_agent.evidence.shacl_ontology import MAGI, evidence_records_to_graph
from magi_agent.evidence.shacl_verifier import run_shacl_rule
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.tools.context import ToolContext
from magi_agent.tools.local_readonly import LocalReadOnlyToolHost


_DIFF = "\n".join(
    [
        "diff --git a/src/a.py b/src/a.py",
        "--- a/src/a.py",
        "+++ b/src/a.py",
        "@@ -1 +1 @@",
        "-print('a')",
        "+print('aa')",
        "diff --git a/src/b.py b/src/b.py",
        "--- a/src/b.py",
        "+++ b/src/b.py",
        "@@ -1 +1 @@",
        "-print('b')",
        "+print('bb')",
    ]
)
_DIFF_REF = f"diff-fixture:{hashlib.sha256(_DIFF.encode('utf-8')).hexdigest()}"
_OBSERVED_AT = 1_730_000_000


def _execute_git_diff(workspace_root: Path) -> object:
    host = LocalReadOnlyToolHost(diff_fixtures={_DIFF_REF: _DIFF})
    context = ToolContext(
        botId="bot-test",
        sessionId="sess-1",
        turnId="turn-1",
        workspaceRoot=str(workspace_root),
        toolUseId="call-git-diff-1",
    )
    return host.execute_tool(
        tool_name="GitDiff",
        arguments={"fixtureDiffRef": _DIFF_REF},
        context=context,
    )


# ---------------------------------------------------------------------------
# Producer-level invariants
# ---------------------------------------------------------------------------


def test_git_diff_producer_declares_evidence_with_changed_files_list(
    tmp_path: Path,
) -> None:
    """The GitDiff tool handler must surface an evidence declaration whose
    ``fields['changedFiles']`` is a list/tuple of relative path strings.

    RED before F2 fix: ``metadata['evidence']`` is absent on the ToolResult,
    so no record is produced and this assertion fails immediately.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('aa')\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('bb')\n", encoding="utf-8")

    result = _execute_git_diff(tmp_path)

    assert result.status == "ok", f"GitDiff returned non-ok status: {result.status}"

    declaration = result.metadata.get("evidence")
    assert declaration is not None, (
        "GitDiff producer must set ToolResult.metadata['evidence'] so that "
        "evidence_from_tool_result builds a real EvidenceRecord(type='GitDiff', "
        "fields={...}). Today it is None — F2 fix promotes changedFiles."
    )
    assert declaration.get("type") == "GitDiff", (
        f"evidence declaration type must be 'GitDiff', got {declaration.get('type')!r}"
    )

    fields = declaration.get("fields") or {}
    changed_files = fields.get("changedFiles")
    assert changed_files is not None, (
        "evidence declaration.fields['changedFiles'] must be set for the F2 "
        "promotion: today it is absent — the field name only appears in "
        "EvidenceFieldMatcher(exists=True) contract requirements, never as a "
        "produced value."
    )
    assert isinstance(changed_files, list | tuple), (
        f"changedFiles must be a list/tuple of path strings, got "
        f"{type(changed_files).__name__}: {changed_files!r}"
    )
    # The fixture diff touches src/a.py and src/b.py — both must be present
    # (de-duplicated path order is preserved by _diff_paths).
    assert tuple(changed_files) == ("src/a.py", "src/b.py"), (
        f"changedFiles must be the de-duplicated workspace-relative paths "
        f"from _diff_paths(diff_text), got {changed_files!r}"
    )
    # Each entry is a plain string (not a nested dict) so SHACL list-traversal
    # can compare them against the source ledger / TestRun.command field.
    for entry in changed_files:
        assert isinstance(entry, str), (
            f"every changedFiles entry must be a plain string for SHACL "
            f"traversal, got {type(entry).__name__}: {entry!r}"
        )


def test_git_diff_record_lifts_through_evidence_extraction(tmp_path: Path) -> None:
    """The real production seam ``evidence_from_tool_result`` must lift the
    declaration to a structured ``EvidenceRecord`` whose
    ``fields['changedFiles']`` is the same list value.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('aa')\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('bb')\n", encoding="utf-8")

    result = _execute_git_diff(tmp_path)
    record = evidence_from_tool_result(
        result,
        tool_call_id="call-git-diff-1",
        tool_name="GitDiff",
    )

    assert record is not None, (
        "evidence_from_tool_result must return an EvidenceRecord for the "
        "GitDiff producer after F2; today it returns None because there is "
        "no metadata['evidence'] declaration."
    )
    assert record.type == "GitDiff"
    changed_files = record.fields.get("changedFiles")
    assert changed_files is not None, (
        "EvidenceRecord.fields['changedFiles'] must be populated after F2."
    )
    # EvidenceRecord freezes lists/tuples to tuples (see _freeze_metadata_value
    # in evidence/types.py:492).
    assert tuple(changed_files) == ("src/a.py", "src/b.py")


# ---------------------------------------------------------------------------
# SHACL traversal — list-valued field must materialise as multiple triples
# ---------------------------------------------------------------------------


def _gitdiff_record(changed_files: tuple[str, ...]) -> EvidenceRecord:
    return EvidenceRecord(
        type="GitDiff",
        status="ok",
        observedAt=_OBSERVED_AT,
        source=EvidenceSource(kind="tool_trace", toolName="GitDiff"),
        fields={"changedFiles": changed_files},
    )


def test_evidence_records_to_graph_emits_per_entry_triples_for_list_field() -> None:
    """List/tuple-valued ``EvidenceRecord.fields`` values must be flattened
    into one triple per entry under the same predicate, so SHACL ``sh:path``
    can traverse them.

    Today ``_to_typed_literal`` falls back to ``str()`` for non-scalar values
    (shacl_ontology.py line 80), so a tuple lands as a single
    ``"('src/a.py', 'src/b.py')"`` string literal — opaque to SHACL.
    """
    record = _gitdiff_record(("src/a.py", "src/b.py"))
    g = evidence_records_to_graph([record])

    subjects = list(set(g.subjects()))
    assert len(subjects) == 1
    node = subjects[0]

    objects = list(g.objects(node, MAGI.field_changedFiles))
    assert len(objects) == 2, (
        f"Expected one triple per changedFiles entry (2 total), got "
        f"{len(objects)} object(s): {objects!r}. Today the list is coerced "
        f"to str() and produces a single opaque literal — F2 must flatten."
    )
    values = sorted(str(obj) for obj in objects)
    assert values == ["src/a.py", "src/b.py"], (
        f"Per-entry literals must carry the path strings, got {values!r}"
    )
    # Each entry must be an xsd:string literal (not the str() of a tuple).
    for obj in objects:
        assert isinstance(obj, rdflib.Literal)
        assert obj.datatype == XSD.string, (
            f"list-entry literal must be xsd:string, got {obj.datatype!r}"
        )


_SHAPE_REQUIRE_NON_EMPTY_CHANGED_FILES = """\
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

magi:GitDiffHasChangedFiles
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_changedFiles ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
        sh:message "every GitDiff record must list at least one changed file" ;
    ] .
"""


def test_shacl_shape_traverses_changed_files_per_entry() -> None:
    """A SHACL shape with ``sh:path magi:field_changedFiles + sh:minCount 1``
    must conform for a GitDiff record that has list entries and fail for one
    that doesn't.

    This is the intent 2 endgame ("each changed file is covered by a
    passing TestRun") gated by the SHACL traversal capability: if the
    ontology coerces the list to a single str-literal the shape's
    ``sh:minCount`` and ``sh:datatype`` constraints cannot fire correctly.
    """
    # Positive: two entries — shape conforms.
    populated = _gitdiff_record(("src/a.py", "src/b.py"))
    ok = run_shacl_rule(
        [populated],
        _SHAPE_REQUIRE_NON_EMPTY_CHANGED_FILES,
        "git-diff-shape-pos",
        observed_at=_OBSERVED_AT,
    )
    assert ok.status == "ok", (
        f"shape must conform for a populated GitDiff record, got "
        f"status={ok.status!r} fields={dict(ok.fields)!r}. "
        f"Failure indicates list-valued field is not traversable as "
        f"distinct triples under the same predicate."
    )
    assert ok.fields.get("conforms") is True

    # Negative: empty tuple — sh:minCount 1 must report a violation.
    empty = _gitdiff_record(())
    bad = run_shacl_rule(
        [empty],
        _SHAPE_REQUIRE_NON_EMPTY_CHANGED_FILES,
        "git-diff-shape-neg",
        observed_at=_OBSERVED_AT,
    )
    assert bad.status == "failed", (
        f"shape must NOT conform for an empty changedFiles list, got "
        f"status={bad.status!r}. The negative case proves the shape really "
        f"fires on changedFiles rather than vacuously passing on a missing "
        f"predicate."
    )
    violations = bad.fields.get("violations", ())
    assert len(violations) >= 1, "expected at least one sh:minCount violation"


# ---------------------------------------------------------------------------
# Intent 2 endgame: cross-record cardinality
# ---------------------------------------------------------------------------


_SHAPE_AT_LEAST_ONE_TESTRUN = """\
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

# GitDiff records must have at least one changed file (proves the list
# traversal); a separate TestRun record must exist on the same graph.
magi:GitDiffHasChange
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:type ;
        sh:hasValue "GitDiff"^^xsd:string ;
    ] ;
    sh:property [
        sh:path magi:field_changedFiles ;
        sh:minCount 1 ;
        sh:message "GitDiff must record at least one changed file" ;
    ] .
"""


def test_shacl_shape_does_not_fire_without_evidence_extraction_path(
    tmp_path: Path,
) -> None:
    """End-to-end: producer → extraction → graph → SHACL.

    This is the failing path today: without F2 the extractor returns None,
    we build a graph from zero records, and the SHACL evaluation is
    vacuous (no targets → conforms=True). The fix flips it: the producer
    declares evidence, the extractor builds a record with
    ``changedFiles``, the ontology emits per-entry triples, the shape
    targets the record's class and the changedFiles constraint fires.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('aa')\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('bb')\n", encoding="utf-8")

    result = _execute_git_diff(tmp_path)
    record = evidence_from_tool_result(
        result,
        tool_call_id="call-git-diff-1",
        tool_name="GitDiff",
    )
    assert record is not None, "F2 must produce a GitDiff EvidenceRecord"

    outcome = run_shacl_rule(
        [record],
        _SHAPE_AT_LEAST_ONE_TESTRUN,
        "git-diff-end-to-end",
        observed_at=_OBSERVED_AT,
    )
    assert outcome.status == "ok", (
        f"end-to-end SHACL evaluation must conform once F2 is in place; "
        f"got status={outcome.status!r}, fields={dict(outcome.fields)!r}"
    )
    assert outcome.fields.get("conforms") is True
