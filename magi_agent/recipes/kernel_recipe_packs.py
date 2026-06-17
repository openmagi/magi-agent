"""Fold kernel-loaded ``recipe`` provides into the recipe-compile PackRegistry.

The neutral pack kernel (``magi_agent/packs/``) already discovers ``pack.toml``
manifests, and for a ``[[provides]] type="recipe"`` entry it parses the spec into
a genuine :class:`magi_agent.recipes.compiler.RecipePackManifest` and stores it in
``registries.recipes`` (``packs/registries.py``). Nothing consumed that registry,
so kernel-authored recipe packs never reached the compiler — the gap this module
closes. There is no structural mismatch to bridge: the kernel stores the exact
``RecipePackManifest`` type the compiler's ``PackRegistry`` holds.

Trust boundary (OSS local; the hosted floor is layered separately and is out of
scope here):

* **default-OFF** — gated by ``MAGI_KERNEL_RECIPE_PACKS_ENABLED``. With the flag
  unset, ``build_runtime_pack_registry`` returns exactly
  ``PackRegistry.with_first_party_packs()`` — byte-identical to today.
* **first-party-wins** — first-party packs register first; a kernel pack whose
  ``pack_id`` collides is dropped (``register`` raises), so a kernel pack can
  never shadow a first-party one.
* **fail-closed-to-first-party** — any discovery/load error drops the kernel
  contribution and returns the first-party-only registry; recipe compilation is
  never halted by external packs.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from magi_agent.config.flags import flag_bool
from magi_agent.recipes.compiler import PackRegistry

logger = logging.getLogger(__name__)

MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV = "MAGI_KERNEL_RECIPE_PACKS_ENABLED"

__all__ = [
    "MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV",
    "build_runtime_pack_registry",
]


def build_runtime_pack_registry(env: Mapping[str, str] | None = None) -> PackRegistry:
    """First-party recipe packs, plus kernel-loaded ``recipe`` provides when ON.

    Returns ``PackRegistry.with_first_party_packs()`` unchanged when the flag is
    OFF (byte-identical baseline). When ON, kernel-discovered recipe packs are
    registered AFTER first-party (first-party-wins on a ``pack_id`` collision).
    """

    registry = PackRegistry.with_first_party_packs()
    if not flag_bool(MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV, env=env):
        return registry

    try:
        from magi_agent.packs.discovery import default_search_bases
        from magi_agent.packs.registries import load_into_registries

        registries, _ = load_into_registries(default_search_bases())
        for ref in registries.recipes.list_refs():
            manifest = registries.recipes.resolve(ref)
            if manifest is None:
                continue
            try:
                registry.register(manifest)
            except ValueError:
                # Colliding pack_id: first-party (registered first) wins; drop the
                # kernel pack so it cannot shadow first-party.
                logger.warning(
                    "kernel recipe pack %r dropped (pack_id collision with first-party)",
                    getattr(manifest, "pack_id", ref),
                )
    except Exception:  # noqa: BLE001 - fail-closed: external packs never halt compile
        return PackRegistry.with_first_party_packs()

    return registry
