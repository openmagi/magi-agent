"""Deterministic SHACL-shape synthesizer for the ``field_constraint`` IR.

Spec: docs/plans/2026-06-23-customize-depth-enrichment-design.md §PR-F3

This module exposes a single public function, :func:`compile_to_shacl_ttl`,
that turns a structured ``field_constraint`` payload into a SHACL Turtle
string.  No LLM, no I/O — given identical input the output is byte-identical.

Two payload shapes are supported.

Single-record predicates
------------------------

::

    {
      "evidenceType": "TestRun",   # must be in BUILTIN_EVIDENCE_TYPES
      "field":        "exitCode",  # must be in available_fields(evidenceType)
      "operator":     "eq" | "neq" | "gt" | "lt" | "ge" | "le"
                      | "exists" | "notExists",
      "value":        <scalar>,    # required for eq/neq/gt/lt/ge/le
    }

Operator mapping:

==========  =====================================
operator    SHACL constraint
==========  =====================================
``eq``      ``sh:hasValue <v>``
``neq``     ``sh:not [ sh:hasValue <v> ]``
``gt``      ``sh:minExclusive <v>``
``lt``      ``sh:maxExclusive <v>``
``ge``      ``sh:minInclusive <v>``
``le``      ``sh:maxInclusive <v>``
``exists``  ``sh:minCount 1``
``notExists``  ``sh:maxCount 0``
==========  =====================================

Cross-record cardinality
------------------------

::

    {
      "operator": "forEachExistsCovering",
      "source":   {"evidenceType": "GitDiff", "field": "changedFiles"},
      "target":   {"evidenceType": "TestRun", "field": "command",
                   "covering":    "source.entry"},
    }

The synthesised shape targets the *source* evidence type and, for each value
on ``source.field`` (the ontology flattens list/tuple fields into one triple
per entry), asserts the existence of at least one record in the data graph
whose ``magi:type`` equals ``target.evidenceType`` AND whose
``target.field`` predicate equals the source entry.

Honest-degrade validator
------------------------

Both ``payload.field`` (or ``source.field`` / ``target.field``) MUST appear
in :func:`magi_agent.customize.shacl_compiler.available_fields` for the
declared evidence type.  Unknown fields raise :class:`ValueError` before any
TTL is produced — silently emitting a ``magi:field_<unknown>`` predicate
would generate a vacuously-satisfied shape (matches nothing → always
conforms), which is exactly the trap the design doc names "honest degrade".

Frozen v1 operator set
----------------------

The eight single-record operators plus ``forEachExistsCovering`` are the
authorable surface for v1.  Any other operator value raises
``ValueError``.
"""
from __future__ import annotations

from typing import Any, Final

from magi_agent.customize.shacl_compiler import available_fields
from magi_agent.evidence.shacl_ontology import _sanitise_field_key
from magi_agent.evidence.types import BUILTIN_EVIDENCE_TYPES


# Single-record operators in the frozen v1 set.
_SINGLE_RECORD_OPERATORS: Final[frozenset[str]] = frozenset({
    "eq", "neq", "gt", "lt", "ge", "le", "exists", "notExists",
})

# Operators that REQUIRE a concrete ``value`` in the payload.
_VALUE_REQUIRED_OPERATORS: Final[frozenset[str]] = frozenset({
    "eq", "neq", "gt", "lt", "ge", "le",
})

# Cross-record cardinality operator.
_CROSS_RECORD_OPERATOR: Final[str] = "forEachExistsCovering"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_to_shacl_ttl(payload: dict[str, Any]) -> str:
    """Compile a ``field_constraint`` payload into a SHACL Turtle string.

    Pure function.  Raises :class:`ValueError` for any validation failure
    (unknown evidence type, unknown field, unknown operator, missing value
    for a comparison operator).  Never calls an LLM or performs I/O.

    The output begins with the standard prefix declarations and a single
    ``magi:FieldConstraintShape`` ``sh:NodeShape`` targeting
    ``magi:Evidence``.  The shape filters to the requested evidence type via
    ``sh:property [ sh:path magi:type; sh:hasValue "<EvidenceType>" ]`` so a
    rule scoped to ``TestRun`` never fires on a ``GitDiff`` record.
    """
    if not isinstance(payload, dict):
        raise ValueError("field_constraint payload must be a mapping")

    operator = payload.get("operator")
    if not isinstance(operator, str):
        raise ValueError("field_constraint payload.operator must be a string")

    if operator == _CROSS_RECORD_OPERATOR:
        return _compile_for_each_exists_covering(payload)

    if operator not in _SINGLE_RECORD_OPERATORS:
        legal = sorted(_SINGLE_RECORD_OPERATORS | {_CROSS_RECORD_OPERATOR})
        raise ValueError(
            f"field_constraint operator {operator!r} is not supported; "
            f"must be one of {legal}"
        )

    return _compile_single_record(payload, operator)


__all__ = ["compile_to_shacl_ttl"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_known_fields(evidence_type: str) -> list[str]:
    """Return the verified field hints for *evidence_type* (may be empty).

    Raises ValueError if the evidence type is not in the builtin catalog.
    """
    if not isinstance(evidence_type, str) or not evidence_type.strip():
        raise ValueError("evidenceType must be a non-empty string")
    if evidence_type not in BUILTIN_EVIDENCE_TYPES:
        raise ValueError(
            f"evidenceType {evidence_type!r} is not in the builtin evidence "
            f"catalog; field_constraint requires a known type"
        )
    for item in available_fields():
        if item["evidenceType"] == evidence_type:
            return list(item["fields"])
    # Defensive: catalog walked and the type wasn't found despite passing the
    # BUILTIN_EVIDENCE_TYPES check.  Treat as empty (no authorable fields).
    return []


def _validate_field(evidence_type: str, field: object) -> str:
    """Validate that *field* is a verified key for *evidence_type*.

    Returns the field name unchanged on success.  Raises ValueError otherwise.
    The error message names the rejected field and the evidence type so the
    honest-degrade banner (design doc §PR-F3) can surface them verbatim.
    """
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field_constraint payload.field must be a non-empty string")
    known = _resolve_known_fields(evidence_type)
    if field not in known:
        if not known:
            raise ValueError(
                f"field_constraint: evidenceType {evidence_type!r} has no "
                f"verified field vocabulary; cannot author {field!r}. "
                "Producer needs to declare ToolResult.metadata['evidence']."
            )
        raise ValueError(
            f"field_constraint: field {field!r} is not in "
            f"available_fields({evidence_type!r})={known!r}. "
            "Compile refused (honest-degrade)."
        )
    return field


def _safe_predicate_local(field: str) -> str:
    """Return the canonical ``magi:field_<sanitised>`` local-name for *field*.

    Mirrors the runtime ontology's sanitisation so the synthesised predicate
    matches the predicate the data graph actually emits.  Empty/unrepresentable
    keys are rejected (would otherwise cause vacuous shapes).
    """
    safe = _sanitise_field_key(field)
    if safe is None:
        raise ValueError(
            f"field_constraint: field name {field!r} sanitises to empty; "
            "cannot map to a stable magi:field_<key> predicate"
        )
    return safe


def _ttl_literal(value: object) -> str:
    """Serialise a scalar Python value as a Turtle literal.

    Mirrors the type mapping in
    :func:`magi_agent.evidence.shacl_ontology._to_typed_literal` so the
    compile-time literal type matches the runtime data-graph literal type
    (xsd:boolean / xsd:integer / xsd:decimal / xsd:string).  Without the same
    type, ``sh:hasValue`` / ``sh:minInclusive`` will silently never match.
    """
    if isinstance(value, bool):
        # bool BEFORE int — bool is an int subclass in Python.
        return '"true"^^xsd:boolean' if value else '"false"^^xsd:boolean'
    if isinstance(value, int):
        return f'"{value}"^^xsd:integer'
    if isinstance(value, float):
        # Match the ontology: floats use xsd:decimal with str() rendering.
        return f'"{value}"^^xsd:decimal'
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"^^xsd:string'
    # Fallback (mirror ontology): coerce non-scalar to its str() form as xsd:string.
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"^^xsd:string'


_PREFIX_BLOCK: Final[str] = (
    "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
    "@prefix magi: <https://openmagi.ai/ns/evidence#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
    "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
    "\n"
)


def _type_guard_clauses(evidence_type: str) -> tuple[str, str]:
    """Render the ``sh:or`` type-guard pair used to scope a shape to *evidence_type*.

    Returns ``(skip_clause, predicate_clause)`` — both blank-node bodies that
    the caller embeds inside a ``sh:or ( <skip> <constraint> )`` list.

    The skip clause asserts "this record is NOT of *evidence_type*", and the
    constraint clause carries the real predicate-path constraint.  Their
    disjunction means: "either this record doesn't belong to the targeted
    type (so the rule is vacuously satisfied), OR the constraint holds".

    Why a guard instead of ``sh:targetClass``-plus-filter:
      ``sh:targetClass magi:Evidence`` selects *every* Evidence record.  If we
      then added a ``sh:property [ sh:path magi:type; sh:hasValue "X" ]``
      block at the top level, every non-X record would violate that filter —
      the rule would silently block on records it has nothing to say about.
      The ``sh:or`` guard makes the type filter a precondition, not an
      assertion, which is the desired "type-scoped" semantics.
    """
    skip = (
        "        [\n"
        "            sh:not [\n"
        "                a sh:NodeShape ;\n"
        "                sh:property [\n"
        "                    sh:path magi:type ;\n"
        f"                    sh:hasValue {_ttl_literal(evidence_type)} ;\n"
        "                ] ;\n"
        "            ] ;\n"
        "        ]\n"
    )
    # The constraint-clause opening is rendered by the caller; this helper
    # only owns the type-guard skip branch.
    return skip, ""


# ---------------------------------------------------------------------------
# Single-record compilation
# ---------------------------------------------------------------------------


def _compile_single_record(payload: dict[str, Any], operator: str) -> str:
    evidence_type = payload.get("evidenceType")
    if not isinstance(evidence_type, str):
        raise ValueError("field_constraint payload.evidenceType must be a string")
    field_raw = payload.get("field")
    field_name = _validate_field(evidence_type, field_raw)
    predicate_local = _safe_predicate_local(field_name)

    constraint_block = _render_single_constraint(payload, operator)
    skip_clause, _ = _type_guard_clauses(evidence_type)

    # The constraint clause: a NodeShape with a single sh:property carrying
    # the predicate-path constraint and a human-readable message.  Wrapped
    # in an ``sh:or`` against the type-skip clause so the rule is scoped to
    # ``evidence_type`` (records of other types short-circuit pass).
    constraint_clause = (
        "        [\n"
        "            a sh:NodeShape ;\n"
        "            sh:property [\n"
        f"                sh:path magi:field_{predicate_local} ;\n"
        f"{_indent_block(constraint_block, 8)}"
        f'                sh:message "field_constraint: {evidence_type}.{field_name} {operator}" ;\n'
        "            ] ;\n"
        "        ]\n"
    )

    return (
        f"{_PREFIX_BLOCK}"
        "magi:FieldConstraintShape\n"
        "    a sh:NodeShape ;\n"
        "    sh:targetClass magi:Evidence ;\n"
        "    sh:or (\n"
        f"{skip_clause}"
        f"{constraint_clause}"
        "    ) .\n"
    )


def _indent_block(block: str, extra_spaces: int) -> str:
    """Re-indent each non-empty line of *block* by *extra_spaces* additional
    spaces, preserving the trailing newline on each line.  Used to nest
    pre-rendered constraint snippets one extra indentation level deep when
    embedding them inside an ``sh:or`` clause.
    """
    pad = " " * extra_spaces
    lines = block.splitlines(keepends=True)
    return "".join((pad + line) if line.strip() else line for line in lines)


def _render_single_constraint(payload: dict[str, Any], operator: str) -> str:
    """Return the inner sh:property body for the single-record operator."""
    if operator in {"exists", "notExists"}:
        # Existence operators are value-free.
        if operator == "exists":
            return "        sh:minCount 1 ;\n"
        return "        sh:maxCount 0 ;\n"

    if operator not in _VALUE_REQUIRED_OPERATORS:
        # Defensive — already filtered upstream.
        raise ValueError(f"unknown single-record operator {operator!r}")

    if "value" not in payload:
        raise ValueError(
            f"field_constraint operator {operator!r} requires payload.value"
        )
    literal = _ttl_literal(payload["value"])

    if operator == "eq":
        return f"        sh:hasValue {literal} ;\n"
    if operator == "neq":
        return (
            "        sh:not [\n"
            f"            sh:hasValue {literal} ;\n"
            "        ] ;\n"
        )
    if operator == "gt":
        return f"        sh:minExclusive {literal} ;\n"
    if operator == "lt":
        return f"        sh:maxExclusive {literal} ;\n"
    if operator == "ge":
        return f"        sh:minInclusive {literal} ;\n"
    if operator == "le":
        return f"        sh:maxInclusive {literal} ;\n"
    # Unreachable.
    raise ValueError(f"unhandled single-record operator {operator!r}")


# ---------------------------------------------------------------------------
# forEachExistsCovering compilation
# ---------------------------------------------------------------------------


def _compile_for_each_exists_covering(payload: dict[str, Any]) -> str:
    """Compile the cross-record cardinality operator.

    The synthesised shape:

      * Targets ``magi:Evidence`` and filters to ``source.evidenceType``.
      * On the source side, uses ``sh:path magi:field_<source.field>``.
      * Each value of that predicate (the ontology flattens list/tuple
        fields, so each list entry materialises as one triple) must satisfy
        ``sh:qualifiedValueShape`` with ``sh:qualifiedMinCount 1``.
      * The qualified value shape says: "there exists at least one node in
        the data graph whose ``magi:type`` equals the target evidence type
        AND whose ``magi:field_<target.field>`` equals THIS source entry".
        The "covering" relation is enforced via SHACL's
        ``sh:qualifiedValueShape`` + ``sh:targetNode`` / property paths.

    Because SHACL property-shape contexts iterate the FOCUS predicate values
    one at a time, ``sh:qualifiedValueShape`` checks each entry individually
    — exactly the "for each X in source, ∃ Y covering" semantics we want.
    The matching Y is selected by an inverse path that walks back from the
    value to any record carrying that value on the target predicate.
    """
    source = payload.get("source")
    target = payload.get("target")
    if not isinstance(source, dict):
        raise ValueError("forEachExistsCovering: source must be a mapping")
    if not isinstance(target, dict):
        raise ValueError("forEachExistsCovering: target must be a mapping")

    src_type = source.get("evidenceType")
    tgt_type = target.get("evidenceType")
    if not isinstance(src_type, str):
        raise ValueError("forEachExistsCovering: source.evidenceType must be a string")
    if not isinstance(tgt_type, str):
        raise ValueError("forEachExistsCovering: target.evidenceType must be a string")

    src_field = _validate_field(src_type, source.get("field"))
    tgt_field = _validate_field(tgt_type, target.get("field"))

    # ``covering`` is a forward-compatibility slot; v1 only supports
    # "source.entry" (each source entry must equal a target value).
    covering = target.get("covering", "source.entry")
    if covering != "source.entry":
        raise ValueError(
            f"forEachExistsCovering: target.covering={covering!r} not supported "
            "(only 'source.entry' in v1)"
        )

    src_pred_local = _safe_predicate_local(src_field)
    tgt_pred_local = _safe_predicate_local(tgt_field)

    # The qualified value shape is applied per-entry on the source predicate.
    # We assert each entry must equal SOMETHING that, walking the inverse of
    # the target predicate, lands on a node whose magi:type is the target
    # evidence type.  Concretely:
    #
    #   sh:property [
    #     sh:path magi:field_<src_field> ;
    #     sh:qualifiedValueShape [
    #       sh:node [
    #         a sh:NodeShape ;
    #         sh:property [
    #           sh:path [ sh:inversePath magi:field_<tgt_field> ] ;
    #           sh:qualifiedValueShape [
    #             sh:property [
    #               sh:path magi:type ;
    #               sh:hasValue "<TgtType>"^^xsd:string ;
    #             ] ;
    #           ] ;
    #           sh:qualifiedMinCount 1 ;
    #         ] ;
    #       ] ;
    #     ] ;
    #     sh:qualifiedMinCount 1 ;
    #   ] ;
    #
    # Each source-list entry → its own focus node → inverse-path lookup of
    # the target predicate → at least one preimage node typed as the target
    # evidence type.  Pure forall-exists.
    skip_clause, _ = _type_guard_clauses(src_type)

    # The constraint side: SHACL ``sh:node`` on a property shape is the
    # FORALL operator over the path's values — every value of
    # ``magi:field_<src_field>`` (one triple per list entry, see the
    # ontology flattener) must conform to the inner NodeShape.  The inner
    # NodeShape uses an inverse path on ``magi:field_<tgt_field>`` to find
    # records that carry THIS entry on the target predicate, and the
    # qualified value shape narrows them to those typed as the target
    # evidence-type.  Requiring ``sh:qualifiedMinCount 1`` on that inner
    # property completes the EXISTS half.
    #
    # Combined:  ∀ entry ∈ source.field ,  ∃ target-record :
    #              target-record.tgt_field = entry  ∧
    #              target-record.magi:type = tgt_type
    constraint_clause = (
        "        [\n"
        "            a sh:NodeShape ;\n"
        "            sh:property [\n"
        f"                sh:path magi:field_{src_pred_local} ;\n"
        "                sh:node [\n"
        "                    a sh:NodeShape ;\n"
        "                    sh:property [\n"
        f"                        sh:path [ sh:inversePath magi:field_{tgt_pred_local} ] ;\n"
        "                        sh:qualifiedValueShape [\n"
        "                            sh:property [\n"
        "                                sh:path magi:type ;\n"
        f"                                sh:hasValue {_ttl_literal(tgt_type)} ;\n"
        "                            ] ;\n"
        "                        ] ;\n"
        "                        sh:qualifiedMinCount 1 ;\n"
        "                    ] ;\n"
        "                ] ;\n"
        f'                sh:message "field_constraint: each {src_type}.{src_field} covered by {tgt_type}.{tgt_field}" ;\n'
        "            ] ;\n"
        "        ]\n"
    )

    return (
        f"{_PREFIX_BLOCK}"
        "magi:FieldConstraintShape\n"
        "    a sh:NodeShape ;\n"
        "    sh:targetClass magi:Evidence ;\n"
        "    sh:or (\n"
        f"{skip_clause}"
        f"{constraint_clause}"
        "    ) .\n"
    )
