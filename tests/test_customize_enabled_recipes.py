"""Phase 3 — ``enabled_recipes`` allowlist enforcement.

When the user supplies an explicit allowlist via ``verification.recipes``, the
recipe ids NOT in the allowlist have their mapped pack's evidence_refs removed
from the assembled requirements. Empty allowlist ⇒ no-op (byte-identical to
``main`` so the 99% case is unchanged).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.catalog import (
    RECIPE_ID_TO_PACK_IDS,
    pack_ids_for_recipe,
)
from magi_agent.customize.store import set_verification_override
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy
from magi_agent.cli.real_runner import (
    _apply_customize_evidence_overrides,
    _apply_customize_verification,
    _disabled_recipe_pack_refs,
)


# ---------------------------------------------------------------------------
# Mapping table — UI label ↔ real pack id
# ---------------------------------------------------------------------------


def test_mapping_table_does_not_classify_security_critical_packs() -> None:
    """Drift guard: security-critical packs (context-safety / evidence /
    source-grounded) must NEVER appear in the mapping — a user must not be able
    to disable hard-safety obligations through this seam."""
    mapped: set[str] = set()
    for ids in RECIPE_ID_TO_PACK_IDS.values():
        mapped.update(ids)
    forbidden = {
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.source-grounded",
    }
    overlap = mapped & forbidden
    assert not overlap, (
        f"RECIPE_ID_TO_PACK_IDS classifies security-critical packs {sorted(overlap)}; "
        "those must stay enforced regardless of user recipe overrides."
    )


def test_pack_ids_for_recipe_known_returns_curated_tuple() -> None:
    assert pack_ids_for_recipe("coding_evidence_gate") == ("openmagi.dev-coding",)


def test_pack_ids_for_recipe_unknown_returns_empty_tuple() -> None:
    assert pack_ids_for_recipe("zzz-no-such-recipe") == ()


# ---------------------------------------------------------------------------
# _disabled_recipe_pack_refs — allowlist semantics
# ---------------------------------------------------------------------------


def test_empty_allowlist_returns_empty_disabled_sets() -> None:
    """Empty ``enabled_recipes`` ⇒ no opt-out ⇒ historic byte-identical."""
    policy = CustomizeVerificationPolicy(enabled_recipes=frozenset())
    validators, evidence = _disabled_recipe_pack_refs(policy)
    assert validators == frozenset()
    assert evidence == frozenset()


def test_allowlist_includes_all_mapped_ids_is_no_op() -> None:
    """Every mapped UI recipe id present in the allowlist ⇒ no packs are
    treated as disabled ⇒ no refs to remove."""
    policy = CustomizeVerificationPolicy(
        enabled_recipes=frozenset(RECIPE_ID_TO_PACK_IDS),
    )
    validators, evidence = _disabled_recipe_pack_refs(policy)
    assert validators == frozenset()
    assert evidence == frozenset()


def test_allowlist_omitting_coding_disables_dev_coding_refs() -> None:
    """User keeps research enabled; coding recipe is dropped ⇒ dev-coding's
    evidence refs are returned for removal."""
    policy = CustomizeVerificationPolicy(
        enabled_recipes=frozenset({"research", "research_scout_dummy"}),
    )
    _validators, evidence = _disabled_recipe_pack_refs(policy)
    # dev-coding contributes evidence:git-diff + evidence:test-run.
    assert "evidence:git-diff" in evidence
    assert "evidence:test-run" in evidence


def test_allowlist_omitting_research_disables_research_refs() -> None:
    policy = CustomizeVerificationPolicy(
        enabled_recipes=frozenset({"coding_evidence_gate"}),
    )
    _validators, evidence = _disabled_recipe_pack_refs(policy)
    # research pack contributes evidence:inspected-source.
    assert "evidence:inspected-source" in evidence


# ---------------------------------------------------------------------------
# end-to-end: _apply_customize_evidence_overrides drops the disabled refs
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_on(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_evidence_overrides_drops_disabled_recipe_evidence_ref(
    gate_on: Path,
) -> None:
    """User opts to keep ``research`` only ⇒ dev-coding's evidence refs are
    pruned from the assembled required_evidence (e2e via the real apply hook)."""
    set_verification_override("recipes", "research", True, path=gate_on)
    out = _apply_customize_evidence_overrides(
        ["evidence:git-diff", "evidence:test-run", "evidence:inspected-source", "seed:keep"]
    )
    assert "evidence:git-diff" not in out
    assert "evidence:test-run" not in out
    # research is in the allowlist, so its evidence stays.
    assert "evidence:inspected-source" in out
    # Unmapped/unrelated refs are untouched.
    assert "seed:keep" in out


def test_evidence_overrides_no_allowlist_is_byte_identical(gate_on: Path) -> None:
    """No ``recipes`` field set ⇒ empty allowlist ⇒ no refs dropped."""
    refs = ["evidence:git-diff", "evidence:inspected-source", "evidence:other"]
    out = _apply_customize_evidence_overrides(list(refs))
    assert out == refs


def test_validators_pass_drops_disabled_recipe_validator_refs(
    gate_on: Path,
) -> None:
    """Validator-side mirror: a recipe that contributes a validator ref also has
    that ref pruned when its UI label is not in the allowlist."""
    # Manually set the allowlist so "coding_evidence_gate" is omitted (dev-coding
    # would be opt-out).
    set_verification_override("recipes", "research", True, path=gate_on)
    # Drive the assembly-level pass with a synthetic required_validators list
    # containing the actual dev-coding pack validator ref ("validator:dev-coding:
    # tdd-verification" — confirmed via PackRegistry; tracking this exact name
    # is what makes the test honest rather than mocked).
    out = _apply_customize_verification(
        [
            "validator:dev-coding:tdd-verification",
            "verifier:research-source-evidence",
            "seed:keep",
        ]
    )
    # dev-coding validator ref dropped (its pack is not in the allowlist).
    assert "validator:dev-coding:tdd-verification" not in out
    assert "seed:keep" in out
