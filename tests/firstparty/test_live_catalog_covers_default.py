"""Task 6.1 (honest form): the catalog flip loses NOTHING.

The doc's original Task 6.1 proposed a ``core_authoring`` bundled pack with
adapters pointing at ``magi_agent.authoring.tools:build_source_open_tool`` etc.
Those producer functions DO NOT EXIST — the ``.default()`` refs (``BrowserLive``,
``CitationVerify``, ``connector.source.readonly``, ``plugin.source-review.readonly``,
``harness:authoring-static@1``, ``authority:owner-human@1``, ``SourceOpen``,
``FileWrite``) are the authoring-plane **reference catalog** the recipe-pack
compiler validates recipe refs against, not live runtime impls. Fabricating
adapters for them would invent an API.

The "loses nothing" intent is honored instead by ``resolve_live_catalog``
PRESERVING the legacy ``.default()`` reference floor (unioning it with the
pack-discovered refs). This test is the guard: every legacy default ref, in
every field, still appears in the live manifest-built catalog after the 6.3 flip.
"""
from __future__ import annotations

from magi_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    resolve_live_catalog,
)

_REF_FIELDS = (
    "connector_refs",
    "tool_refs",
    "plugin_refs",
    "validator_refs",
    "harness_refs",
    "required_evidence_refs",
    "evidence_producer_refs",
    "approval_authority_refs",
    "hard_invariant_refs",
    "required_hard_invariant_refs",
)


def test_live_catalog_preserves_every_legacy_default_ref() -> None:
    default = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()
    for field in _REF_FIELDS:
        missing = set(getattr(default, field)) - set(getattr(live, field))
        assert missing == set(), (
            f"the catalog flip dropped {field} refs with no live home: {sorted(missing)}"
        )


def test_live_catalog_adds_pack_refs_on_top_of_the_floor() -> None:
    """Beyond preserving the floor, the live catalog carries bundled-pack refs not
    in the static default — proving it is manifest-built, not the hardcode."""
    default = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()
    added_tools = set(live.tool_refs) - set(default.tool_refs)
    added_validators = set(live.validator_refs) - set(default.validator_refs)
    assert "Clock" in added_tools
    assert "verifier:sourceOpened@1" in added_validators
