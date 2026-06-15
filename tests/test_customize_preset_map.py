from __future__ import annotations

from magi_agent.customize.preset_map import (
    PRESET_SEAMS,
    enforcement_for,
    seam_for,
    supported_modes_for,
)


def test_wired_presets_have_enforcing_seams():
    cv = seam_for("coding-verification")
    assert cv is not None
    assert "verifier:dev-coding:test-evidence" in cv.validator_refs
    assert "openmagi.dev-coding" in cv.require_packs
    fg = seam_for("fact-grounding")
    assert fg is not None
    assert "fact_grounding" in fg.evidence_labels


def test_seam_keys_are_canonical_hyphen_ids():
    for key in PRESET_SEAMS:
        assert "_" not in key, f"preset id {key!r} must use hyphens, not underscores"


def test_enforcement_for_classifies_honestly():
    # genuinely wired in Phase 2
    assert enforcement_for("coding-verification", category="coding", is_security=False) == "enforcing"
    assert enforcement_for("fact-grounding", category="fact", is_security=False) == "enforcing"
    # security presets are enforced elsewhere (PermissionGate), not via this toggle
    assert enforcement_for("dangerous-patterns", category="security", is_security=True) == "always-on"
    # not yet wired → honest preview
    assert enforcement_for("answer-quality", category="answer", is_security=False) == "preview"


def test_supported_modes_default_deterministic():
    # unknown preset → deterministic only
    assert supported_modes_for("answer-quality") == ("deterministic",)
    # fact-grounding deterministic only this round (no llm path yet)
    assert supported_modes_for("fact-grounding") == ("deterministic",)


def test_seam_for_unknown_returns_none():
    assert seam_for("does-not-exist") is None
