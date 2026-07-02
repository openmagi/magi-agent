"""Phase 1 — 38-preset scope classification pin.

If the harness adds a new preset (or renames one) without updating
``PRESET_SCOPES``, the catalog will start serving an un-scoped preset and any
scope-aware enforcement layer will fall back to ``("always",)`` for it — a
silent classification drift the test below catches.
"""
from __future__ import annotations

from magi_agent.customize.preset_map import (
    PRESET_SCOPES,
    scope_for_preset,
)
from magi_agent.customize.scope import SCOPES


def test_every_catalog_preset_is_classified() -> None:
    from magi_agent.harness.presets import builtin_preset_catalog

    catalog_keys = {p.key for p in builtin_preset_catalog()}
    classified = set(PRESET_SCOPES)
    missing = catalog_keys - classified
    assert not missing, (
        f"PRESET_SCOPES is missing classifications for {sorted(missing)}. "
        "Add them to magi_agent/customize/preset_map.py:PRESET_SCOPES."
    )


def test_no_phantom_classifications_for_unknown_presets() -> None:
    """Drift in the other direction: PRESET_SCOPES classifies a preset that no
    longer exists in the catalog. Likely a typo or stale rename — flag it."""
    from magi_agent.harness.presets import builtin_preset_catalog

    catalog_keys = {p.key for p in builtin_preset_catalog()}
    phantom = set(PRESET_SCOPES) - catalog_keys
    assert not phantom, (
        f"PRESET_SCOPES classifies non-existent presets {sorted(phantom)}. "
        "Remove or rename them in magi_agent/customize/preset_map.py:PRESET_SCOPES."
    )


def test_every_scope_value_is_in_vocabulary() -> None:
    for preset_id, scopes in PRESET_SCOPES.items():
        for s in scopes:
            assert s in SCOPES, (
                f"preset {preset_id!r} declares scope {s!r} not in customize/scope.SCOPES"
            )


def test_unknown_preset_id_defaults_to_always() -> None:
    assert scope_for_preset("zzz-no-such-preset") == ("always",)


def test_classified_preset_returns_declared_tuple() -> None:
    assert scope_for_preset("coding-verification") == ("coding",)
    assert scope_for_preset("redaction") == ("always",)
    assert scope_for_preset("fact-grounding") == ("research",)


def test_scope_distribution_matches_design() -> None:
    """Spot-check the D.2 distribution so a refactor cannot quietly shift a
    classification (e.g. move ``redaction`` out of always)."""
    by_scope: dict[str, list[str]] = {}
    for preset_id, scopes in PRESET_SCOPES.items():
        for s in scopes:
            by_scope.setdefault(s, []).append(preset_id)
    assert "redaction" in by_scope["always"]
    assert "coding-verification" in by_scope["coding"]
    assert "fact-grounding" in by_scope["research"]
    assert "artifact-delivery" in by_scope["delivery"]
    assert "memory-continuity" in by_scope["memory"]
    assert "task-contract" in by_scope["task"]

# PR-P5.3: the filter_refs_by_scope tests were removed with the function (the
# auto turn-scope axis is retired). PRESET_SCOPES / scope_for_preset remain for
# the catalog's display-only "scope" field and are covered above.
