"""Task 6.4: microkernel-core guard (D6).

The core = ``{ loader, registries, typed-context dispatcher, ADK loop seam }``.
Everything else is a removable pack. This guard fails if a future change
re-privileges a primitive by hardcoding a concrete first-party control class into
a core module — registration must flow through the loader's lazy ``module:symbol``
import, never a hardcoded class ref.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "magi_agent"

# The microkernel (D6): loader + registries + typed-context dispatcher + catalog.
_CORE_MODULES = [
    _ROOT / "packs" / "loader.py",
    _ROOT / "packs" / "registries.py",
    _ROOT / "packs" / "context.py",
    _ROOT / "packs" / "catalog_build.py",
]

# Concrete first-party primitive symbols that MUST NOT be hardcoded into the core.
_FORBIDDEN_FIRSTPARTY_SYMBOLS = {
    "GaConstraintReinjectionControl",
    "SelfReviewAfterTurnControl",
    "MaxStepsBrakeControl",
    "_EditRetryLoopControl",
    "_ResilienceLoopControl",
    "_CompactionLoopControl",
}


def _names_referenced(path: Path) -> set[str]:
    """Symbol references in code (ast.Name ids + ast.Attribute attrs).

    Docstrings/comments are ``ast.Constant`` literals, not Name/Attribute nodes,
    so a control mentioned only in prose is correctly ignored — we guard against
    real code references (imports, instantiation, attribute access), not docs.
    """
    tree = ast.parse(path.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return names | attrs


def test_core_modules_do_not_hardcode_first_party_primitives() -> None:
    for module in _CORE_MODULES:
        referenced = _names_referenced(module)
        leaked = referenced & _FORBIDDEN_FIRSTPARTY_SYMBOLS
        assert leaked == set(), (
            f"{module.name} hardcodes first-party primitives: {sorted(leaked)}"
        )


def test_live_catalog_path_does_not_call_hardcoded_default_catalog() -> None:
    # Re-homed from the deleted authoring plane: the live catalog entry point is
    # kernel-owned (packs/catalog_build.py). It must be manifest-built — folding
    # build_catalog(result.primitives) over discovered packs — and never the
    # legacy `catalog or CompileRecipePackCatalog.default()` hardcode shape
    # (.default() survives only as the preserved fail-open floor inside
    # resolve_live_catalog).
    src = (_ROOT / "packs" / "catalog_build.py").read_text()
    assert "def resolve_live_catalog(" in src
    assert "build_catalog(result.primitives)" in src
    assert "catalog or CompileRecipePackCatalog.default()" not in src


def test_build_default_plugin_loads_controls_via_loader_not_hand_assembly() -> None:
    """The keystone: build_default_plugin's body loads controls via the pack
    loader (``build_control_plane_from_packs``) and does NOT hand-assemble them."""
    src = (_ROOT / "adk_bridge" / "control_plane.py").read_text()
    body = src.split("def build_default_plugin", 1)[1].split("\ndef ", 1)[0]
    assert "build_control_plane_from_packs(" in body
    assert "plane.register(" not in body
