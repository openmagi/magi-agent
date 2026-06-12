"""Group C.1 — bundled first-party ``recipe_authoring_static`` pack provides a
recipe via a declarative ``spec`` (no impl). The loader resolves the spec relpath
and the projector validates it as a ``RecipePackManifest`` into the live recipe
registry (D3 declarative path)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_REF = "recipe:authoring-static@1"


def test_recipe_authoring_static_pack_registers_recipe() -> None:
    registries, report = load_into_registries([_FIRST_PARTY_ROOT / "recipe_authoring_static"])
    assert _REF in report.registered
    manifest = registries.recipes.resolve(_REF)
    assert manifest is not None
    assert manifest.pack_id == "openmagi.authoring-static"
    assert "verifier:sourceOpened@1" in manifest.validator_refs
