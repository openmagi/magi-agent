from __future__ import annotations

from magi_agent.customize.custom_rules import CRITERION_MAX, validate_custom_rule


def _det(**over):
    rule = {
        "scope": "coding",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test-run"}},
        "firesAt": "pre_final",
        "action": "block",
    }
    rule.update(over)
    return rule


def _tool(**over):
    rule = {
        "scope": "research",
        "enabled": True,
        "what": {"kind": "tool_perm", "payload": {"match": {"domain": "sec.gov"}, "decision": "deny"}},
        "firesAt": "before_tool_use",
        "action": "block",
    }
    rule.update(over)
    return rule


def _llm(**over):
    rule = {
        "scope": "research",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": "all claims cited"}},
        "firesAt": "pre_final",
        "action": "block",
    }
    rule.update(over)
    return rule


# --- valid shapes ---
def test_valid_deterministic_rule():
    assert validate_custom_rule(_det()) == []


def test_valid_tool_perm_rule():
    assert validate_custom_rule(_tool()) == []


def test_valid_llm_pre_final():
    assert validate_custom_rule(_llm()) == []


def test_valid_llm_after_tool_needs_toolmatch_and_override():
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={"kind": "llm_criterion", "payload": {"criterion": "block non-10K", "toolMatch": ["web_search"]}},
        projection=["result", "args"],
    )
    assert validate_custom_rule(rule) == []


# --- adversarial (§11) ---
def test_unknown_scope_kind_action_rejected():
    assert validate_custom_rule(_det(scope="bogus"))
    assert validate_custom_rule(_det(action="nope"))
    assert validate_custom_rule(_det(what={"kind": "bogus", "payload": {}}))


def test_deterministic_ref_must_be_in_what_menu():
    assert validate_custom_rule(
        _det(what={"kind": "deterministic_ref", "payload": {"ref": "verifier:made-up"}})
    )


def test_deterministic_illegal_action():
    # det allows block/retry/audit only — ask_approval is illegal
    assert validate_custom_rule(_det(action="ask_approval"))


def test_deterministic_illegal_firesat():
    # det is fixed to pre_final
    assert validate_custom_rule(_det(firesAt="before_tool_use"))


def test_tool_perm_illegal_firesat():
    assert validate_custom_rule(_tool(firesAt="pre_final"))


def test_llm_after_tool_without_toolmatch_rejected():
    rule = _llm(firesAt="after_tool_use", action="override",
                what={"kind": "llm_criterion", "payload": {"criterion": "x"}})
    errs = validate_custom_rule(rule)
    assert any("toolMatch" in e for e in errs)


def test_llm_after_tool_must_override():
    rule = _llm(firesAt="after_tool_use", action="block",
                what={"kind": "llm_criterion", "payload": {"criterion": "x", "toolMatch": ["web_search"]}})
    assert validate_custom_rule(rule)


def test_projection_rejects_conversation():
    rule = _llm(firesAt="after_tool_use", action="override",
                what={"kind": "llm_criterion", "payload": {"criterion": "x", "toolMatch": ["web_search"]}},
                projection=["result", "conversation"])
    errs = validate_custom_rule(rule)
    assert any("projection" in e.lower() for e in errs)


def test_criterion_length_cap():
    rule = _llm(what={"kind": "llm_criterion", "payload": {"criterion": "x" * (CRITERION_MAX + 1)}})
    errs = validate_custom_rule(rule)
    assert any("criterion" in e.lower() for e in errs)
