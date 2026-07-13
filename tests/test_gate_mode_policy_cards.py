"""Mode-gated first-party policy cards (answer_verifier / research_governance /
edit_match): registry, effective resolver, env projection, and catalog descriptor."""
from __future__ import annotations


def test_registry_has_three_mode_gates() -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        GATE_MODE_POLICIES,
        gate_mode_policy_by_id,
    )

    ids = {g.id for g in GATE_MODE_POLICIES}
    assert ids == {"answer_verifier", "research_governance", "edit_match"}
    # strongest-first ordering per gate
    assert gate_mode_policy_by_id("answer_verifier").values == ("enforce", "audit", "off")
    assert gate_mode_policy_by_id("edit_match").values == (
        "block_final_answer",
        "audit",
        "off",
    )
    assert gate_mode_policy_by_id("nope") is None


def test_effective_reads_through_resolver() -> None:
    from magi_agent.customize.builtin_policy_overrides import gate_mode_effective

    # explicit env wins
    assert (
        gate_mode_effective("answer_verifier", {"MAGI_ANSWER_VERIFIER_MODE": "enforce"})
        == "enforce"
    )
    assert (
        gate_mode_effective(
            "edit_match", {"MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT": "audit"}
        )
        == "audit"
    )
    # unset answer_verifier -> off (resolver default)
    assert gate_mode_effective("answer_verifier", {}) == "off"
    # unknown id -> None
    assert gate_mode_effective("nope", {}) is None


def test_apply_projection_overwrites_valid_ignores_invalid() -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        apply_gate_mode_overrides_to_env,
    )

    env: dict[str, str] = {}
    apply_gate_mode_overrides_to_env(
        env,
        {
            "gate_modes": {
                "answer_verifier": "enforce",
                "edit_match": "block_final_answer",
                "research_governance": "bogus",  # invalid -> ignored
            }
        },
    )
    assert env["MAGI_ANSWER_VERIFIER_MODE"] == "enforce"
    assert env["MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT"] == "block_final_answer"
    assert "MAGI_RESEARCH_GOVERNANCE_MODE" not in env  # invalid dropped

    # absent section -> byte-identical (no-op)
    env2: dict[str, str] = {}
    apply_gate_mode_overrides_to_env(env2, {})
    assert env2 == {}


def test_store_roundtrip_gate_modes(tmp_path) -> None:
    from magi_agent.customize.store import load_overrides, set_gate_mode_override

    p = tmp_path / "customize.json"
    set_gate_mode_override("edit_match", "audit", p)
    loaded = load_overrides(p)
    assert loaded["gate_modes"]["edit_match"] == "audit"
    # second gate coexists
    set_gate_mode_override("answer_verifier", "enforce", p)
    loaded2 = load_overrides(p)
    assert loaded2["gate_modes"]["edit_match"] == "audit"
    assert loaded2["gate_modes"]["answer_verifier"] == "enforce"


def test_catalog_attaches_gate_mode_descriptor() -> None:
    from magi_agent.customize.catalog import _policy_entries

    by_id = {e["id"]: e for e in _policy_entries()}
    for pid in ("answer_verifier", "research_governance", "edit_match"):
        assert "gateMode" in by_id[pid], f"{pid} missing gateMode descriptor"
        gm = by_id[pid]["gateMode"]
        assert "value" in gm and "options" in gm
        assert gm["value"] in gm["options"]
