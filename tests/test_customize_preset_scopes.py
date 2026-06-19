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


# ---------------------------------------------------------------------------
# filter_refs_by_scope — keep unscoped, drop scope-mismatched preset refs
# ---------------------------------------------------------------------------


def test_filter_keeps_unowned_refs() -> None:
    """A ref no preset claims is left alone — scope filter is opt-in per preset."""
    from magi_agent.customize.preset_map import filter_refs_by_scope

    refs = ("verifier:some-external-pack", "evidence:custom")
    assert filter_refs_by_scope(refs, current_scope="coding") == refs


def test_filter_keeps_coding_ref_on_coding_turn() -> None:
    from magi_agent.customize.preset_map import filter_refs_by_scope

    refs = ("verifier:dev-coding:test-evidence",)
    assert (
        filter_refs_by_scope(refs, current_scope="coding") == refs
    )


def test_filter_drops_coding_ref_on_non_coding_turn() -> None:
    """The lab bug fix: a coding preset's ref must not be required on a
    non-coding turn (else the gate blocks ``Hi`` on a missing coding evidence)."""
    from magi_agent.customize.preset_map import filter_refs_by_scope

    refs = ("verifier:dev-coding:test-evidence", "verifier:other-external")
    kept = filter_refs_by_scope(refs, current_scope="research")
    assert "verifier:dev-coding:test-evidence" not in kept
    assert "verifier:other-external" in kept  # external = kept


def test_filter_always_scope_kept_on_every_turn() -> None:
    """An ``always`` preset's ref applies to every turn."""
    from magi_agent.customize.preset_map import filter_refs_by_scope

    # redaction is always-scope and owns no controls_refs by default (opt-in
    # seam) so use an evidence-pack preset variant — but the test stays generic:
    # any ref classified under an always preset survives every scope.
    # Skip if no always preset has a controls_ref to assert against.
    from magi_agent.customize.preset_map import PRESET_SEAMS

    always_refs: list[str] = []
    for preset_id, seam in PRESET_SEAMS.items():
        if scope_for_preset(preset_id) == ("always",):
            always_refs.extend(seam.controls_refs)
    if not always_refs:
        return  # no always-scope seam currently contributes refs
    refs = tuple(always_refs)
    assert filter_refs_by_scope(refs, current_scope="research") == refs
