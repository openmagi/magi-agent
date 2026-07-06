"""Fold kernel-loaded ``recipe`` provides into the recipe-compile PackRegistry.

The neutral pack kernel (``magi_agent/packs/``) discovers ``pack.toml`` manifests;
a ``[[provides]] type="recipe"`` entry points at a spec that IS the compiler's own
:class:`magi_agent.recipes.compiler.RecipePackManifest` (no structural bridge to
build). This module reads those recipe specs through the kernel's own discovery
(``packs.discovery.discover_pack_files`` — declarative only, so the recipe-compile
path never imports any pack impl) and folds them into the compile registry.

Trust boundary (OSS local; the hosted floor is layered separately):

* **default-OFF** — gated by ``MAGI_KERNEL_RECIPE_PACKS_ENABLED``. OFF returns
  exactly ``PackRegistry.with_first_party_packs()`` (byte-identical baseline).
* **trusted vs untrusted by provenance** — packs discovered from the BUNDLED
  first-party base are trusted and register as-is (a bundled recipe pack may
  legitimately use the first-party id namespace and ``defaultEnabled``). Packs
  from the USER dirs (``~/.magi/packs`` / ``<cwd>/.magi/packs``) are untrusted
  third-party content and must pass the **compose-only** checks below or they are
  dropped. R2 ref-closure (a post-load pass, ``_drop_unresolved_external_recipe_packs``)
  additionally drops any external pack whose declared refs do not resolve in the
  admitted recipe-pack ref universe; first-party packs are never dropped.
* **first-party-wins** — first-party packs register first; a kernel pack whose
  ``pack_id`` collides is dropped (``register`` raises), so it cannot shadow a
  first-party pack. The R1 ``ext.`` namespace check makes shadowing of
  first-party ids structurally impossible for untrusted packs as well.
* **fail-closed-to-first-party** — any discovery/parse error drops the offending
  pack (or, at the outer boundary, the whole external contribution) and keeps the
  first-party-only registry; compilation is never halted by external packs.
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Iterable, Mapping
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from magi_agent.config.flags import flag_bool, flag_profile_bool
from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest

logger = logging.getLogger(__name__)

MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV = "MAGI_KERNEL_RECIPE_PACKS_ENABLED"
MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV = "MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED"

_EXTERNAL_RECIPE_NAMESPACE_PREFIX = "ext."
RECIPE_ENTRY_POINT_GROUP = "magi.recipes"

# R2 ref-closure families (PR5). Each entry is a ``RecipePackManifest`` ref field
# whose values must close against the declared-ref universe of the admitted
# recipe-pack set. Recipe-level refs (tool/validator/evidence/instruction/...) are
# an authoring abstraction with NO concrete primitive-registry resolution source:
# the first-party packs declare a namespace (``tool:file.read``,
# ``validator:research:citation-support``, ``instruction:...``) that the
# materializer passes through as opaque labels, never resolving them against the
# loaded PrimitiveRegistry / CompileRecipePackCatalog. The honest closure universe
# is therefore the union, per family, of every ref DECLARED by the admitted packs
# (first-party packs define the universe and so always close; a publisher that
# ships the matching primitive declares the ref in the same bundle and so closes).
# ``depends_on_pack_ids`` is intentionally NOT here: it already closes against the
# recipe registry at selection time (``_dependency_unavailable`` in compiler.py).
_REF_CLOSURE_FAMILIES: tuple[str, ...] = (
    "tool_refs",
    "validator_refs",
    "evidence_refs",
    "instruction_refs",
    "approval_gate_refs",
    "callback_refs",
    "checkpoint_refs",
    "audit_refs",
    "granted_tool_names",
)

__all__ = [
    "MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV",
    "MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV",
    "RECIPE_ENTRY_POINT_GROUP",
    "build_recipe_ref_universe",
    "build_runtime_pack_registry",
    "parse_recipe_manifest",
    "recipe_pack_ref_closure_reason",
    "validate_external_recipe_pack",
]


def parse_recipe_manifest(spec_path: Path) -> RecipePackManifest | None:
    """Parse a recipe spec TOML into a ``RecipePackManifest``, or ``None`` on error."""

    try:
        with open(spec_path, "rb") as handle:
            raw = tomllib.load(handle)
        return RecipePackManifest.model_validate(raw)
    except Exception:  # noqa: BLE001 - fail-closed: a bad spec drops the pack
        return None


def validate_external_recipe_pack(manifest: RecipePackManifest) -> str:
    """Return ``""`` when an UNTRUSTED recipe pack is admissible, else a reason.

    Compose-only trust boundary for third-party (user-dir) recipe packs. Bundled
    first-party packs bypass this (see module docstring). R2 ref-closure is a
    separate post-load pass (``recipe_pack_ref_closure_reason`` /
    ``_drop_unresolved_external_recipe_packs``) because it needs the full admitted
    ref universe, which is not available until every pack is registered.
    """

    pack_id = manifest.pack_id
    # R1 — namespace: an untrusted pack must be ``ext.``-prefixed (a publisher uses
    # an ``ext.<publisher>.<name>`` sub-namespace), so it can never claim a
    # first-party id. ("/" is not a valid recipe pack id.)
    if not pack_id.startswith(_EXTERNAL_RECIPE_NAMESPACE_PREFIX):
        return "r1_namespace_required"
    # R4 — external packs cannot assert hard-safety authority.
    if manifest.hard_safety:
        return "r4_hard_safety_blocked"
    # R6 — external packs cannot claim runtime-primitive ownership.
    if manifest.adk_primitive_ownership or manifest.openmagi_boundary_ownership:
        return "r6_ownership_blocked"
    # R7 — external packs cannot silently default-enable (promote_as_default at
    # compiler.py auto-selects defaultEnabled packs globally); explicit selection
    # only.
    if manifest.default_enabled:
        return "r7_default_enabled_blocked"
    return ""


# Catalog-default fields contribute their known refs to these recipe families.
# ``CompileRecipePackCatalog.default()`` is the kernel-owned canonical known-ref
# set (the live runtime validates primitive refs against the catalog), so its refs
# are part of the closure universe alongside the first-party recipe-pack refs.
_CATALOG_FIELD_TO_FAMILY: tuple[tuple[str, str], ...] = (
    ("tool_refs", "tool_refs"),
    ("validator_refs", "validator_refs"),
    ("evidence_producer_refs", "evidence_refs"),
    ("required_evidence_refs", "evidence_refs"),
)


def build_recipe_ref_universe(
    packs: Iterable[RecipePackManifest],
) -> dict[str, frozenset[str]]:
    """Per-family known-ref universe for R2 closure: declared refs + catalog (R2).

    The closure universe against which ``recipe_pack_ref_closure_reason`` resolves
    an external pack's refs. It is the union, per family, of (a) every ref the
    admitted recipe packs DECLARE and (b) the kernel-owned canonical known-ref set
    ``CompileRecipePackCatalog.default()``. Built from the trusted pack set after
    all packs are registered (load-order safe: a pack may reference a ref another
    pack declares later). First-party packs are part of ``packs`` and contribute
    every ref they declare, which is why first-party packs always close.
    """

    from magi_agent.packs.types import CompileRecipePackCatalog

    universe: dict[str, set[str]] = {family: set() for family in _REF_CLOSURE_FAMILIES}
    for pack in packs:
        for family in _REF_CLOSURE_FAMILIES:
            universe[family].update(getattr(pack, family))
    catalog = CompileRecipePackCatalog.default()
    for catalog_field, family in _CATALOG_FIELD_TO_FAMILY:
        universe[family].update(getattr(catalog, catalog_field))
    return {family: frozenset(refs) for family, refs in universe.items()}


def recipe_pack_ref_closure_reason(
    manifest: RecipePackManifest,
    universe: Mapping[str, frozenset[str]],
) -> str:
    """Return ``""`` when every R2 ref of ``manifest`` resolves, else a reason.

    A ref is resolved when it appears in the closure universe for its family. A
    dangling ref (declared by no admitted pack) is unsafe: an external pack with a
    dangling ref is dropped fail-closed by the caller; a first-party pack with a
    dangling ref is only warned about (first-party behavior is never changed).
    """

    for family in _REF_CLOSURE_FAMILIES:
        known = universe.get(family, frozenset())
        for ref in getattr(manifest, family):
            if ref not in known:
                return "r2_unresolved_ref"
    return ""


def _coerce_entry_point_payload(value: Any) -> dict | None:
    """Coerce an ``entry_points`` payload to a recipe-manifest dict, or ``None``.

    Honesty / security: ``EntryPoint.load()`` imports the publisher's module, which
    runs its top-level code (the standard Python distribution-tool model — pip-
    installed plugins are *installation-trusted*, like pytest plugins). We restrict
    accepted payloads to inert DATA shapes (dict / Pydantic model with
    ``model_dump``) and **skip callable / code-carrying payloads** so a published
    plugin cannot smuggle a tool/control invocation through the recipe surface — it
    can only contribute a declarative recipe manifest. This is intentionally
    self-host-trust only and gated by a separate default-OFF flag.
    """

    if value is None or callable(value):
        return None
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True)
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def _entry_point_recipe_manifests(*, group: str) -> list[RecipePackManifest]:
    """Discover external recipe manifests via Python ``entry_points``.

    Each accepted manifest is parsed through ``RecipePackManifest.model_validate``
    and otherwise dropped. Any per-entry failure (import error, validation error,
    callable payload) is skipped with a warning; the whole boundary is fail-closed
    so the rest of the registry build is never halted by a bad publisher.
    """

    manifests: list[RecipePackManifest] = []
    try:
        entry_points = tuple(importlib_metadata.entry_points(group=group))
    except Exception:  # noqa: BLE001 - importlib_metadata absent/broken → empty
        return manifests
    for ep in entry_points:
        loader = getattr(ep, "load", None)
        if not callable(loader):
            continue
        try:
            value = loader()
        except Exception:  # noqa: BLE001 - a broken publisher never poisons others
            logger.warning("recipe entry_point %r failed to load", getattr(ep, "name", "?"))
            continue
        payload = _coerce_entry_point_payload(value)
        if payload is None:
            # Callable / code-carrying / unknown-shape payloads are intentionally
            # skipped: the recipe surface is declarative-only.
            logger.warning(
                "recipe entry_point %r skipped (non-data payload)",
                getattr(ep, "name", "?"),
            )
            continue
        try:
            manifest = RecipePackManifest.model_validate(payload)
        except Exception:  # noqa: BLE001 - a malformed manifest drops, never raises
            logger.warning(
                "recipe entry_point %r dropped (invalid manifest)",
                getattr(ep, "name", "?"),
            )
            continue
        manifests.append(manifest)
    return manifests


def _register_recipe_pack(
    registry: PackRegistry,
    manifest: RecipePackManifest,
    *,
    trusted: bool,
    external_pack_ids: set[str] | None = None,
) -> None:
    if not trusted:
        reason = validate_external_recipe_pack(manifest)
        if reason:
            logger.warning(
                "external recipe pack %r dropped (%s)", manifest.pack_id, reason
            )
            return
    try:
        registry.register(manifest)
    except ValueError:
        # Colliding pack_id: first-party (registered first) wins; drop so a kernel
        # pack cannot shadow first-party.
        logger.warning(
            "kernel recipe pack %r dropped (pack_id collision with first-party)",
            manifest.pack_id,
        )
        return
    # R2 ref-closure (PR5) is enforced in a single post-load pass once every pack
    # is registered (load-order safe), so remember which packs are external here.
    if not trusted and external_pack_ids is not None:
        external_pack_ids.add(manifest.pack_id)


def _drop_unresolved_external_recipe_packs(
    registry: PackRegistry, external_pack_ids: set[str]
) -> PackRegistry:
    """R2 ref-closure post-pass: drop external packs with a dangling ref (PR5).

    Run AFTER all packs are registered. The closure universe is the union of every
    ref declared by the TRUSTED packs only (first-party + bundled-trusted). It must
    exclude the external candidates' own refs: a recipe manifest cannot distinguish
    "a ref I provide" from "a ref I merely reference" (every field is a reference),
    so letting an external pack contribute to the universe would let it self-bless
    a dangling ref. An external pack therefore closes only by composing over refs
    that genuinely exist in the trusted runtime. First-party / trusted packs are
    never dropped (they DEFINE the universe and so always close); a (hypothetical)
    trusted dangling ref is only warned about.
    """

    if not external_pack_ids:
        return registry
    trusted_universe = build_recipe_ref_universe(
        pack for pack in registry.values() if pack.pack_id not in external_pack_ids
    )
    drop: set[str] = set()
    for pack in registry.values():
        reason = recipe_pack_ref_closure_reason(pack, trusted_universe)
        if not reason:
            continue
        if pack.pack_id in external_pack_ids:
            logger.warning("external recipe pack %r dropped (%s)", pack.pack_id, reason)
            drop.add(pack.pack_id)
        else:
            # Trusted: surface the latent bug but never drop (no behavior change).
            logger.warning(
                "trusted recipe pack %r declares an unresolved ref (%s); kept",
                pack.pack_id,
                reason,
            )
    if not drop:
        return registry
    return PackRegistry(
        pack for pack in registry.values() if pack.pack_id not in drop
    )


def build_runtime_pack_registry(env: Mapping[str, str] | None = None) -> PackRegistry:
    """First-party recipe packs, plus kernel-loaded ``recipe`` provides when ON.

    Returns ``PackRegistry.with_first_party_packs()`` unchanged when the flag is
    OFF (byte-identical baseline). When ON, kernel-discovered recipe packs are
    registered AFTER first-party (first-party-wins on a ``pack_id`` collision),
    with the compose-only trust boundary applied to untrusted (user-dir) packs;
    a final R2 ref-closure pass drops any external pack with a dangling ref.
    """

    registry = PackRegistry.with_first_party_packs()
    if not flag_profile_bool(MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV, env=env):
        return registry

    external_pack_ids: set[str] = set()

    try:
        from magi_agent.packs.discovery import (
            _bundled_firstparty_base,
            default_search_bases,
            discover_pack_files,
        )

        try:
            bundled = _bundled_firstparty_base().resolve()
        except (OSError, ValueError):
            bundled = None

        for base in default_search_bases():
            try:
                resolved_base = Path(base).resolve()
            except (OSError, ValueError):
                continue
            trusted = bundled is not None and resolved_base == bundled
            try:
                discovered = discover_pack_files([base])
            except Exception:  # noqa: BLE001 - a bad base never halts the rest
                continue
            for disc in discovered:
                for entry in disc.manifest.provides:
                    if entry.type != "recipe" or entry.spec is None:
                        continue
                    manifest = parse_recipe_manifest((disc.pack_dir / entry.spec))
                    if manifest is None:
                        continue
                    _register_recipe_pack(
                        registry,
                        manifest,
                        trusted=trusted,
                        external_pack_ids=external_pack_ids,
                    )
    except Exception:  # noqa: BLE001 - fail-closed: external packs never halt compile
        return PackRegistry.with_first_party_packs()

    # Python ``entry_points`` source (pip-installed recipe publishers). Separate
    # default-OFF flag (AND'd with the kernel-packs gate) because entry_points
    # imports the publisher's module — the standard distribution-tool trust model
    # (like pytest plugins). Self-host opt-in only; the hosted floor must never
    # enable this. Untrusted (external publishers): same compose-only validation
    # as FS user-dir packs (R1/R4/R6/R7).
    if flag_bool(MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV, env=env):
        try:
            for manifest in _entry_point_recipe_manifests(group=RECIPE_ENTRY_POINT_GROUP):
                _register_recipe_pack(
                    registry,
                    manifest,
                    trusted=False,
                    external_pack_ids=external_pack_ids,
                )
        except Exception:  # noqa: BLE001 - fail-closed: entry_points never halt compile
            pass

    # R2 ref-closure: drop external packs with a dangling ref (load-order safe,
    # first-party packs are never dropped). See module docstring.
    return _drop_unresolved_external_recipe_packs(registry, external_pack_ids)
