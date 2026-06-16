from __future__ import annotations

from magi_agent.customize.preset_map import (
    PRESET_SEAMS,
    enforcement_for,
    seam_for,
    supported_modes_for,
)


def test_coding_verification_seam_is_opt_out():
    cv = seam_for("coding-verification")
    assert cv is not None
    assert cv.controls_refs == ("verifier:dev-coding:test-evidence",)
    assert cv.runtime_default_on is True


def test_seam_keys_are_canonical_hyphen_ids():
    for key in PRESET_SEAMS:
        assert "_" not in key, f"preset id {key!r} must use hyphens, not underscores"


def test_enforcement_for_classifies_honestly():
    assert enforcement_for("coding-verification", category="coding", is_security=False) == "enforcing"
    # security presets are enforced elsewhere (PermissionGate), not via this toggle
    assert enforcement_for("dangerous-patterns", category="security", is_security=True) == "always-on"
    # not yet wired (incl. fact-grounding, deferred to Phase 3) → honest preview
    assert enforcement_for("answer-quality", category="answer", is_security=False) == "preview"
    assert enforcement_for("fact-grounding", category="fact", is_security=False) == "preview"


def test_supported_modes_default_deterministic():
    assert supported_modes_for("answer-quality") == ("deterministic",)
    assert supported_modes_for("coding-verification") == ("deterministic",)


def test_seam_for_unknown_returns_none():
    assert seam_for("does-not-exist") is None
    assert seam_for("fact-grounding") is None  # deferred to Phase 3
