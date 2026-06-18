"""SHACL constraint verifier — pure function, zero model/LLM calls.

Wraps pyshacl.validate with a strict fail-safe contract:
  - Any exception (shape parse failure, pyshacl error, flattening error)
    → status='unknown', never status='failed'.
  - Violations are extracted from the SHACL ValidationReport graph and
    stable-sorted so the same input always produces an identical record.
  - inference="none" is fixed; RDFS/OWL inference is never applied.
  - observed_at is INJECTED by the caller (determinism + testability).

Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.2
"""
from __future__ import annotations

from collections.abc import Iterable

import rdflib

from magi_agent.evidence.shacl_ontology import evidence_records_to_graph
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource

# ---------------------------------------------------------------------------
# SHACL / RDF namespaces
# ---------------------------------------------------------------------------

_SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
_SHACL_VERIFIER_NAME = "shacl-constraint-verifier"

# ---------------------------------------------------------------------------
# Internal seam (monkeypatch target for test 4)
# ---------------------------------------------------------------------------


def _pyshacl_validate(
    data_graph: rdflib.Graph,
    shacl_graph: rdflib.Graph,
    inference: str,
) -> tuple[bool, rdflib.Graph, str]:
    """Thin wrapper around pyshacl.validate so tests can monkeypatch it."""
    import pyshacl  # local import — heavy dep, only load when needed

    conforms, report_graph, report_text = pyshacl.validate(
        data_graph,
        shacl_graph=shacl_graph,
        inference=inference,
    )
    return conforms, report_graph, report_text


# ---------------------------------------------------------------------------
# Violation extraction
# ---------------------------------------------------------------------------


def _extract_violations(report_graph: rdflib.Graph) -> tuple[dict[str, object], ...]:
    """Extract violation dicts from a SHACL ValidationReport graph.

    Each violation is a dict with keys: focusNode, resultPath, value, message.
    The tuple is stable-sorted so the same graph always yields the same order.
    """
    # sh:ValidationResult nodes are linked from the sh:ValidationReport via sh:result
    violations: list[dict[str, object]] = []

    for result_node in report_graph.subjects(_SH.resultSeverity, _SH.Violation):
        focus_node = _first_value(report_graph, result_node, _SH.focusNode)
        result_path = _first_value(report_graph, result_node, _SH.resultPath)
        value = _first_value(report_graph, result_node, _SH.value)
        message = _first_value(report_graph, result_node, _SH.resultMessage)

        violations.append(
            {
                "focusNode": _node_str(focus_node),
                "resultPath": _node_str(result_path),
                "value": _node_str(value),
                "message": _node_str(message),
            }
        )

    # Stable sort: sort by the string representation of each dict's items
    violations.sort(key=lambda v: (
        str(v.get("focusNode", "")),
        str(v.get("resultPath", "")),
        str(v.get("value", "")),
        str(v.get("message", "")),
    ))
    return tuple(violations)


def _first_value(
    g: rdflib.Graph,
    subject: rdflib.term.Node,
    predicate: rdflib.term.URIRef,
) -> rdflib.term.Node | None:
    """Return the first object for (subject, predicate, ?) or None."""
    for obj in g.objects(subject, predicate):
        return obj
    return None


def _node_str(node: rdflib.term.Node | None) -> str | None:
    """Convert an RDF node to a stable string, or None if absent."""
    if node is None:
        return None
    return str(node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_shacl_rule(
    records: Iterable[EvidenceRecord],
    shape_ttl: str,
    rule_id: str,
    *,
    observed_at: int,
) -> EvidenceRecord:
    """Validate *records* against a SHACL shape and emit an EvidenceRecord.

    Parameters
    ----------
    records:
        Evidence records to validate. Consumed once; may be a generator.
    shape_ttl:
        SHACL shape serialised as Turtle text.
    rule_id:
        Stable identifier for this rule (stored in ``fields["ruleId"]``).
    observed_at:
        Unix-epoch millisecond timestamp injected by the caller.
        NEVER call time.time() or datetime.now() here — determinism.

    Returns
    -------
    EvidenceRecord
        * status="ok"      — pyshacl validation passed (conforms=True).
        * status="failed"  — pyshacl found SHACL violations (conforms=False).
        * status="unknown" — any internal exception; fields["error"] is set.
                             NEVER status="failed" for internal errors.
    """
    _source = EvidenceSource(
        kind="verifier",
        verifierName=_SHACL_VERIFIER_NAME,
    )

    # ------------------------------------------------------------------
    # Step 1 — build data graph from evidence records
    # ------------------------------------------------------------------
    try:
        data_graph = evidence_records_to_graph(records)
    except Exception as exc:  # noqa: BLE001
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": f"evidence_records_to_graph failed: {exc}",
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "flatten_error"},
        )

    # ------------------------------------------------------------------
    # Step 2 — parse the SHACL shape
    # ------------------------------------------------------------------
    try:
        shacl_graph = rdflib.Graph()
        shacl_graph.parse(data=shape_ttl, format="turtle")
    except Exception as exc:  # noqa: BLE001
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": f"shape_ttl parse failed: {exc}",
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "shape_parse_error"},
        )

    # ------------------------------------------------------------------
    # Step 3 — run pyshacl validation
    # ------------------------------------------------------------------
    try:
        conforms, report_graph, report_text = _pyshacl_validate(
            data_graph,
            shacl_graph=shacl_graph,
            inference="none",
        )
    except Exception as exc:  # noqa: BLE001
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": f"pyshacl.validate failed: {exc}",
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "pyshacl_error"},
        )

    # ------------------------------------------------------------------
    # Step 4 — extract violations (stable-sorted)
    # ------------------------------------------------------------------
    try:
        violations = _extract_violations(report_graph)
    except Exception as exc:  # noqa: BLE001
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": f"violation extraction failed: {exc}",
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "extraction_error"},
        )

    # ------------------------------------------------------------------
    # Step 5 — emit the verdict record
    # ------------------------------------------------------------------
    return EvidenceRecord(
        type="custom:ShaclConstraintCheck",
        status="ok" if conforms else "failed",
        observedAt=observed_at,
        source=_source,
        fields={
            "ruleId": rule_id,
            "conforms": conforms,
            "violations": violations,
        },
        metadata={
            "ruleId": rule_id,
            "inference": "none",
            "violationCount": len(violations),
        },
    )


__all__ = ["run_shacl_rule"]
