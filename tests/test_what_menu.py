"""PR-F-UX5 — split-menu correctness for ``magi_agent.customize.what_menu``.

The split MUST classify each ref by prefix ONLY (no rename, no remap):

* ``evidence:*`` prefix → ``evidence_menu``
* ``verifier:*`` prefix OR unprefixed named-judgment refs → ``judgment_menu``

The legacy ``what_menu()`` MUST remain the byte-equal union so existing
consumers (``customRuleMenu`` catalog field, NL compiler) keep working without
rebase.
"""

from __future__ import annotations

import pytest

from magi_agent.customize.what_menu import (
    evidence_menu,
    judgment_menu,
    what_menu,
)

# Base producer-backed refs (emitted unconditionally).
_BASE_EVIDENCE = {"evidence:git-diff", "evidence:test-run"}
_BASE_JUDGMENT = {"verifier:dev-coding:test-evidence"}

# Config-gated entries: (env flag, ref, expected bucket key).
_GATED = (
    ("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "fact_grounding", "judgment"),
    (
        "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
        "verifier:research-source-evidence",
        "judgment",
    ),
    (
        "MAGI_GA_DELIVERABLE_GATE_ENABLED",
        "evidence:artifact-delivery-ref",
        "evidence",
    ),
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # All gated producers OFF + customize master flag OFF so the default menu is
    # base-only (3 entries). Tests opt producers in explicitly.
    for flag, _ref, _bucket in _GATED:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")


# ---------------------------------------------------------------------------
# Default-menu split (no gated producers active)
# ---------------------------------------------------------------------------


def test_evidence_menu_default_returns_only_evidence_prefixed_refs():
    refs = {e["ref"] for e in evidence_menu()}
    assert refs == _BASE_EVIDENCE
    # No verifier:* / bare-token leakage into the evidence bucket.
    for ref in refs:
        assert ref.startswith("evidence:"), ref


def test_judgment_menu_default_returns_only_verifier_prefixed_or_bare_refs():
    refs = {e["ref"] for e in judgment_menu()}
    assert refs == _BASE_JUDGMENT
    # No evidence:* leakage into the judgment bucket.
    for ref in refs:
        assert not ref.startswith("evidence:"), ref


def test_evidence_and_judgment_menus_are_disjoint():
    overlap = {e["ref"] for e in evidence_menu()} & {e["ref"] for e in judgment_menu()}
    assert overlap == set()


def test_legacy_what_menu_is_union_of_evidence_and_judgment():
    # Back-compat invariant: customRuleMenu (= what_menu()) MUST still return
    # every ref from both buckets so existing consumers keep working.
    union_refs = {e["ref"] for e in evidence_menu()} | {
        e["ref"] for e in judgment_menu()
    }
    legacy_refs = {e["ref"] for e in what_menu()}
    assert union_refs == legacy_refs


def test_legacy_what_menu_descriptor_shape_unchanged():
    # The split helpers must return the SAME descriptor shape as what_menu()
    # (JSON-serializable dicts with the documented keys). Otherwise the new
    # catalog fields would force a separate consumer schema.
    required = {"ref", "label", "evidenceType", "tier", "firesAt", "allowedActions"}
    for entry in (*evidence_menu(), *judgment_menu()):
        assert set(entry) >= required
        assert isinstance(entry["allowedActions"], list)


# ---------------------------------------------------------------------------
# Config-gated entries go to the correct bucket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag,ref,bucket", _GATED)
def test_config_gated_ref_appears_in_the_correct_bucket_when_active(
    monkeypatch, flag, ref, bucket
):
    # OFF: ref is invisible in both buckets.
    assert ref not in {e["ref"] for e in evidence_menu()}
    assert ref not in {e["ref"] for e in judgment_menu()}

    # ON: ref appears in exactly its expected bucket.
    monkeypatch.setenv(flag, "1")
    in_evidence = ref in {e["ref"] for e in evidence_menu()}
    in_judgment = ref in {e["ref"] for e in judgment_menu()}
    if bucket == "evidence":
        assert in_evidence and not in_judgment, (ref, in_evidence, in_judgment)
    else:
        assert in_judgment and not in_evidence, (ref, in_evidence, in_judgment)


def test_bare_fact_grounding_ref_classified_as_judgment(monkeypatch):
    # The bare ``fact_grounding`` token is documented as a verdict primitive
    # despite the missing prefix. Re-asserting here so the classification
    # cannot silently drift to ``evidence`` if a future refactor adds an
    # ``evidence:`` rename without updating the runtime ref strings.
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    judgment_refs = {e["ref"] for e in judgment_menu()}
    assert "fact_grounding" in judgment_refs


def test_evidence_artifact_delivery_ref_classified_as_evidence(monkeypatch):
    # The ``evidence:artifact-delivery-ref`` token is a raw producer record
    # (an artifact-delivery ledger entry). Re-asserting because the
    # discovery notes flag the wire-name ambiguity (it represents a
    # promise-was-kept assertion but the byte-key wears the ``evidence:``
    # prefix; the prefix wins).
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    evidence_refs = {e["ref"] for e in evidence_menu()}
    assert "evidence:artifact-delivery-ref" in evidence_refs


def test_env_param_honored_without_touching_os_environ_for_split_helpers():
    env = {"MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "1"}
    judgment_refs = {e["ref"] for e in judgment_menu(env=env)}
    assert "fact_grounding" in judgment_refs
    # os.environ untouched → live judgment menu stays base-only.
    assert "fact_grounding" not in {e["ref"] for e in judgment_menu()}
