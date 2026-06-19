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
  dropped. (Ref-closure R2 is intentionally deferred to a later PR.)
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
from collections.abc import Mapping
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from magi_agent.config.flags import flag_bool
from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest

logger = logging.getLogger(__name__)

MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV = "MAGI_KERNEL_RECIPE_PACKS_ENABLED"
MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV = "MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED"

_EXTERNAL_RECIPE_NAMESPACE_PREFIX = "ext."
RECIPE_ENTRY_POINT_GROUP = "magi.recipes"

__all__ = [
    "MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV",
    "MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV",
    "RECIPE_ENTRY_POINT_GROUP",
    "build_runtime_pack_registry",
    "parse_recipe_manifest",
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
    first-party packs bypass this (see module docstring). R2 ref-closure is
    deferred to a later PR.
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
    registry: PackRegistry, manifest: RecipePackManifest, *, trusted: bool
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


def build_runtime_pack_registry(env: Mapping[str, str] | None = None) -> PackRegistry:
    """First-party recipe packs, plus kernel-loaded ``recipe`` provides when ON.

    Returns ``PackRegistry.with_first_party_packs()`` unchanged when the flag is
    OFF (byte-identical baseline). When ON, kernel-discovered recipe packs are
    registered AFTER first-party (first-party-wins on a ``pack_id`` collision),
    with the compose-only trust boundary applied to untrusted (user-dir) packs.
    """

    registry = PackRegistry.with_first_party_packs()
    if not flag_bool(MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV, env=env):
        return registry

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
                    _register_recipe_pack(registry, manifest, trusted=trusted)
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
                _register_recipe_pack(registry, manifest, trusted=False)
        except Exception:  # noqa: BLE001 - fail-closed: entry_points never halt compile
            pass

    return registry
