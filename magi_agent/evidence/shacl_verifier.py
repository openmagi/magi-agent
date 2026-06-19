"""SHACL constraint verifier — pure function, zero model/LLM calls.

Wraps pyshacl.validate with a strict fail-safe contract:
  - Any exception (shape parse failure, pyshacl error, flattening error)
    → status='unknown', never status='failed'.
  - Violations are extracted from the SHACL ValidationReport graph and
    stable-sorted so the same input always produces an identical record.
  - inference="none" is fixed; RDFS/OWL inference is never applied.
  - observed_at is INJECTED by the caller (determinism + testability).

DoS guards (design doc §6):
  - _MAX_SHAPE_BYTES: operator-supplied shape TTL is capped at 100 KB.
    Larger shapes are rejected fail-safe (status='unknown').
  - sh:sparql detection: shapes containing sh:sparql constraints (arbitrary
    SPARQL execution) are rejected fail-safe before calling pyshacl.  This
    prevents both unbounded execution and SPARQL-injection vectors.
  - _VALIDATE_TIMEOUT_S: pyshacl.validate is run in a thread with a wall-clock
    timeout.  Pathological inputs that cause pyshacl to hang return
    status='unknown' without blocking the caller.

Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.2
"""
from __future__ import annotations

import concurrent.futures
from collections.abc import Iterable
from typing import Literal

import rdflib

from magi_agent.evidence.shacl_ontology import evidence_records_to_graph
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource

# ---------------------------------------------------------------------------
# SHACL / RDF namespaces
# ---------------------------------------------------------------------------

_SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
_SHACL_VERIFIER_NAME = "shacl-constraint-verifier"

# ---------------------------------------------------------------------------
# DoS guard constants (design doc §6)
# ---------------------------------------------------------------------------

# Maximum byte length of operator-supplied shape_ttl (UTF-8 encoded).
# Shapes larger than this are rejected fail-safe rather than parsed.
_MAX_SHAPE_BYTES: int = 100_000

# Wall-clock timeout in seconds for the pyshacl.validate call.
# Pathological shapes (e.g. SPARQL constraints that slip through, deep recursion)
# cannot hang the process for longer than this.
_VALIDATE_TIMEOUT_S: int = 10

# ---------------------------------------------------------------------------
# Internal seam (monkeypatch target for test 4)
# ---------------------------------------------------------------------------


def _pyshacl_validate(
    data_graph: rdflib.Graph,
    shacl_graph: rdflib.Graph,
    inference: Literal["none"],
) -> tuple[bool, rdflib.Graph, str]:
    """Thin wrapper around pyshacl.validate so tests can monkeypatch it.

    M1: ``inference`` is typed ``Literal["none"]`` to enforce the no-inference
    invariant at the type level, not just by caller discipline.
    """
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


def _min_value(
    g: rdflib.Graph,
    subject: rdflib.term.Node,
    predicate: rdflib.term.URIRef,
) -> rdflib.term.Node | None:
    """Return the lexicographically smallest object for (subject, predicate, ?).

    F1 fix: multi-valued predicates (e.g. sh:resultMessage) must be resolved
    deterministically regardless of Python hash seed / set-iteration order.
    We collect ALL objects, stringify them, and return the minimum — giving a
    stable selection that is identical across processes and hash seeds.
    """
    objects = list(g.objects(subject, predicate))
    if not objects:
        return None
    # Sort by string representation and return the node whose str() is minimal.
    return min(objects, key=lambda o: str(o))


def _extract_violations(report_graph: rdflib.Graph) -> tuple[dict[str, object], ...]:
    """Extract violation dicts from a SHACL ValidationReport graph.

    Each violation is a dict with keys: focusNode, resultPath, value, message,
    severity.  The tuple is stable-sorted so the same graph always yields the
    same order.

    F4 fix: all sh:ValidationResult nodes are collected regardless of
    sh:resultSeverity (Violation, Warning, Info).  Previously only
    sh:Violation-severity nodes were iterated, causing sh:Warning shapes to
    produce status='failed' with violations=() — a silent block.  Now the
    severity is included in the violation dict and the result is always
    non-empty when conforms=False.
    """
    violations: list[dict[str, object]] = []

    # Iterate over ALL result nodes linked via sh:result from the report,
    # regardless of severity — Violation, Warning, Info, etc.
    for result_node in report_graph.subjects(_SH.resultSeverity):
        focus_node = _min_value(report_graph, result_node, _SH.focusNode)
        result_path = _min_value(report_graph, result_node, _SH.resultPath)
        value = _min_value(report_graph, result_node, _SH.value)
        # F1: use _min_value instead of _first_value for sh:resultMessage so that
        # shapes with multiple sh:message values always yield the same message.
        message = _min_value(report_graph, result_node, _SH.resultMessage)
        severity_node = _min_value(report_graph, result_node, _SH.resultSeverity)

        # Extract the local name of the severity URI for a human-friendly string
        # (e.g. "http://www.w3.org/ns/shacl#Warning" → "Warning").
        severity_str: str | None = None
        if severity_node is not None:
            sev_uri = str(severity_node)
            severity_str = sev_uri.rsplit("#", 1)[-1] if "#" in sev_uri else sev_uri.rsplit("/", 1)[-1]

        violations.append(
            {
                "focusNode": _node_str(focus_node),
                "resultPath": _node_str(result_path),
                "value": _node_str(value),
                "message": _node_str(message),
                "severity": severity_str,
            }
        )

    # Stable sort: sort by the string representation of each dict's items
    violations.sort(key=lambda v: (
        str(v.get("focusNode", "")),
        str(v.get("resultPath", "")),
        str(v.get("value", "")),
        str(v.get("message", "")),
        str(v.get("severity", "")),
    ))
    return tuple(violations)


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
    # F2a — byte-size cap: reject oversized shapes before parsing
    # ------------------------------------------------------------------
    shape_bytes = len(shape_ttl.encode("utf-8"))
    if shape_bytes > _MAX_SHAPE_BYTES:
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": (
                    f"shape_ttl too large: {shape_bytes} bytes exceeds "
                    f"_MAX_SHAPE_BYTES={_MAX_SHAPE_BYTES}. "
                    "Oversized shapes are rejected fail-safe to prevent DoS."
                ),
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "shape_too_large"},
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
    # F2b — reject shapes containing sh:sparql (arbitrary SPARQL execution)
    # Rationale: sh:sparql constraints can execute arbitrary SPARQL queries,
    # which may hang, consume unbounded resources, or perform injection attacks.
    # We reject them fail-safe before calling pyshacl, regardless of whether
    # the timeout guard would eventually catch it.
    # ------------------------------------------------------------------
    if any(True for _ in shacl_graph.subject_objects(_SH.sparql)):
        return EvidenceRecord(
            type="custom:ShaclConstraintCheck",
            status="unknown",
            observedAt=observed_at,
            source=_source,
            fields={
                "ruleId": rule_id,
                "error": (
                    "shape_ttl contains sh:sparql constraints, which are not supported. "
                    "Only deterministic/bounded SHACL constraints are permitted."
                ),
                "conforms": None,
            },
            metadata={"failSafe": True, "errorKind": "sparql_constraint_rejected"},
        )

    # ------------------------------------------------------------------
    # Step 3 — run pyshacl validation with wall-clock timeout (F2c)
    # ------------------------------------------------------------------
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _pyshacl_validate,
                data_graph,
                shacl_graph,
                "none",
            )
            try:
                conforms, report_graph, report_text = future.result(
                    timeout=_VALIDATE_TIMEOUT_S
                )
            except concurrent.futures.TimeoutError:
                return EvidenceRecord(
                    type="custom:ShaclConstraintCheck",
                    status="unknown",
                    observedAt=observed_at,
                    source=_source,
                    fields={
                        "ruleId": rule_id,
                        "error": (
                            f"pyshacl.validate timed out after {_VALIDATE_TIMEOUT_S}s. "
                            "Shape rejected fail-safe."
                        ),
                        "conforms": None,
                    },
                    metadata={"failSafe": True, "errorKind": "validate_timeout"},
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
