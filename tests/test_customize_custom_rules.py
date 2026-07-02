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


# --- PR-P5.0: scope is optional (auto turn-scope retired) ---
def test_scope_absent_is_valid_global():
    # The reworked wizard stops sending coding/research/delivery scope; an
    # absent scope means "global" and must validate.
    rule = _det()
    del rule["scope"]
    assert validate_custom_rule(rule) == []


def test_scope_none_is_valid_global():
    assert validate_custom_rule(_det(scope=None)) == []


def test_legacy_scope_value_still_accepted():
    # Back-compat: a rule persisted with a known scope before the rework stays
    # valid (treated as global at runtime, which is already scope-blind).
    assert validate_custom_rule(_det(scope="coding")) == []


def test_present_but_unknown_scope_still_rejected():
    assert validate_custom_rule(_det(scope="bogus"))


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


def test_llm_after_tool_block_still_illegal():
    # ``block`` remains illegal at after_tool_use (the override gate cannot
    # retroactively block an already-dispatched tool result).
    rule = _llm(firesAt="after_tool_use", action="block",
                what={"kind": "llm_criterion", "payload": {"criterion": "x", "toolMatch": ["web_search"]}})
    assert validate_custom_rule(rule)


def test_llm_after_tool_audit_is_legal():
    # WS-B: ``audit`` is a legal action at after_tool_use — the criterion judge
    # runs and the verdict is recorded to the evidence ledger WITHOUT blocking.
    rule = _llm(firesAt="after_tool_use", action="audit",
                what={"kind": "llm_criterion", "payload": {"criterion": "x", "toolMatch": ["web_search"]}})
    assert validate_custom_rule(rule) == []


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


# ---------------------------------------------------------------------------
# PR-F-LIFE2 — per-LLM-call slots. before_llm_call + after_llm_call accept
# (llm_criterion + audit) ONLY (audit-only contract — surrounding ADK plugin
# enforces a per-turn critic budget via
# MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET, default 3). deterministic_ref /
# tool_perm / mutator kinds have no runtime fan-out at these slots
# (honest-degrade — review pass on F-LIFE1 established the same pattern).
# Wired in magi_agent/adk_bridge/lifecycle_llm_call_control.py via
# magi_agent.customize.lifecycle_audit.{run_before_llm_call_audit,
# run_after_llm_call_audit}.
# ---------------------------------------------------------------------------


def test_llm_criterion_audit_at_before_llm_call_accepted():
    rule = _llm(firesAt="before_llm_call", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_after_llm_call_accepted():
    rule = _llm(firesAt="after_llm_call", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_before_llm_call_rejected():
    """Block at the per-call boundary would amplify the runaway-cost risk
    (one bad rule blocks every LLM call within a turn) — keep audit-only
    in v1."""
    rule = _llm(firesAt="before_llm_call", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_llm_call" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_after_llm_call_rejected():
    rule = _llm(firesAt="after_llm_call", action="block")
    errs = validate_custom_rule(rule)
    assert any("after_llm_call" in e and "audit" in e for e in errs), errs


def test_llm_criterion_retry_at_before_llm_call_rejected():
    """retry has no honest meaning per-LLM-call (the call is already
    happening / has already happened)."""
    rule = _llm(firesAt="before_llm_call", action="retry")
    errs = validate_custom_rule(rule)
    assert any("before_llm_call" in e and "audit" in e for e in errs), errs


def test_llm_criterion_retry_at_after_llm_call_rejected():
    rule = _llm(firesAt="after_llm_call", action="retry")
    errs = validate_custom_rule(rule)
    assert any("after_llm_call" in e and "audit" in e for e in errs), errs


def test_deterministic_ref_at_before_llm_call_rejected_no_runtime_fanout():
    """deterministic_ref has no fan-out at the per-LLM-call slots — the
    surrounding ADK plugin only wires the criterion judge. Reject so
    operators don't persist inert rules."""
    rule = _det(firesAt="before_llm_call", action="audit")
    errs = validate_custom_rule(rule)
    assert any("before_llm_call" in e for e in errs), errs


def test_deterministic_ref_at_after_llm_call_rejected_no_runtime_fanout():
    rule = _det(firesAt="after_llm_call", action="audit")
    errs = validate_custom_rule(rule)
    assert any("after_llm_call" in e for e in errs), errs


def test_tool_perm_at_before_llm_call_rejected():
    """tool_perm has no honest mapping at a per-LLM-call slot (no tool
    invocation is in flight at the model-callback boundary)."""
    rule = _tool(firesAt="before_llm_call", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_llm_call" in e for e in errs), errs


def test_life2_firesat_slots_listed_in_FIRES_AT():
    """Guard against drift — both per-LLM-call slots must be members of
    FIRES_AT so the validator's ``firesAt must be one of …`` check
    accepts them."""
    from magi_agent.customize.custom_rules import FIRES_AT

    assert "before_llm_call" in FIRES_AT
    assert "after_llm_call" in FIRES_AT


# ---------------------------------------------------------------------------
# PR-F-LIFE3 — four NEW emitter slots: before_compaction / after_compaction /
# on_task_checkpoint / on_artifact_created. All four accept (llm_criterion +
# audit) ONLY. Honest-degrade matches the F-LIFE1/2 pattern: deterministic_ref
# / tool_perm / mutator kinds have no runtime fan-out at these chokepoints
# (the compaction plugin / work-queue driver / file-delivery boundary call
# only the lifecycle_audit fan-out helpers). Wired by:
#   * magi_agent/adk_bridge/context_compaction.py
#     (run_before_compaction_audit + run_after_compaction_audit)
#   * magi_agent/missions/work_queue/driver.py
#     (run_task_checkpoint_audit at claimed/completed/failed/short_circuited)
#   * magi_agent/artifacts/file_delivery.py
#     (run_artifact_created_audit on the write_artifact ok-status branch)
# All gated by MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED.
# ---------------------------------------------------------------------------


def test_llm_criterion_audit_at_before_compaction_accepted():
    rule = _llm(firesAt="before_compaction", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_after_compaction_accepted():
    rule = _llm(firesAt="after_compaction", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_on_task_checkpoint_accepted():
    rule = _llm(firesAt="on_task_checkpoint", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_audit_at_on_artifact_created_accepted():
    rule = _llm(firesAt="on_artifact_created", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_before_compaction_rejected():
    """Block at a compaction chokepoint has no honest meaning: the
    compaction decision is owned by the surrounding plugin, not the audit
    fan-out — keep audit-only in v1."""
    rule = _llm(firesAt="before_compaction", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_compaction" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_after_compaction_rejected():
    rule = _llm(firesAt="after_compaction", action="block")
    errs = validate_custom_rule(rule)
    assert any("after_compaction" in e and "audit" in e for e in errs), errs


def test_llm_criterion_retry_at_on_task_checkpoint_rejected():
    """retry has no honest meaning at the work-queue dispatcher boundary
    (the task transition has already been recorded by the time the audit
    fires)."""
    rule = _llm(firesAt="on_task_checkpoint", action="retry")
    errs = validate_custom_rule(rule)
    assert any("on_task_checkpoint" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_on_artifact_created_rejected():
    """Block at on_artifact_created is honestly impossible — the artifact
    has already been written by the provider by the time this emit
    fires. Keep audit-only."""
    rule = _llm(firesAt="on_artifact_created", action="block")
    errs = validate_custom_rule(rule)
    assert any("on_artifact_created" in e and "audit" in e for e in errs), errs


def test_deterministic_ref_at_before_compaction_rejected_no_runtime_fanout():
    """deterministic_ref has no fan-out at the four new emitter slots —
    the surrounding runtime sites only call the lifecycle_audit helpers
    (which consume llm_criterion only). Reject so operators don't
    persist inert rules."""
    rule = _det(firesAt="before_compaction", action="audit")
    errs = validate_custom_rule(rule)
    assert any("before_compaction" in e for e in errs), errs


def test_deterministic_ref_at_on_task_checkpoint_rejected_no_runtime_fanout():
    rule = _det(firesAt="on_task_checkpoint", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_task_checkpoint" in e for e in errs), errs


def test_deterministic_ref_at_on_artifact_created_rejected_no_runtime_fanout():
    rule = _det(firesAt="on_artifact_created", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_artifact_created" in e for e in errs), errs


def test_tool_perm_at_before_compaction_rejected():
    """tool_perm has no honest mapping at a compaction chokepoint (no
    tool invocation is in flight at the model-callback before-trim
    boundary)."""
    rule = _tool(firesAt="before_compaction", action="block")
    errs = validate_custom_rule(rule)
    assert any("before_compaction" in e for e in errs), errs


def test_life3_firesat_slots_listed_in_FIRES_AT():
    """Guard against drift — all four PR-F-LIFE3 emitter slots must be
    members of FIRES_AT so the validator's ``firesAt must be one of …``
    check accepts them."""
    from magi_agent.customize.custom_rules import FIRES_AT

    assert "before_compaction" in FIRES_AT
    assert "after_compaction" in FIRES_AT
    assert "on_task_checkpoint" in FIRES_AT
    assert "on_artifact_created" in FIRES_AT


# ---------------------------------------------------------------------------
# PR-F-LIFE4b — three NEW task / session boundary slots:
# on_task_complete / on_session_start / on_session_end. All three accept
# ``llm_criterion`` only (audit-only fan-out shape inherited from F-LIFE3).
# Per honest runtime contract:
#   * on_task_complete: {audit, block, ask_approval}. Block records the
#     audit ledger entry but does not roll back the already-emitted final
#     turn (matches on_subagent_stop honest-degrade).
#   * on_session_start: {audit, block}. Block REPLACES the model output
#     with a synthetic policy-blocked response via the ADK before_model
#     boundary (refuses the session).
#   * on_session_end: {audit} only. The session has already ended by
#     the time the audit fires.
# Wired by:
#   * magi_agent/runtime/governed_turn.py (run_task_complete_audit at the
#     finally block, gated by <task_done> marker presence in final text)
#   * magi_agent/adk_bridge/lifecycle_session_control.py
#     (run_session_start_audit at first-fire-per-session detection)
#   * (on_session_end is honest-degrade in v1 — no transport-side wire)
# All gated by MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED.
# ---------------------------------------------------------------------------


def test_llm_criterion_audit_at_on_task_complete_accepted():
    rule = _llm(firesAt="on_task_complete", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_on_task_complete_accepted():
    """PR-F-LIFE4b — ``on_task_complete`` accepts block (records the audit
    ledger entry but does not roll back the already-emitted final turn,
    matches the on_subagent_stop honest-degrade pattern)."""
    rule = _llm(firesAt="on_task_complete", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_on_task_complete_accepted():
    """PR-F-LIFE4b — ``ask_approval`` surfaces requires_approval=true."""
    rule = _llm(firesAt="on_task_complete", action="ask_approval")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_retry_at_on_task_complete_rejected():
    """retry has no honest meaning at the task-complete boundary — the
    final turn has already emitted by the time the audit fires."""
    rule = _llm(firesAt="on_task_complete", action="retry")
    errs = validate_custom_rule(rule)
    assert any("on_task_complete" in e for e in errs), errs


def test_llm_criterion_audit_at_on_session_start_accepted():
    rule = _llm(firesAt="on_session_start", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_on_session_start_accepted():
    """PR-F-LIFE4b — ``on_session_start`` accepts block (LifecycleSessionControl
    short-circuits the model call with a synthetic policy-blocked response,
    refusing the session)."""
    rule = _llm(firesAt="on_session_start", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_on_session_start_rejected():
    """ask_approval is NOT exposed at on_session_start in v1: the only
    honest treatments are audit (record + proceed) and block (refuse the
    session)."""
    rule = _llm(firesAt="on_session_start", action="ask_approval")
    errs = validate_custom_rule(rule)
    assert any("on_session_start" in e for e in errs), errs


def test_llm_criterion_audit_at_on_session_end_accepted():
    rule = _llm(firesAt="on_session_end", action="audit")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_on_session_end_rejected():
    """Block at on_session_end is honestly impossible — the session has
    already ended by the time this emit fires (mirrors after_turn_end /
    after_compaction)."""
    rule = _llm(firesAt="on_session_end", action="block")
    errs = validate_custom_rule(rule)
    assert any("on_session_end" in e for e in errs), errs


def test_llm_criterion_ask_at_on_session_end_rejected():
    """ask is also honestly impossible at on_session_end (the session is
    over — no consumer to ask)."""
    rule = _llm(firesAt="on_session_end", action="ask_approval")
    errs = validate_custom_rule(rule)
    assert any("on_session_end" in e for e in errs), errs


def test_deterministic_ref_at_on_task_complete_rejected_no_runtime_fanout():
    """deterministic_ref has no fan-out at the three new emitter slots —
    the surrounding runtime sites only call the lifecycle_audit helpers
    (which consume llm_criterion only)."""
    rule = _det(firesAt="on_task_complete", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_task_complete" in e for e in errs), errs


def test_deterministic_ref_at_on_session_start_rejected_no_runtime_fanout():
    rule = _det(firesAt="on_session_start", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_session_start" in e for e in errs), errs


def test_deterministic_ref_at_on_session_end_rejected_no_runtime_fanout():
    rule = _det(firesAt="on_session_end", action="audit")
    errs = validate_custom_rule(rule)
    assert any("on_session_end" in e for e in errs), errs


def test_tool_perm_at_on_task_complete_rejected():
    """tool_perm has no honest mapping at the task-complete chokepoint
    (no tool invocation is in flight)."""
    rule = _tool(firesAt="on_task_complete", action="block")
    errs = validate_custom_rule(rule)
    assert any("on_task_complete" in e for e in errs), errs


def test_life4b_firesat_slots_listed_in_FIRES_AT():
    """Guard against drift — all three PR-F-LIFE4b boundary slots must
    be members of FIRES_AT so the validator's ``firesAt must be one of
    …`` check accepts them."""
    from magi_agent.customize.custom_rules import FIRES_AT

    assert "on_task_complete" in FIRES_AT
    assert "on_session_start" in FIRES_AT
    assert "on_session_end" in FIRES_AT


# --- PR-D0: reference-safe custom-rule id validation --------------------------


def test_id_absent_is_valid():
    # The transport backfills cr_<uuid> when absent, so a rule with no id is
    # valid at the validator layer.
    assert "id" not in _det()
    assert validate_custom_rule(_det()) == []


def test_valid_ids_accepted():
    for rid in ("cr_abc", "block-secrets", "rule-001", "cr_" + "a1b2c3d4" * 4):
        assert validate_custom_rule(_det(id=rid)) == [], rid


def test_id_with_colon_rejected():
    errs = validate_custom_rule(_det(id="custom_rule:foo"))
    assert any(e.startswith("id must be") for e in errs), errs


def test_id_with_whitespace_rejected():
    assert any(e.startswith("id must be") for e in validate_custom_rule(_det(id="a b")))


def test_empty_or_nonstring_id_rejected():
    assert any(e.startswith("id must be") for e in validate_custom_rule(_det(id="")))
    assert any(e.startswith("id must be") for e in validate_custom_rule(_det(id=123)))


def test_id_length_boundary_matches_frontend_contract():
    # 128 chars accepted (matches the guided-wizard Policy-ID max), 129 rejected.
    assert validate_custom_rule(_det(id="a" * 128)) == []
    assert any(e.startswith("id must be") for e in validate_custom_rule(_det(id="a" * 129)))
