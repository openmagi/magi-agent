"""Tests for magi_agent.evidence.shacl_ontology.evidence_records_to_graph.

TDD: write failing tests first, then implement.
Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.1
"""
from __future__ import annotations

import rdflib
import rdflib.compare
from rdflib.namespace import XSD

from magi_agent.evidence.shacl_ontology import MAGI, MAGI_NS, evidence_records_to_graph
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


def _make_record(
    *,
    type: str = "Calculation",
    status: str = "ok",
    fields: dict | None = None,
    observed_at: int = 1_000_000,
) -> EvidenceRecord:
    return EvidenceRecord(
        type=type,
        status=status,  # type: ignore[arg-type]  # helper uses str, EvidenceRecord expects EvidenceStatus literal
        observedAt=observed_at,
        source=EvidenceSource(kind="verifier"),
        fields=fields or {},
    )


# ---------------------------------------------------------------------------
# Test 1 — basic triples for a single record
# ---------------------------------------------------------------------------


def test_basic_triples_single_record() -> None:
    """A record with type=Calculation, status=ok, fields={amount:4200, category:'travel'}
    must produce the expected triples in the graph."""
    record = _make_record(
        type="Calculation",
        status="ok",
        fields={"amount": 4200, "category": "travel"},
    )
    g = evidence_records_to_graph([record])

    # Exactly one subject node must exist
    subjects = list(set(g.subjects()))
    assert len(subjects) == 1, f"Expected 1 subject, got {len(subjects)}"
    node = subjects[0]

    # rdf:type triple
    rdf_type = rdflib.RDF.type
    type_objects = list(g.objects(node, rdf_type))
    assert MAGI.Evidence in type_objects, f"magi:Evidence type not found: {type_objects}"

    # magi:type predicate (record type string)
    type_triples = list(g.objects(node, MAGI.type))
    assert len(type_triples) == 1, f"Expected 1 magi:type triple, got {type_triples}"
    assert str(type_triples[0]) == "Calculation"

    # magi:status predicate
    status_triples = list(g.objects(node, MAGI.status))
    assert len(status_triples) == 1
    assert str(status_triples[0]) == "ok"

    # magi:field_amount — integer value
    amount_triples = list(g.objects(node, MAGI.field_amount))
    assert len(amount_triples) == 1
    amount_lit = amount_triples[0]
    assert isinstance(amount_lit, rdflib.Literal)
    assert amount_lit.toPython() == 4200
    assert amount_lit.datatype == XSD.integer

    # magi:field_category — string value
    category_triples = list(g.objects(node, MAGI.field_category))
    assert len(category_triples) == 1
    cat_lit = category_triples[0]
    assert isinstance(cat_lit, rdflib.Literal)
    assert str(cat_lit) == "travel"
    assert cat_lit.datatype == XSD.string


# ---------------------------------------------------------------------------
# Test 2 — XSD type preservation: int, float, bool, str
# ---------------------------------------------------------------------------


def test_xsd_type_preservation() -> None:
    """int → xsd:integer, float → xsd:decimal, bool → xsd:boolean, str → xsd:string."""
    record = _make_record(
        fields={
            "int_field": 42,
            "float_field": 3.14,
            "bool_field": True,
            "str_field": "hello",
        }
    )
    g = evidence_records_to_graph([record])
    subjects = list(set(g.subjects()))
    assert len(subjects) == 1
    node = subjects[0]

    def get_lit(pred_name: str) -> rdflib.Literal:
        pred = MAGI[f"field_{pred_name}"]
        triples = list(g.objects(node, pred))
        assert len(triples) == 1, f"Expected 1 triple for field_{pred_name}, got {triples}"
        lit = triples[0]
        assert isinstance(lit, rdflib.Literal), f"Expected Literal for {pred_name}, got {type(lit)}"
        return lit

    int_lit = get_lit("int_field")
    assert int_lit.datatype == XSD.integer, f"int → {int_lit.datatype}"
    assert int_lit.toPython() == 42

    float_lit = get_lit("float_field")
    assert float_lit.datatype == XSD.decimal, f"float → {float_lit.datatype}"

    bool_lit = get_lit("bool_field")
    assert bool_lit.datatype == XSD.boolean, f"bool → {bool_lit.datatype}"
    assert bool_lit.toPython() is True

    str_lit = get_lit("str_field")
    assert str_lit.datatype == XSD.string, f"str → {str_lit.datatype}"
    assert str(str_lit) == "hello"


# ---------------------------------------------------------------------------
# Test 3 — determinism: same records → isomorphic graph, stable serialisation
# ---------------------------------------------------------------------------


def test_determinism_same_input_same_graph() -> None:
    """Calling evidence_records_to_graph twice with identical records must produce
    graphs that serialise identically when sorted (isomorphic)."""
    records = [
        _make_record(type="Calculation", fields={"amount": 100, "tag": "alpha"}),
        _make_record(type="TestRun", status="failed", fields={"exit_code": 1}),
    ]

    g1 = evidence_records_to_graph(records)
    g2 = evidence_records_to_graph(records)

    ser1 = _sorted_n3(g1)
    ser2 = _sorted_n3(g2)
    assert ser1 == ser2, "Serialised graphs differ — determinism broken"
    # Also check using rdflib isomorphism
    assert rdflib.compare.isomorphic(g1, g2), "Graphs are not isomorphic"


def _sorted_n3(g: rdflib.Graph) -> str:
    """Serialise to N-Triples, sort lines, return joined string."""
    nt = g.serialize(format="nt")
    lines = sorted(line for line in nt.splitlines() if line.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test 4 — empty input → empty graph, no exception
# ---------------------------------------------------------------------------


def test_empty_records_produces_empty_graph() -> None:
    """An empty iterable must produce an empty graph (zero triples) without raising."""
    g = evidence_records_to_graph([])
    assert len(g) == 0, f"Expected 0 triples, got {len(g)}"


# ---------------------------------------------------------------------------
# Test 5 — unsafe field-key characters → escaped/sanitised, no exception, deterministic
# ---------------------------------------------------------------------------


def test_unsafe_field_key_characters_handled_deterministically() -> None:
    """Fields with unsafe characters (spaces, slashes, dots) in their key must not
    raise exceptions. The output must be deterministic across two calls."""
    record = _make_record(
        fields={
            "key with spaces": "val1",
            "key/with/slash": "val2",
            "key.with.dot": "val3",
            "normal_key": "val4",
        }
    )

    # Must not raise
    g1 = evidence_records_to_graph([record])
    g2 = evidence_records_to_graph([record])

    # Must be deterministic
    assert _sorted_n3(g1) == _sorted_n3(g2), "Unsafe-key handling is non-deterministic"

    # The graph must have at least the type and status triples
    subjects = list(set(g1.subjects()))
    assert len(subjects) == 1
    node = subjects[0]
    assert (node, rdflib.RDF.type, MAGI.Evidence) in g1

    # normal_key must be preserved
    normal_vals = list(g1.objects(node, MAGI.field_normal_key))
    assert len(normal_vals) == 1
    assert str(normal_vals[0]) == "val4"

    # Unsafe keys must be ESCAPED (not dropped) — assert sanitised predicates carry values
    # "key with spaces" → field_key_with_spaces
    spaces_vals = list(g1.objects(node, MAGI.field_key_with_spaces))
    assert len(spaces_vals) == 1, (
        f"Expected escaped predicate field_key_with_spaces in graph, got {spaces_vals}. "
        "Implementation must escape keys, not drop them."
    )
    assert str(spaces_vals[0]) == "val1"

    # "key/with/slash" → field_key_with_slash
    slash_vals = list(g1.objects(node, MAGI.field_key_with_slash))
    assert len(slash_vals) == 1, (
        f"Expected escaped predicate field_key_with_slash in graph, got {slash_vals}. "
        "Implementation must escape keys, not drop them."
    )
    assert str(slash_vals[0]) == "val2"

    # "key.with.dot" → field_key_with_dot
    dot_vals = list(g1.objects(node, MAGI.field_key_with_dot))
    assert len(dot_vals) == 1, (
        f"Expected escaped predicate field_key_with_dot in graph, got {dot_vals}. "
        "Implementation must escape keys, not drop them."
    )
    assert str(dot_vals[0]) == "val3"


# ---------------------------------------------------------------------------
# Test M2 — digit-starting field key is prefixed (not dropped), carries value
# ---------------------------------------------------------------------------


def test_digit_starting_field_key_is_prefixed_and_carries_value() -> None:
    """M2: a field key that starts with a digit (e.g. '1amount') must be sanitised
    with a prefix so the predicate is a valid XML local name, and the value must
    be preserved in the graph.

    The _sanitise_field_key implementation prefixes a leading underscore when the
    key starts with a digit, yielding e.g. 'field__1amount' or 'field_1amount'.
    This test only asserts that:
      (a) no exception is raised,
      (b) the value 5 IS present in the graph (the key is not dropped),
      (c) the stored predicate local-name does NOT start with a digit.
    """
    record = _make_record(fields={"1amount": 5})
    g = evidence_records_to_graph([record])

    # Must not raise — already implied by the call above
    subjects = list(set(g.subjects()))
    assert len(subjects) == 1
    node = subjects[0]

    # Find any predicate whose local name contains "1amount"
    matching_predicates = [
        pred
        for pred in g.predicates(node)
        if "1amount" in str(pred)
    ]
    assert matching_predicates, (
        "M2 FAIL: no predicate containing '1amount' found in graph. "
        "Digit-starting keys must be prefixed and stored, not silently dropped."
    )

    # The predicate local name must NOT start with a digit (XML validity)
    for pred in matching_predicates:
        local_name = str(pred).rsplit("#", 1)[-1]  # extract after #
        assert not local_name[0].isdigit(), (
            f"M2 FAIL: predicate local name {local_name!r} starts with a digit — "
            "invalid XML local name."
        )

    # The value 5 must be stored
    all_values = [str(o) for pred in matching_predicates for o in g.objects(node, pred)]
    assert "5" in all_values, (
        f"M2 FAIL: expected value '5' for digit-prefixed key, got: {all_values}"
    )
