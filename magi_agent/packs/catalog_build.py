"""Build the live ``CompileRecipePackCatalog`` from loaded pack primitives (D4).

The catalog is the union of all loaded packs' provides refs, FLAT — there is no
first-party-only tier (§1 "no privilege"). It is the live-path replacement for
``CompileRecipePackCatalog.default()`` (the hardcode the blueprint removes in
later phases).

Mapping (see 02-phase1 doc, "catalog mapping decision"):
    tool              -> toolRefs
    connector         -> connectorRefs
    validator         -> validatorRefs
    harness           -> harnessRefs
    evidence_producer -> evidenceProducerRefs
    control_plane     -> pluginRefs
    callback          -> pluginRefs
    recipe            -> (not a catalog ref; registered as a spec via the loader)

For OSS local full-trust the hard-invariant tiers are empty (the hosted floor is
layered separately and is out of scope).
"""
from __future__ import annotations

from collections.abc import Iterable

from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.loader import LoadedPrimitive

# provides type -> the catalog field it contributes to. Order in this dict is the
# pluginRefs emission order (control_plane before callback) when both share a field.
_FIELD_FOR_TYPE: dict[str, str] = {
    "tool": "tool_refs",
    "connector": "connector_refs",
    "validator": "validator_refs",
    "harness": "harness_refs",
    "evidence_producer": "evidence_producer_refs",
    "control_plane": "plugin_refs",
    "callback": "plugin_refs",
}


def _dedup_preserve_order(refs: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return tuple(out)


def build_catalog(primitives: Iterable[LoadedPrimitive]) -> CompileRecipePackCatalog:
    """Union loaded primitives' refs into a flat ``CompileRecipePackCatalog``."""
    buckets: dict[str, list[str]] = {field: [] for field in set(_FIELD_FOR_TYPE.values())}
    for primitive in primitives:
        field = _FIELD_FOR_TYPE.get(primitive.type)
        if field is None:  # recipe (spec) and any non-catalog type
            continue
        buckets[field].append(primitive.ref)

    return CompileRecipePackCatalog(
        toolRefs=_dedup_preserve_order(buckets["tool_refs"]),
        connectorRefs=_dedup_preserve_order(buckets["connector_refs"]),
        validatorRefs=_dedup_preserve_order(buckets["validator_refs"]),
        harnessRefs=_dedup_preserve_order(buckets["harness_refs"]),
        evidenceProducerRefs=_dedup_preserve_order(buckets["evidence_producer_refs"]),
        pluginRefs=_dedup_preserve_order(buckets["plugin_refs"]),
        # OSS local full-trust: no hosted hard-invariant floor.
        hardInvariantRefs=(),
        requiredHardInvariantRefs=(),
    )
