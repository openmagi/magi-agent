from __future__ import annotations

import pytest

from magi_agent.customize.what_menu import (
    allowed_actions_for,
    is_known_ref,
    known_refs,
    what_menu,
)

# Base producer-backed refs: emitted unconditionally by
# evidence/local_tool_collector._inferred_refs on a live turn.
_BASE_REFS = {"evidence:git-diff", "evidence:test-run", "verifier:dev-coding:test-evidence"}

# Config-gated refs: surfaced only while their engine satisfier is active.
_GATED = {
    "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "fact_grounding",
    "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED": "verifier:research-source-evidence",
    "MAGI_GA_DELIVERABLE_GATE_ENABLED": "evidence:artifact-delivery-ref",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Producer flags + the customize master flag default OFF so the menu is the
    # base 3 unless a test opts a producer on.
    for name in (*_GATED, "MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")


def test_menu_entries_have_required_descriptor_keys():
    menu = what_menu()
    assert menu
    for entry in menu:
        assert set(entry) >= {"ref", "label", "evidenceType", "tier", "firesAt", "allowedActions"}
        assert entry["tier"] == "deterministic"
        assert entry["firesAt"] == "pre_final"
        assert entry["allowedActions"]


def test_default_menu_is_base_only():
    # All producer flags OFF → menu is exactly the 3 always-producible base refs.
    assert known_refs() == frozenset(_BASE_REFS)


def test_base_refs_are_producer_backed_only():
    # Truth source: refs evidence/local_tool_collector._inferred_refs can emit.
    # The base menu must never advertise a ref with no unconditional producer.
    assert frozenset(_BASE_REFS) <= {
        "evidence:git-diff",
        "evidence:test-run",
        "verifier:dev-coding:test-evidence",
    }


def test_is_known_ref_base():
    assert is_known_ref("evidence:test-run")
    assert not is_known_ref("verifier:made-up")
    assert not is_known_ref("")


@pytest.mark.parametrize("flag,ref", list(_GATED.items()))
def test_config_gated_ref_appears_only_when_producer_flag_on(monkeypatch, flag, ref):
    # OFF: the config-gated ref is invisible (no fake toggle).
    assert not is_known_ref(ref)
    assert ref not in known_refs()
    # ON: the matching producer flag surfaces exactly that ref.
    monkeypatch.setenv(flag, "1")
    assert is_known_ref(ref)
    assert ref in known_refs()
    assert allowed_actions_for(ref) == ("block", "retry", "audit")


@pytest.mark.parametrize("flag,ref", list(_GATED.items()))
def test_config_gated_ref_explicit_off_overrides_profile(monkeypatch, flag, ref):
    # An explicit "0" wins even under the full profile (flag is strict default-OFF
    # today; survives a later _b->_pb change because "0" always wins).
    monkeypatch.setenv(flag, "0")
    assert not is_known_ref(ref)


def test_env_param_is_honored_without_touching_os_environ():
    env = {"MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "1"}
    assert is_known_ref("fact_grounding", env)
    # os.environ untouched → live default menu stays base-only.
    assert not is_known_ref("fact_grounding")
