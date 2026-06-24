"""evidence → RDF ontology flattener.

Pure function, deterministic, zero model/LLM calls.
Each EvidenceRecord maps to exactly one RDF node identified by its position
in the input sequence (magi:rec_<index>), giving stable node URIs for the
same input ordering.

Spec: docs/plans/2026-06-18-shacl-PR1-engine-tasks.md Task 1.1
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

import rdflib
from rdflib.namespace import RDF, XSD

from magi_agent.evidence.types import EvidenceRecord

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

MAGI_NS: str = "https://openmagi.ai/ns/evidence#"
MAGI = rdflib.Namespace(MAGI_NS)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitise_field_key(key: str) -> str | None:
    """Return a safe predicate-local-name for *key*, or None to skip.

    Replaces any character that is not alphanumeric or underscore with ``_``.
    Consecutive replacements are collapsed to a single ``_``.  Keys that are
    empty after sanitisation (e.g. an all-whitespace key) are skipped (return
    None).  Leading digits are prefixed with ``_`` to keep the result a valid
    XML local name.

    The transformation is deterministic: identical inputs always produce
    identical outputs.
    """
    sanitised = _UNSAFE_CHAR_RE.sub("_", key)
    # Collapse multiple consecutive underscores to one (O(n), not O(n²))
    sanitised = re.sub(r"_+", "_", sanitised)
    # Strip leading/trailing underscores for cleanliness, but keep at least one char
    sanitised = sanitised.strip("_")
    if not sanitised:
        return None
    # XML local names must not start with a digit
    if sanitised[0].isdigit():
        sanitised = "_" + sanitised
    return sanitised


def _to_typed_literal(value: object) -> rdflib.Literal:
    """Convert a scalar Python value to an rdflib Literal with the correct XSD datatype.

    Type mapping (spec §Task 1.1):
      bool  → xsd:boolean   (must come before int — bool is a subclass of int)
      int   → xsd:integer
      float → xsd:decimal
      str   → xsd:string
      other → str() coercion → xsd:string  (deterministic; no metadata annotation needed)
    """
    if isinstance(value, bool):
        return rdflib.Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return rdflib.Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        # Use Decimal for xsd:decimal round-trip fidelity
        return rdflib.Literal(Decimal(str(value)), datatype=XSD.decimal)
    if isinstance(value, str):
        return rdflib.Literal(value, datatype=XSD.string)
    # Non-scalar fallback: coerce to string, deterministically
    return rdflib.Literal(str(value), datatype=XSD.string)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evidence_records_to_graph(records: Iterable[EvidenceRecord]) -> rdflib.Graph:
    """Map each EvidenceRecord to one RDF node and return the populated graph.

    Node structure for record at index *i*::

        magi:rec_<i>
            a                    magi:Evidence ;
            magi:type            "<record.type>"^^xsd:string ;
            magi:status          "<record.status>"^^xsd:string ;
            magi:field_<key>     <typed-literal>   # for each k, v in record.fields

    Determinism:
        Node URIs are ``magi:rec_<index>`` where *index* is the zero-based
        position in the input sequence.  Same records in the same order →
        same graph every call.

    Field key sanitisation:
        Unsafe characters (not ``[A-Za-z0-9_]``) are replaced with ``_`` and
        collapsed.  Keys that collapse to the empty string are silently skipped.
        The sanitisation is deterministic.

    List / tuple field values (F2, 2026-06-23):
        A list or tuple value is FLATTENED into one triple per entry under
        the same ``magi:field_<key>`` predicate.  This lets SHACL ``sh:path``
        traversal iterate over each element (e.g. the GitDiff.changedFiles
        list of relative paths) instead of seeing one opaque ``str(tuple)``
        literal.  Each entry is typed via ``_to_typed_literal`` so a tuple
        of strings produces multiple ``xsd:string`` literals.  Empty
        list/tuple → zero triples (caller's ``sh:minCount 1`` then fires).
        Nested non-scalar entries (dicts, list-of-lists) fall through to
        ``str()`` coercion, preserving the prior YAGNI behaviour.

    Other non-scalar field values (dicts, …) are coerced via ``str()`` to an
    ``xsd:string`` literal; no metadata annotation is added (YAGNI).

    Zero model/LLM calls — pure function.
    """
    g = rdflib.Graph()
    g.bind("magi", MAGI)

    for idx, record in enumerate(records):
        node = MAGI[f"rec_{idx}"]

        # rdf:type
        g.add((node, RDF.type, MAGI.Evidence))

        # magi:type — the evidence type string
        g.add((node, MAGI.type, rdflib.Literal(record.type, datatype=XSD.string)))

        # magi:status
        g.add((node, MAGI.status, rdflib.Literal(record.status, datatype=XSD.string)))

        # magi:field_<key> for each field
        for key, value in record.fields.items():
            safe_key = _sanitise_field_key(key)
            if safe_key is None:
                continue  # skip un-representable keys deterministically
            predicate = MAGI[f"field_{safe_key}"]
            # F2: flatten list/tuple values into one triple per entry so
            # SHACL ``sh:path`` traversal can iterate (e.g. GitDiff.changedFiles).
            # bytes/bytearray/memoryview are NOT iterables we want to expand
            # here — they would already be rejected upstream by
            # ``_freeze_metadata_value``; the isinstance check on list|tuple
            # keeps the expansion to operator-meaningful sequences.
            if isinstance(value, list | tuple):
                for entry in value:
                    g.add((node, predicate, _to_typed_literal(entry)))
                continue
            g.add((node, predicate, _to_typed_literal(value)))

    return g
