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


def test_llm_criterion_block_at_on_user_prompt_submit_accepted():
    """PR-F-LIFE4a — ``on_user_prompt_submit`` lifted to {audit, block}.

    The gate fan-out
    (:func:`magi_agent.customize.lifecycle_audit.run_user_prompt_submit_gate`)
    is wired in ``runtime/governed_turn.run_governed_turn`` so the runtime
    short-circuits the engine stream when a block-action criterion fails.
    """
    rule = _llm(firesAt="on_user_prompt_submit", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_on_user_prompt_submit_still_rejected():
    """PR-F-LIFE4a leaves ``ask_approval`` out of the lift at
    ``on_user_prompt_submit`` — the design matrix targets ``{audit, block}``
    only (no honest approval surface at the inbound-prompt boundary today).
    """
    rule = _llm(firesAt="on_user_prompt_submit", action="ask_approval")
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


def test_llm_criterion_block_at_before_turn_start_accepted():
    """PR-F-LIFE4a — ``before_turn_start`` lifted to {audit, block,
    ask_approval}. The gate fan-out is consulted at the TOP of the
    governed-turn funnel so the runtime short-circuits the engine stream
    BEFORE ``rt.engine.run_turn_stream`` starts."""
    rule = _llm(firesAt="before_turn_start", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_before_turn_start_accepted():
    """PR-F-LIFE4a — ``ask_approval`` is also lifted at ``before_turn_start``.
    Honest-degrade today: the runtime records ``requires_approval=true`` on
    the audit ledger and proceeds — a real approval UI is a follow-up."""
    rule = _llm(firesAt="before_turn_start", action="ask_approval")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_after_turn_end_rejected():
    """Block at top-level turn end has no honest target (emission already
    completed) — keep wire audit-only."""
    rule = _llm(firesAt="after_turn_end", action="block")
    errs = validate_custom_rule(rule)
    assert any("after_turn_end" in e and "audit" in e for e in errs), errs


def test_llm_criterion_retry_at_before_turn_start_still_rejected():
    """PR-F-LIFE4a leaves ``retry`` out of the lift at ``before_turn_start``
    — retry has no honest runtime wire at top-level turn entry."""
    rule = _llm(firesAt="before_turn_start", action="retry")
    errs = validate_custom_rule(rule)
    assert any("before_turn_start" in e for e in errs), errs


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


def test_llm_criterion_block_at_before_llm_call_accepted():
    """PR-F-LIFE4a — ``before_llm_call`` lifted to {audit, block}. The same
    per-turn critic budget that gates the audit fan-out also gates the
    block decision (cannot block on a call the critic was never paid to
    evaluate), so a single misbehaving rule cannot multiply cost."""
    rule = _llm(firesAt="before_llm_call", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_after_llm_call_accepted():
    """PR-F-LIFE4a — ``after_llm_call`` lifted to {audit, block}. Block
    verdict signals the ADK after_model boundary to suppress the just-
    emitted response."""
    rule = _llm(firesAt="after_llm_call", action="block")
    assert validate_custom_rule(rule) == []


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


def test_llm_criterion_block_at_before_compaction_accepted():
    """PR-F-LIFE4a — ``before_compaction`` lifted to {audit, block}. The
    compaction plugin's gate consult sits at the same chokepoint as the
    audit emit; block verdict tells the plugin to skip the tail-drop."""
    rule = _llm(firesAt="before_compaction", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_block_at_after_compaction_rejected():
    """``after_compaction`` stays audit-only — the compaction has already
    taken effect on ``llm_request.contents`` by the time the audit fires."""
    rule = _llm(firesAt="after_compaction", action="block")
    errs = validate_custom_rule(rule)
    assert any("after_compaction" in e and "audit" in e for e in errs), errs


def test_llm_criterion_block_at_on_task_checkpoint_accepted():
    """PR-F-LIFE4a — ``on_task_checkpoint`` lifted to {audit, block,
    ask_approval}. Block verdict tells the work-queue driver to halt
    further state advancement for the task this tick."""
    rule = _llm(firesAt="on_task_checkpoint", action="block")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_ask_at_on_task_checkpoint_accepted():
    """PR-F-LIFE4a — ``ask_approval`` is the honest treatment for the
    "pause for human review" use case at the work-queue boundary."""
    rule = _llm(firesAt="on_task_checkpoint", action="ask_approval")
    assert validate_custom_rule(rule) == []


def test_llm_criterion_retry_at_on_task_checkpoint_rejected():
    """retry has no honest meaning at the work-queue dispatcher boundary
    (the task transition has already been recorded by the time the audit
    fires)."""
    rule = _llm(firesAt="on_task_checkpoint", action="retry")
    errs = validate_custom_rule(rule)
    assert any("on_task_checkpoint" in e for e in errs), errs


def test_llm_criterion_block_at_on_artifact_created_rejected():
    """Block at on_artifact_created is honestly impossible — the artifact
    has already been written by the provider by the time this emit
    fires. PR-F-LIFE4a only lifts ``ask_approval`` (review-pending)."""
    rule = _llm(firesAt="on_artifact_created", action="block")
    errs = validate_custom_rule(rule)
    assert any("on_artifact_created" in e for e in errs), errs


def test_llm_criterion_ask_at_on_artifact_created_accepted():
    """PR-F-LIFE4a — ``ask_approval`` is the honest verdict for
    ``on_artifact_created``: the artifact exists but the receipt is
    augmented with ``requires_approval=true`` so a follow-up approval
    surface can hold delivery."""
    rule = _llm(firesAt="on_artifact_created", action="ask_approval")
    assert validate_custom_rule(rule) == []


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
