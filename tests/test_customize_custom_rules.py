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


def test_valid_tool_perm_domain_allowlist():
    rule = _tool(
        what={
            "kind": "tool_perm",
            "payload": {"match": {"domainAllowlist": ["sec.gov"]}, "decision": "deny"},
        }
    )
    assert validate_custom_rule(rule) == []


def test_tool_perm_empty_match_rejected():
    rule = _tool(what={"kind": "tool_perm", "payload": {"match": {}, "decision": "deny"}})
    assert validate_custom_rule(rule)


def test_tool_perm_bad_allowlist_rejected():
    rule = _tool(
        what={"kind": "tool_perm", "payload": {"match": {"domainAllowlist": []}, "decision": "deny"}}
    )
    assert validate_custom_rule(rule)


def test_valid_tool_perm_path():
    rule = _tool(
        what={
            "kind": "tool_perm",
            "payload": {"match": {"path": "/Users/me/secret"}, "decision": "deny"},
        },
    )
    assert validate_custom_rule(rule) == []


def test_valid_tool_perm_path_allowlist():
    rule = _tool(
        what={
            "kind": "tool_perm",
            "payload": {
                "match": {"pathAllowlist": ["/Users/me/proj"]},
                "decision": "deny",
            },
        },
    )
    assert validate_custom_rule(rule) == []


def test_tool_perm_bad_path_rejected():
    rule = _tool(
        what={
            "kind": "tool_perm",
            "payload": {"match": {"path": ""}, "decision": "deny"},
        },
    )
    assert validate_custom_rule(rule)


def test_tool_perm_bad_path_allowlist_rejected():
    # Empty list
    rule = _tool(
        what={
            "kind": "tool_perm",
            "payload": {"match": {"pathAllowlist": []}, "decision": "deny"},
        },
    )
    assert validate_custom_rule(rule)
    # Non-string entry
    rule2 = _tool(
        what={
            "kind": "tool_perm",
            "payload": {
                "match": {"pathAllowlist": ["/Users/me/proj", 123]},
                "decision": "deny",
            },
        },
    )
    assert validate_custom_rule(rule2)


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


# --- P4: after-tool contentMatch pre-filter (deterministic) ---
def test_valid_after_tool_content_match_only():
    # Pure-deterministic ingestion gate: contentMatch, no criterion.
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={
            "kind": "llm_criterion",
            "payload": {
                "toolMatch": ["web_search"],
                "contentMatch": {"pattern": "ssn:", "isRegex": False},
            },
        },
    )
    assert validate_custom_rule(rule) == []


def test_valid_after_tool_content_match_plus_criterion():
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={
            "kind": "llm_criterion",
            "payload": {
                "toolMatch": ["web_search"],
                "criterion": "block non-10K filings",
                "contentMatch": {"pattern": r"\d{4}", "isRegex": True},
            },
        },
    )
    assert validate_custom_rule(rule) == []


def test_after_tool_requires_criterion_or_content():
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={"kind": "llm_criterion", "payload": {"toolMatch": ["web_search"]}},
    )
    errs = validate_custom_rule(rule)
    assert any("criterion or a contentMatch" in e for e in errs)


def test_content_match_missing_pattern_rejected():
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={
            "kind": "llm_criterion",
            "payload": {"toolMatch": ["web_search"], "contentMatch": {"isRegex": True}},
        },
    )
    errs = validate_custom_rule(rule)
    assert any("contentMatch.pattern" in e for e in errs)


def test_content_match_bad_regex_rejected():
    rule = _llm(
        firesAt="after_tool_use",
        action="override",
        what={
            "kind": "llm_criterion",
            "payload": {
                "toolMatch": ["web_search"],
                "contentMatch": {"pattern": "([unclosed", "isRegex": True},
            },
        },
    )
    errs = validate_custom_rule(rule)
    assert any("valid regex" in e for e in errs)


def test_content_match_forbidden_at_pre_final():
    rule = _llm(
        what={
            "kind": "llm_criterion",
            "payload": {"criterion": "c", "contentMatch": {"pattern": "x"}},
        }
    )
    errs = validate_custom_rule(rule)
    assert any("after_tool_use" in e for e in errs)


def test_pre_final_llm_still_requires_criterion():
    rule = _llm(what={"kind": "llm_criterion", "payload": {}})
    errs = validate_custom_rule(rule)
    assert any("criterion is required" in e for e in errs)


# ---------------------------------------------------------------------------
# PR-F-UX1 Tier 2 lifecycle expansion — validate the two new firesAt slots
# (on_user_prompt_submit + on_subagent_stop) accept llm_criterion + audit AND
# nothing else. The matrix is intentionally narrow: block/retry/override on the
# Tier 2 slots is a runtime contract change deferred to a later PR.
# ---------------------------------------------------------------------------


def test_llm_criterion_audit_at_on_user_prompt_submit_accepted():
    rule = _llm(firesAt="on_user_prompt_submit", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_on_subagent_stop_accepted():
    rule = _llm(firesAt="on_subagent_stop", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_on_user_prompt_submit_rejected():
    """Block at Tier 2 slot is rejected: audit-only matrix entry."""
    rule = _llm(firesAt="on_user_prompt_submit", action="block")
    errs = validate_custom_rule(rule)
    assert any("on_user_prompt_submit" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_on_subagent_stop_accepted():
    """PR-F-LIFE1 — ``on_subagent_stop`` is lifted past audit-only: the
    backend ``_LEGAL`` matrix now accepts (llm_criterion ×
    on_subagent_stop × block) so an operator can author a "subagent must
    produce a summary"-style rule whose failed verdict is a directive to
    the PARENT caller. ``audit`` remains valid for the conservative case.
    """
    rule = _llm(firesAt="on_subagent_stop", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_on_subagent_stop_accepted():
    """PR-F-LIFE1 — ``ask_approval`` joins ``block`` + ``audit`` at the
    ``on_subagent_stop`` slot for the same reason (the verb is a directive
    to the parent caller, not a mutation of the already-emitted child
    output)."""
    rule = _llm(firesAt="on_subagent_stop", action="ask_approval")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_retry_at_on_subagent_stop_still_rejected():
    """PR-F-LIFE1 leaves ``retry`` out of the lift — retry has no honest
    runtime mapping after the child output has already been emitted."""
    rule = _llm(firesAt="on_subagent_stop", action="retry")
    errs = validate_custom_rule(rule)
    assert any("on_subagent_stop" in e and "audit" in e for e in errs), errs


def test_deterministic_ref_at_on_user_prompt_submit_rejected():
    """Only llm_criterion is wired to the two Tier 2 slots; other kinds reject."""
    rule = _det(firesAt="on_user_prompt_submit", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_user_prompt_submit" in e for e in errs), errs


def test_deterministic_ref_at_on_subagent_stop_rejected():
    rule = _det(firesAt="on_subagent_stop", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_subagent_stop" in e for e in errs), errs


def test_tier2_firesat_slots_listed_in_FIRES_AT():
    """Guard against drift — both new slots must be members of FIRES_AT."""
    from magi_agent.customize.custom_rules import FIRES_AT

    assert "on_user_prompt_submit" in FIRES_AT
    assert "on_subagent_stop" in FIRES_AT


# ---------------------------------------------------------------------------
# PR-F-LIFE1 — top-level turn-boundary slots. Validate that both
# before_turn_start and after_turn_end accept (llm_criterion + audit) and
# (deterministic_ref + audit), and reject non-audit actions / other kinds.
# Wired in magi_agent/runtime/governed_turn.run_governed_turn via
# magi_agent.customize.lifecycle_audit.{run_before_turn_start_audit,
# run_after_turn_end_audit}.
# ---------------------------------------------------------------------------


def test_llm_criterion_audit_at_before_turn_start_accepted():
    rule = _llm(firesAt="before_turn_start", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_after_turn_end_accepted():
    rule = _llm(firesAt="after_turn_end", action="audit")
    assert validate_custom_rule(rule) == []


def test_deterministic_ref_at_before_turn_start_rejected_no_runtime_fanout():
    """deterministic_ref has no fan-out at the turn-boundary slots; the
    validator must reject so operators do not persist inert rules.
    (Review pass on PR-F-LIFE1 dropped this from _LEGAL — only
    llm_criterion has a real lifecycle_audit fan-out at these slots.)"""
    rule = _det(firesAt="before_turn_start", action="audit")
    errs = validate_custom_rule(rule)
    assert any("before_turn_start" in e for e in errs), errs


def test_deterministic_ref_at_after_turn_end_rejected_no_runtime_fanout():
    rule = _det(firesAt="after_turn_end", action="audit")
    errs = validate_custom_rule(rule)
    assert any("after_turn_end" in e for e in errs), errs


def test_llm_criterion_block_at_before_turn_start_rejected():
    """Block at top-level turn entry would require a runtime contract change
    (engine stream has not started yet) — keep wire audit-only."""
    rule = _llm(firesAt="before_turn_start", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_turn_start" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_after_turn_end_rejected():
    """Block at top-level turn end has no honest target (emission already
    completed) — keep wire audit-only."""
    rule = _llm(firesAt="after_turn_end", action="block")
    errs = validate_custom_rule(rule)
    assert any("after_turn_end" in e and "audit" in e for e in errs), errs


def test_tool_perm_at_before_turn_start_rejected():
    """tool_perm has no honest mapping at a turn-boundary slot (no tool
    invocation is in flight)."""
    rule = _tool(firesAt="before_turn_start", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_turn_start" in e for e in errs), errs


def test_life1_firesat_slots_listed_in_FIRES_AT():
    """Guard against drift — both turn-boundary slots must be members of
    FIRES_AT so the validator's ``firesAt must be one of …`` check
    accepts them."""
    from magi_agent.customize.custom_rules import FIRES_AT

    assert "before_turn_start" in FIRES_AT
    assert "after_turn_end" in FIRES_AT
