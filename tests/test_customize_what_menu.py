from __future__ import annotations

from magi_agent.customize.what_menu import is_known_ref, known_refs, what_menu


def test_menu_entries_have_required_descriptor_keys():
    menu = what_menu()
    assert menu
    for entry in menu:
        assert set(entry) >= {"ref", "label", "evidenceType", "tier", "firesAt", "allowedActions"}
        assert entry["tier"] == "deterministic"
        assert entry["firesAt"] == "pre_final"
        assert entry["allowedActions"]


def test_menu_refs_are_producer_backed_only():
    # Truth source: refs that evidence/local_tool_collector._inferred_refs can emit
    # on a live turn. The menu must NOT advertise refs with no producer (would
    # block unconditionally). Keep in sync with _inferred_refs.
    producible = {"evidence:git-diff", "evidence:test-run", "verifier:dev-coding:test-evidence"}
    assert known_refs() <= producible


def test_is_known_ref():
    assert is_known_ref("evidence:test-run")
    assert not is_known_ref("verifier:made-up")
    assert not is_known_ref("")
