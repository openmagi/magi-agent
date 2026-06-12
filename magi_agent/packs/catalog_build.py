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

from collections.abc import Iterable, Mapping

from magi_agent.packs.loader import LoadedPrimitive
from magi_agent.packs.types import CompileRecipePackCatalog

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


def resolve_live_catalog(
    *,
    env: Mapping[str, str] | None = None,
) -> CompileRecipePackCatalog:
    """Build the live catalog from loaded pack manifests (D4).

    Replaces the hardcoded ``CompileRecipePackCatalog.default()`` on the live
    ``None``-catalog path: discovers packs (bundled first-party + user dirs),
    reads their static ``provides`` refs, and folds them into a flat catalog —
    no first-party-only tier (§1 no privilege). A user pack's refs land in
    exactly the same fields as first-party's.

    Re-homed from the deleted ``magi_agent/authoring/compiler.py``: the catalog
    is kernel-owned now, with zero authoring dependence.

    The legacy ``.default()`` reference floor is PRESERVED (unioned in) so that
    existing recipe-ref validation (and the hosted hard-invariant floor the model
    validator at ``CompileRecipePackCatalog._validate_required_hard_invariants``
    requires) keeps passing — this flip adds pack-discovered refs without dropping
    any reference the live runtime already validated against. Discovery failures
    fail open to the static default (the runtime stays usable with no packs).
    """
    floor = CompileRecipePackCatalog.default()
    try:
        from magi_agent.packs.discovery import (  # noqa: PLC0415
            default_search_bases,
            discover_pack_files,
            load_packs_config,
            resolve_enabled_packs,
        )
        from magi_agent.packs.loader import RecordingSink, load_packs  # noqa: PLC0415

        discovered = discover_pack_files(default_search_bases())
        enabled = resolve_enabled_packs(discovered, load_packs_config())
        sink = RecordingSink()
        result = load_packs(enabled, sink)
        pack_catalog = build_catalog(result.primitives)
    except Exception:
        return floor

    return CompileRecipePackCatalog(
        connectorRefs=_dedup_preserve_order(floor.connector_refs + pack_catalog.connector_refs),
        toolRefs=_dedup_preserve_order(floor.tool_refs + pack_catalog.tool_refs),
        pluginRefs=_dedup_preserve_order(floor.plugin_refs + pack_catalog.plugin_refs),
        validatorRefs=_dedup_preserve_order(floor.validator_refs + pack_catalog.validator_refs),
        harnessRefs=_dedup_preserve_order(floor.harness_refs + pack_catalog.harness_refs),
        requiredEvidenceRefs=floor.required_evidence_refs,
        evidenceProducerRefs=_dedup_preserve_order(
            floor.evidence_producer_refs + pack_catalog.evidence_producer_refs
        ),
        approvalAuthorityRefs=floor.approval_authority_refs,
        # Preserve the hosted hard-invariant floor (out of scope for pack refs).
        hardInvariantRefs=floor.hard_invariant_refs,
        requiredHardInvariantRefs=floor.required_hard_invariant_refs,
    )
