from __future__ import annotations

from magi_agent.customize.preset_map import (
    PRESET_SEAMS,
    description_for,
    domain_for,
    enforcement_for,
    opt_method_for,
    seam_for,
    supported_modes_for,
    tier_for,
)


def test_domain_for_maps_category_to_when_group():
    assert domain_for("security") == "always-on"
    assert domain_for("coding") == "coding"
    assert domain_for("research") == "research"
    assert domain_for("fact") == "research"
    # answer/output/task/memory collapse into delivery·general
    for c in ("answer", "output", "task", "memory"):
        assert domain_for(c) == "delivery", c
    # unknown → delivery (safe default)
    assert domain_for("totally-unknown") == "delivery"


def test_tier_for_classifies_enforcement_mechanism():
    # deterministic wired seams badge "deterministic"
    for pid in ("coding-verification", "fact-grounding", "source-authority", "artifact-delivery"):
        assert tier_for(pid, is_security=False) == "deterministic", pid
    # the answer-quality seam is an LLM-tier judge → badges "llm", not a false "det"
    assert tier_for("answer-quality", is_security=False) == "llm"
    assert tier_for("dangerous-patterns", is_security=True) == "always-on"
    assert tier_for("claim-citation", is_security=False) is None  # preview → no tier


def test_opt_method_for_reads_seam_wiring():
    assert opt_method_for("coding-verification") == "opt-out"
    assert opt_method_for("fact-grounding") == "opt-in"
    assert opt_method_for("answer-quality") == "opt-in"
    assert opt_method_for("claim-citation") is None


def test_description_for_uses_accurate_text_for_wired_presets():
    # source-authority must reflect OSS anti-fabrication reality, NOT the hosted
    # "memory vs real-time priority" copy (spec §4.3).
    sa = description_for("source-authority")
    assert "anti-fab" in sa.lower() or "inspected source" in sa.lower()
    # every wired/security preset has a non-empty, specific description
    for pid in ("coding-verification", "fact-grounding", "artifact-delivery", "dangerous-patterns"):
        assert description_for(pid)
        assert "parity" not in description_for(pid).lower()


def test_description_for_unknown_has_honest_fallback():
    d = description_for("totally-unknown-preset")
    assert d  # non-empty honest fallback


def test_coding_verification_seam_is_opt_out():
    cv = seam_for("coding-verification")
    assert cv is not None
    assert cv.controls_refs == ("verifier:dev-coding:test-evidence",)
    assert cv.runtime_default_on is True
    assert cv.wiring == "opt_out"


def test_opt_in_seams_are_runtime_default_off():
    for pid in ("fact-grounding", "source-authority", "artifact-delivery"):
        seam = seam_for(pid)
        assert seam is not None, pid
        assert seam.wiring == "opt_in", pid
        assert seam.runtime_default_on is False, pid


def test_seam_keys_are_canonical_hyphen_ids():
    for key in PRESET_SEAMS:
        assert "_" not in key, f"preset id {key!r} must use hyphens, not underscores"


def test_enforcement_for_classifies_honestly():
    # wired presets (opt-out + opt-in) → enforcing
    assert enforcement_for("coding-verification", category="coding", is_security=False) == "enforcing"
    assert enforcement_for("fact-grounding", category="fact", is_security=False) == "enforcing"
    assert enforcement_for("source-authority", category="research", is_security=False) == "enforcing"
    assert enforcement_for("artifact-delivery", category="output", is_security=False) == "enforcing"
    # security presets are enforced elsewhere (PermissionGate), not via this toggle
    assert enforcement_for("dangerous-patterns", category="security", is_security=True) == "always-on"
    # the answer-quality LLM seam is now wired → enforcing
    assert enforcement_for("answer-quality", category="answer", is_security=False) == "enforcing"
    # the completion-evidence LLM seam (C-MERGE-1) is now wired → enforcing
    assert enforcement_for("completion-evidence", category="answer", is_security=False) == "enforcing"
    # the self-claim LLM seam (C-MERGE-2) is now wired → enforcing
    assert enforcement_for("self-claim", category="fact", is_security=False) == "enforcing"
    # metadata-only / no live producer → honest preview
    assert enforcement_for("claim-citation", category="fact", is_security=False) == "preview"


def test_supported_modes_default_deterministic():
    # the answer-quality seam declares the llm tier; non-seam presets default det
    assert supported_modes_for("answer-quality") == ("llm",)
    assert supported_modes_for("claim-citation") == ("deterministic",)
    assert supported_modes_for("coding-verification") == ("deterministic",)


def test_seam_for_unknown_returns_none():
    assert seam_for("does-not-exist") is None
    assert seam_for("claim-citation") is None  # metadata-only, not wired
