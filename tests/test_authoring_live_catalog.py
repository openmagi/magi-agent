"""Task 6.3: the live catalog is manifest-built (D4), not the hardcoded default.

``resolve_live_catalog`` replaces ``CompileRecipePackCatalog.default()`` on the
``None``-catalog live path. It is the UNION of (a) the legacy default reference
floor — preserved so existing recipe-ref validation keeps passing (no regression)
— AND (b) the refs discovered from loaded pack manifests (bundled first-party +
user dirs), proving the catalog is now built from manifests, flat, with no
first-party-only tier.
"""
from __future__ import annotations

from magi_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    resolve_live_catalog,
)


def test_resolve_live_catalog_is_manifest_built_not_hardcoded_default() -> None:
    static = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()  # no explicit catalog -> manifest-built
    # It is a real catalog, not the frozen hardcode object identity:
    assert isinstance(live, CompileRecipePackCatalog)
    assert live is not static
    # The legacy default's tool refs are preserved (no recipe-validation
    # regression): the floor is a subset of the live union.
    assert set(static.tool_refs).issubset(set(live.tool_refs))


def test_resolve_live_catalog_includes_pack_only_refs() -> None:
    """The live catalog is provably manifest-sourced: it carries a ref provided by
    a bundled pack (``Clock``) that is NOT in the static ``.default()`` hardcode."""
    static = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()
    assert "Clock" not in static.tool_refs
    assert "Clock" in live.tool_refs


def test_resolve_live_catalog_preserves_hard_invariant_floor() -> None:
    """The hosted hard-invariant floor the model validator requires
    (``requiredHardInvariantRefs`` subset of ``hardInvariantRefs``) is preserved,
    so the catalog still validates."""
    live = resolve_live_catalog()
    assert set(live.required_hard_invariant_refs).issubset(
        set(live.hard_invariant_refs)
    )
