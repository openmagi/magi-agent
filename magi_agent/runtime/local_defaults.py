from __future__ import annotations

from collections.abc import Mapping, MutableMapping

SAFE_RUNTIME_PROFILES = frozenset({"safe", "off", "minimal", "conservative", "eval"})
LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV = "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS"

LOCAL_FULL_RUNTIME_ENV_DEFAULTS: Mapping[str, str] = {
    "MAGI_RUNTIME_PROFILE": "full",
    "MAGI_AGENT_LOCAL_CHAT_ROUTE": "on",
    "MAGI_STREAMING_CHAT": "on",
    "MAGI_FIRST_PARTY_TOOLS_ENABLED": "1",
    "MAGI_RUNNER_POLICY_ROUTING_ENABLED": "1",
    # Route denial is useful audit metadata, but hard-blocking it breaks live
    # turns when the materialized budget/capability estimate is conservative.
    "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED": "0",
    # Subagent routes reflect configured provider keys so a fireworks-only bot
    # never advertises keyless anthropic/openai routes it cannot use.
    "MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED": "1",
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    # WS5 PR5a: recover a "tools ran but the model said nothing" turn by re-invoking
    # once (the recovery + grace helpers are already wired; this flips them ON so a
    # default self-host turn does not silently complete blank).
    "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
    # WS5 PR5b: do a bounded second attempt whose final message asks for an answer
    # OR an explicit "what is blocking me" statement; if still empty, stream a
    # deterministic blocked notice so the turn ends honestly instead of blank.
    # Inert unless the recovery flag above is also ON.
    "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": "1",
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    # WS4 PR4b: the live MagiContextCompactionPlugin honors this when
    # MAGI_CONTEXT_COMPACTION_ENABLED is ON, applying tier-6 collapse-drain and
    # tier-7 reactive-compact at CRITICAL so a long session does not wedge on
    # context overflow (the cost-free reliability safety net; tier-7 defaults to
    # the hermetic stub summarizer).
    "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    # WS9 PR9c: MCP connection resilience (caller-bounded timeout + per-endpoint
    # circuit breaker + bounded reconnect) for the live composio dispatcher seam.
    # Pure in-memory, accuracy-neutral, so it is safe in the full profile.
    "MAGI_MCP_RESILIENCE_ENABLED": "1",
    # WS8 PR8a-3: Telegram inbound poll resilience (full-jitter backoff +
    # per-watcher circuit breaker + one-shot persistent-failure notice). Pure
    # in-memory, no persistence, so it is operationally safe in the full profile.
    "MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED": "1",
    "MAGI_SELF_REVIEW_ENABLED": "1",
    "MAGI_SELF_REVIEW_SHADOW": "0",
    "MAGI_SELF_REVIEW_PIPELINE_ENABLED": "1",
    "MAGI_SELF_REVIEW_LIVE_ENABLED": "1",
    "MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1",
    "MAGI_READ_LEDGER_ENABLED": "1",
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": "1",
    "MAGI_LSP_DIAGNOSTICS_ENABLED": "1",
    "MAGI_READ_QUALITY_ENABLED": "1",
    "MAGI_RIPGREP_ENABLED": "1",
    "MAGI_APPLY_PATCH_ENABLED": "1",
    "MAGI_PROVIDER_REPAIR_ENABLED": "1",
    "MAGI_TOOL_CONCURRENCY_ENABLED": "1",
    "MAGI_MAX_TOOL_CONCURRENCY": "8",
    "MAGI_MODEL_AWARE_PROMPTS_ENABLED": "1",
    "MAGI_CODING_REPAIR_LOOP_ENABLED": "1",
    "MAGI_GA_LIVE_ENABLED": "1",
    # A1 measurement: observe-only citation audit on by default in the full
    # local profile; reports persist to the durable evidence dir so the
    # default-ON enforce flip can be justified with measured FP data.
    "MAGI_RESEARCH_GOVERNANCE_MODE": "audit",
    "MAGI_MESSAGE_CACHE_ENABLED": "1",
    "MAGI_PROMPT_CACHE_ENABLED": "1",
    "MAGI_FILE_TOOLS_ENABLED": "1",
    "MAGI_BROWSER_TOOL_ENABLED": "1",
    "MAGI_SELF_INTROSPECTION_ENABLED": "1",
    "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED": "1",
    # Belt-and-suspenders memory master (WS2 PR2a). The shipped CLI bootstrap
    # (cli/memory_bootstrap.apply_memory_config_bootstrap) already setdefaults
    # MAGI_MEMORY_ENABLED=1 BEFORE this overlay runs at real startup, so this
    # entry is a no-op there. It exists so the overlay surface is self-consistent
    # with the bootstrap and so a non-CLI embedder that applies this overlay
    # WITHOUT the bootstrap still resolves the master ON. setdefault semantics
    # mean an explicit operator MAGI_MEMORY_ENABLED=0 still wins per-flag. Kept in
    # LOCAL_FULL only; never widened onto a lean/safe/eval profile.
    "MAGI_MEMORY_ENABLED": "1",
    "MAGI_MEMORY_WRITE_READINESS_ENABLED": "1",
    "MAGI_MEMORY_WRITE_ENABLED": "1",
    "MAGI_MEMORY_LOCAL_DEV": "1",
    # WS2 PR2c: the per-turn recall reranker (cheap-model semantic reorder + the
    # staleness-note scan). This flag follows NO master and is set by NO other
    # profile, so rerank/staleness are inert on a shipped full install unless we
    # seed them here. Turning it ON in the full local profile is the only
    # behaviour-changing activation in WS2. Fail-open in the callee (no key -> BM25
    # order), so it never breaks a turn. setdefault keeps an explicit operator
    # MAGI_MEMORY_RECALL_RERANK_ENABLED=0 winning. lab inherits this via
    # apply_local_full_runtime_defaults; safe/eval/off never apply this overlay.
    "MAGI_MEMORY_RECALL_RERANK_ENABLED": "1",
    "MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED": "1",
    # CC-style user HookBus (~/.magi/settings.json + <workspace>/.magi/settings.json).
    # ON for self-host (full) and lab; a no-op unless the user authored hooks. This
    # runs user-supplied bash/http/llm at lifecycle points, so it is SELF-HOST ONLY:
    # hosted multi-tenant deployments do NOT apply this overlay (and must keep it
    # OFF). Read via a raw env helper (not profile-aware), so test/import default
    # stays OFF — only the CLI/serve startup overlay flips it on.
    "MAGI_USER_HOOKS_ENABLED": "1",
    "MAGI_DEFERRED_TOOLS_ENABLED": "1",
    "MAGI_WORKFLOW_EXECUTOR_ENABLED": "1",
    "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
    "MAGI_CHILD_RUNNER_TOOLSET": "readonly",
    "MAGI_SCHEDULER_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_SHADOW": "0",
    "MAGI_OBSERVABILITY_ENABLED": "1",
    "MAGI_OBS_HOME": ".openmagi",
    "MAGI_SESSION_PERSISTENCE_ENABLED": "1",
    # WS1 PR1e: the CLI Envelope-log TEXT write path, flipped ON for the full
    # local profile. This is the v3 replay source the context-only durable
    # foreground resume reads (replay_messages_up_to), NOT the obs
    # MAGI_SESSION_TRANSCRIPT_ENABLED. It stages ON together with the durable
    # substrate below so a crashed local turn has an Envelope log to replay. The
    # safe / eval / off profiles keep it OFF (the eval overlay registers it at
    # "0"); the helper default (cli_session_log_enabled) stays strict default-OFF.
    "MAGI_CLI_SESSION_LOG_ENABLED": "1",
    # WS1 PR1e: durable crash-resume substrate, activated for the full local
    # (self-host) profile. The master sqlite-write gate
    # (MAGI_DURABLE_LOCAL_WRITES_ENABLED) creates/writes the local
    # durable_checkpoints + plan_ledger tables in ~/.magi/work_queue.db (local
    # sqlite only, never the hosted DB); MAGI_DURABLE_CHECKPOINTS_ENABLED emits a
    # checkpoint from the headless tap after each persisted tool_end + at the
    # terminal; MAGI_DURABLE_STARTUP_RECOVERY_ENABLED runs the boot sweep that
    # reclaims dead-pid background tasks (at-least-once) on the next CLI boot.
    # MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED is deliberately NOT set: the
    # OPTIONAL context-only foreground continuation is opt-in only, so the v1
    # full profile delivers the PRIMARY T1 background reclaim and never auto-runs
    # the foreground re-entry. Safe / eval / off profiles keep all of these OFF;
    # the registry defaults stay default-OFF so a fresh import is byte-identical.
    "MAGI_DURABLE_LOCAL_WRITES_ENABLED": "1",
    "MAGI_DURABLE_CHECKPOINTS_ENABLED": "1",
    "MAGI_DURABLE_STARTUP_RECOVERY_ENABLED": "1",
    # The durable work-queue dispatcher tick loop that re-runs reclaimed tasks.
    "MAGI_WORK_QUEUE_EXECUTOR_ENABLED": "1",
    # M4 (missions x work-queue unification): the work-queue IS the mission
    # substrate, so a self-host install surfaces its background tasks by default.
    # BOARD_API/NOTIFY are profile-aware default-ON (_pb) and would self-enable
    # under MAGI_RUNTIME_PROFILE=full anyway; seed them explicitly here to match
    # the EXECUTOR line above (belt-and-suspenders, setdefault semantics keep an
    # explicit operator "0" winning).
    #   * MAGI_WORK_QUEUE_BOARD_API_ENABLED - the read-only board HTTP API. Auth
    #     is fail-closed Bearer against the gateway token (board_api.py:38-45), so
    #     ON is safe.
    #   * MAGI_WORK_QUEUE_NOTIFY_ENABLED - the terminal-event notifier so a
    #     completed background task announces into the next reply.
    "MAGI_WORK_QUEUE_BOARD_API_ENABLED": "1",
    "MAGI_WORK_QUEUE_NOTIFY_ENABLED": "1",
    # The agent-facing background-task tool. Both flags are required before
    # run_in_background creates a real WorkTask (scheduled_work.py:145-181): the
    # TOOL flag exposes the entrypoint live, ATTACHED tells the honest gate a real
    # SqliteWorkQueueStore is wired. The store IS real and the executor is
    # default-ON locally, so the honest-block precondition ("until a real job
    # store is wired") is satisfied. MAGI_SCHEDULER_ATTACHED stays UNSET: cron
    # scheduling is a separate unbuilt surface (scheduled_work.py:59,77,83).
    "MAGI_BACKGROUND_TASK_TOOL_ENABLED": "1",
    "MAGI_BACKGROUND_TASKS_ATTACHED": "1",
    # M9 (missions x serve process-model): the gateway daemon supervises the
    # watcher fleet (work_queue_executor + notify + mission_action_reconciler).
    # M4 above flips the work-queue EXECUTOR gate ON, but nothing hosts the
    # daemon on the serve path, so the executor never actually runs. M9 co-locates
    # the daemon in the ``create_app`` serve lifespan; this flag turns it ON so a
    # fresh ``magi serve`` runs the executor in the same process as uvicorn.
    # Raw env read (NOT a registered flag; read via ``is_gateway_daemon_enabled``
    # -> ``config._truthy.env_bool``), so the local overlay is the only thing that
    # turns it on for a served install. setdefault semantics keep an explicit
    # operator "0" winning; safe/eval/off never apply this overlay. Hosted
    # enablement is C1's job (setting the env on the pod); M9 does not touch the
    # hosted tpl.
    "MAGI_GATEWAY_DAEMON_ENABLED": "1",
    # WS3 PR3c: durable cross-turn plan/todo ledger + evidence-first goal
    # completion, activated for the full local (self-host) profile.
    # MAGI_PLAN_LEDGER_DURABLE_ENABLED appends each TodoWrite full-snapshot to
    # <workspace_root>/.magi/durable/plan_ledger/<session_id>.jsonl and re-seeds
    # the in-memory todo list on the next per-turn handler-set build, so a
    # multi-step plan survives turns and process restarts (the index half stays
    # a no-op until WS1's durable store is injected).
    # MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED activates the deterministic
    # pre-judge resolver: an all-complete durable ledger short-circuits to
    # ``done`` and a clean stop short of verifiable completion emits an honest
    # ``goal_paused`` (SEAM 2, hoisted outside the goal-loop guard, so it is
    # reachable with the lab judge loop OFF and adds ZERO model calls).
    # MAGI_GOAL_LOOP_ENABLED is now a profile-aware default-ON flag (the ledger-
    # first auto-continue authority), so it self-enables under the full profile
    # via the profile resolver and does not need an explicit seed here. The brake
    # is a deterministic measurable-progress gate (ok tool end OR ledger delta OR
    # new evidence), NOT the cost-bearing LLM judge, so ambient auto-continue adds
    # no per-turn model calls in the common open-todo case.
    # MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE stays unset globally so
    # free-form chat is never forced to provide evidence (it is recipe-scoped).
    # Safe / eval / off profiles keep all WS3 flags OFF; the registry defaults
    # stay default-OFF so a fresh import is byte-identical.
    "MAGI_PLAN_LEDGER_DURABLE_ENABLED": "1",
    "MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED": "1",
    # PR-04-PR2: --resume/--continue rehydration. Staging complete: ON together
    # with the session-log write path above. An explicit
    # MAGI_CLI_RESUME_ENABLED=0 still wins via the setdefault overlay semantics.
    "MAGI_CLI_RESUME_ENABLED": "1",
    "MAGI_LEARNING_ENABLED": "true",
    "MAGI_LEARNING_REFLECTION_ENABLED": "1",
    "MAGI_LEARNING_DASHBOARD_ENABLED": "1",
    "MAGI_LEARNING_TELEMETRY_ENABLED": "1",
    "MAGI_LEARNING_LIVE_ENABLED": "1",
    "MAGI_SKILL_CURATOR_ENABLED": "1",
    "MAGI_SKILL_CURATOR_SHADOW": "0",
    "MAGI_AUTOPILOT": "1",
    # Native encrypted local vault backend: ON only for the local serve overlay
    # so the dashboard "Credentials" registration works out-of-the-box. Hosted
    # bots never run this overlay (and set MAGI_VAULT_ADMIN_URL once a hosted
    # vault exists), so they stay on the disabled/pending path and never write
    # secrets to a PVC. The helper default (local_vault_enabled) stays OFF.
    "MAGI_LOCAL_VAULT_ENABLED": "1",
    # Agent Vault Phase 2: local credential-injecting forward proxy. ON only for
    # the local serve overlay (same gating as MAGI_LOCAL_VAULT_ENABLED above), so
    # the bot can USE a registered credential without seeing it. Requires the
    # optional ``magi-agent[vault]`` extra (mitmproxy); when the extra is missing
    # the serve bootstrap logs an install hint and continues WITHOUT the proxy.
    # Hosted bots never run this overlay (and set MAGI_VAULT_ADMIN_URL once a
    # hosted vault exists), so they never start the local proxy.
    "MAGI_LOCAL_VAULT_PROXY_ENABLED": "1",
}


EVAL_RUNTIME_ENV_DEFAULTS: Mapping[str, str] = {
    # Profile identity
    "MAGI_RUNTIME_PROFILE": "eval",
    "MAGI_TASK_TYPES": "coding",
    # Tool caps. A coding agent must be able to run real test suites: 120s
    # command budget, 128KB head+tail-bounded output, and a generous per-turn
    # call budget (reference scaffolds routinely exceed 100 tool calls).
    "MAGI_TOOL_MAX_OUTPUT_BYTES": "131072",
    "MAGI_TOOL_COMMAND_TIMEOUT_MS": "120000",
    "MAGI_TOOL_MAX_CALLS_PER_TURN": "512",
    # Model reasoning: published benchmark numbers are measured with adaptive
    # thinking at high effort; benchmark the same model mode.
    "MAGI_MODEL_REASONING_EFFORT": "max",
    # Coding capability ON
    "MAGI_FIRST_PARTY_TOOLS_ENABLED": "1",
    "MAGI_EDIT_FUZZY_MATCH_ENABLED": "1",
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_READ_LEDGER_ENABLED": "1",
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": "1",
    "MAGI_READ_QUALITY_ENABLED": "1",
    "MAGI_RIPGREP_ENABLED": "1",
    "MAGI_APPLY_PATCH_ENABLED": "1",
    "MAGI_MODEL_AWARE_PROMPTS_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    # Relaxed loop-guard thresholds for eval: a coding agent legitimately
    # re-runs tests/greps dozens of times. The conservative production defaults
    # (soft=3/hard=5/freq-soft=15/freq-hard=30) block that iteration.
    # In an ephemeral eval sandbox a true infinite loop only wastes the per-
    # instance timeout, so generous thresholds are safe here.
    "MAGI_LOOP_GUARD_SOFT_THRESHOLD": "25",
    "MAGI_LOOP_GUARD_HARD_THRESHOLD": "50",
    "MAGI_LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD": "80",
    "MAGI_LOOP_GUARD_FREQUENCY_HARD_THRESHOLD": "200",
    "MAGI_TOOL_CONCURRENCY_ENABLED": "1",
    "MAGI_PROVIDER_REPAIR_ENABLED": "1",
    "MAGI_MESSAGE_CACHE_ENABLED": "1",
    "MAGI_PROMPT_CACHE_ENABLED": "1",
    "MAGI_FILE_TOOLS_ENABLED": "1",
    "MAGI_TRUSTED_LOCAL_SHELL_ENABLED": "1",
    # Delivery machinery OFF
    "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED": "0",
    "MAGI_GA_LIVE_ENABLED": "0",
    "MAGI_CODING_REPAIR_LOOP_ENABLED": "0",
    "MAGI_SELF_REVIEW_ENABLED": "0",
    "MAGI_AUTOPILOT": "0",
    "MAGI_SESSION_PERSISTENCE_ENABLED": "0",
    "MAGI_CLI_SESSION_LOG_ENABLED": "0",
    "MAGI_CLI_RESUME_ENABLED": "0",
    "MAGI_CONTEXT_COMPACTION_ENABLED": "0",
    "MAGI_LEARNING_ENABLED": "false",
    "MAGI_SKILL_CURATOR_ENABLED": "0",
    "MAGI_RUNNER_POLICY_ROUTING_ENABLED": "0",
    # Eval-specific prompt + guard flags (P2 + P3)
    "MAGI_EVAL_AUTONOMY_ENABLED": "1",
    "MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED": "1",
}


# Lab (experimental) runtime tier. ``MAGI_RUNTIME_PROFILE=lab`` is a single
# opt-in dogfood switch that turns ON the full experimental flat-flag set on top
# of the local-full overlay. ``lab`` is intentionally NOT in
# ``SAFE_RUNTIME_PROFILES``, so it already inherits every profile-aware
# default-ON (``profile_bool``) seam and the LOCAL_FULL seed; this mapping only
# adds the remaining strict-truthy ``_b`` experimental flags whose registry
# default is OFF. Registry defaults in ``config/flags.py`` are unchanged: a
# fresh install (or any safe/eval profile) stays conservative. Every entry is
# applied with ``setdefault`` so an explicit ``MAGI_X=0`` walks the feature back
# per-flag.
LAB_EXPERIMENTAL_FLAGS: tuple[str, ...] = (
    "MAGI_BROWSER_TOOL_ENABLED",
    "MAGI_COMPACTION_ANCHORED_SUMMARY_ENABLED",
    "MAGI_COMPACTION_MANUAL_ENABLED",
    "MAGI_COMPACTION_REAL_TOKENS_ENABLED",
    "MAGI_COMPACTION_SUMMARIZE_ENABLED",
    "MAGI_COMPACTION_TOOL_PRUNE_ENABLED",
    "MAGI_COMPUTE_VIA_CODE_ENABLED",
    "MAGI_CROSS_VERIFY_ENABLED",
    # F4 capability_scope custom rule: spawn-time toolset cap (deny tool names /
    # max permission class) authored on top of the parent_cap intersection.
    # Strict default-OFF in the registry so a fresh install / hosted serve stays
    # byte-identical; lab opts in so dogfood spawns honor operator-authored caps.
    "MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED",
    # F7 cost-vocabulary applier: project Customize budgets onto the live
    # MAGI_* env (tool calls per turn / max-steps brake / loop-guard hard) via
    # ``setdefault`` (operator env always wins). Strict default-OFF in the
    # registry so a fresh install / hosted serve stays byte-identical; lab opts
    # in so dogfood turns honor operator-authored budgets out-of-the-box.
    "MAGI_CUSTOMIZE_BUDGETS_ENABLED",
    # PR-F-UX1 Tier 2 lifecycle expansion: activate the two new audit-only
    # custom_rule gate sites (on_user_prompt_submit + on_subagent_stop).
    # Both fan-outs are no-ops without authored rules; lab opts in so dogfood
    # turns can exercise the wizard's new lifecycle options end-to-end.
    "MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED",
    # PR-F-LIFE1 Tier 2 lifecycle expansion (turn boundaries): activate the
    # two new audit-only custom_rule gate sites (before_turn_start +
    # after_turn_end) wired in run_governed_turn. Both fan-outs are no-ops
    # without authored rules; lab opts in so dogfood turns can exercise the
    # wizard's new lifecycle options end-to-end. Strict default-OFF in the
    # registry so a fresh install / hosted serve stays byte-identical.
    "MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED",
    # PR-F-LIFE2 Tier 2 lifecycle expansion (per-LLM-call boundaries):
    # activate the two new audit-only custom_rule gate sites
    # (before_llm_call + after_llm_call) wired adjacent to the ADK
    # before/after model callback boundary. Hard-capped at
    # MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET combined invocations per turn
    # (default 3) to prevent runaway critic cost on the per-call hot
    # path; both fan-outs are no-ops without authored rules. Lab opts in
    # so dogfood turns can exercise the wizard's new lifecycle options
    # end-to-end. Strict default-OFF in the registry so a fresh install /
    # hosted serve stays byte-identical (the ADK plugin is not
    # registered when the master flag is OFF).
    "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED",
    # PR-F-LIFE3 Tier 2 lifecycle expansion (four new emitter slots):
    # activate audit-only custom_rule gate sites at before_compaction,
    # after_compaction (wired around MagiContextCompactionPlugin
    # ._apply_tail_trim), on_task_checkpoint (wired at each work-queue
    # task status transition inside WorkQueueDriver.run_once), and
    # on_artifact_created (wired after a successful
    # FileDeliveryBoundary.execute write_artifact ok-status branch). All
    # four fan-outs are no-ops without authored rules; lab opts in so
    # dogfood turns can exercise the wizard's new lifecycle options
    # end-to-end. Strict default-OFF in the registry so a fresh
    # install / hosted serve stay byte-identical.
    "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED",
    # PR-F-LIFE4b Tier 2 lifecycle expansion (task / session boundary
    # emitters): activate custom_rule gate sites at on_task_complete
    # (wired in run_governed_turn's finally block, fires when the agent
    # declares a multi-turn user task done), on_session_start (wired by
    # LifecycleSessionControl via first-fire-per-session detection on
    # the ADK before_model boundary), and on_session_end (honest-
    # degrade in v1 — wizard exposes the slot, runtime emit wire ships
    # in a follow-up). All three are no-ops without authored rules; lab
    # opts in so dogfood turns can exercise the wizard's new lifecycle
    # options end-to-end. Strict default-OFF in the registry so a
    # fresh install / hosted serve stays byte-identical.
    "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED",
    # PR-F-MUT1 prompt_injection mutator: append to a tool's arguments before
    # dispatch OR append a new section to the assembled system prompt. Both
    # wires are no-ops without authored rules; lab opts in so dogfood turns
    # can exercise the wizard's new mutator kind end-to-end. Strict
    # default-OFF in the registry so a fresh install / hosted serve stays
    # byte-identical.
    "MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED",
    # PR-F-LIFE5 Self Improvement recipe enable: sibling gate to
    # MAGI_LEARNING_ENABLED. When ON (alongside the master flag) the
    # Customize dashboard's Self Improvement recipe maps to the real
    # ``openmagi.self-improvement`` pack — disabling the recipe in the UI
    # subtracts its evidence/validator refs from the assembled
    # requirements. The two frozen safety policies
    # (policy:self-improvement.eval-observation-required@1,
    # policy:self-improvement.no-direct-mutation@1) remain enforced
    # regardless. Strict default-OFF in the registry so a fresh install /
    # hosted serve stay byte-identical; lab opts in so dogfood turns can
    # exercise the toggle end-to-end.
    "MAGI_CUSTOMIZE_SELF_IMPROVEMENT_ENABLED",
    # PR-F-MUT2 output_rewrite mutator: re.sub-based redact of a tool's
    # output text AFTER dispatch but BEFORE the model reads it. Same gating
    # shape as F-MUT1 — wire is a no-op without authored rules; lab opts in
    # so dogfood turns can exercise the wizard's second mutator kind
    # end-to-end. Strict default-OFF in the registry so a fresh install /
    # hosted serve stays byte-identical.
    "MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED",
    # PR-F-EXEC1 shell_command action kind: operator-authored subprocess
    # hooks at 11 lifecycle slots. Wires are no-ops without authored rules;
    # lab opts in so dogfood turns can exercise the wizard's new action
    # kind + per-turn budget cap (default 5 spawns / turn shared across
    # all slots) end-to-end. Strict default-OFF in the registry so a fresh
    # install / hosted serve stays byte-identical (hosted activation is
    # explicitly deferred to v2 with a separate admin-tier flag).
    "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED",
    # PR-F-EXEC2 shell_check condition kind: operator-authored subprocess
    # verifier (script reads slot context envelope from stdin, emits
    # ``{passed, reason}`` JSON on stdout — exit-code fallback when stdout
    # is not parseable JSON). Reuses the same shell_runner module + the
    # same MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET per-(session, turn) counter
    # as F-EXEC1 so a turn that fires a mix of shell_command + shell_check
    # rules hits one shared cost ceiling. Wire is a no-op without authored
    # rules; lab opts in so dogfood turns can exercise the wizard's
    # new condition kind end-to-end. Strict default-OFF in the registry
    # so a fresh install / hosted serve stay byte-identical (hosted
    # activation is explicitly deferred to v2 alongside F-EXEC1).
    "MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED",
    # PR-F-UX2 (F8 core) runtime-fields endpoint: backs the wizard's chip
    # picker shown above regex / contentMatch / llm_criterion / SHACL inputs.
    # Pure derivation (no I/O, no LLM), registration-time only, fail-open on
    # unknown tuples. Lab opts in so dogfood wizard authoring sees the chips
    # out-of-the-box; registry default stays OFF so a fresh install / hosted
    # serve do not expose the surface until explicitly enabled.
    "MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED",
    # NOTE: MAGI_CUSTOMIZE_VERIFICATION_ENABLED / MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED
    # are NOT here: they are profile-aware default-ON (``_pb``), so they resolve ON
    # in every non-safe profile (full AND lab) on their own. The lab seed only
    # forces the strict-truthy ``_b`` experimental flags whose registry default is
    # OFF.
    # Customize NL → rule compiler endpoint. Registration-time only (never on
    # the hot path) and fail-open when no provider key is configured, so safe to
    # opt in for lab. Without this the dashboard "Compile" button returns
    # ``nl-rule compiler disabled`` even though the user already has the UI.
    "MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED",
    # NOTE: MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED is intentionally NOT here:
    # it is a profile-aware default-ON (`_pb`) capability, so it does not need
    # the lab opt-in mechanism (it is already ON in the full profile).
    # PR-F-UX6 interview-driven NL authoring + hybrid primitive proposals.
    # Registration-time only (never on the hot path) and fail-open when no
    # provider key is configured, so safe to dogfood in lab. Without this the
    # compose surface still works in the legacy one-shot mode — interview-mode
    # only activates when the flag is ON AND the input is underspecified.
    # Registry default stays OFF so a fresh install / hosted serve preserve the
    # legacy compile path until the operator explicitly opts in.
    "MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED",
    # Customize seam-spec endpoint. Registration-time / dashboard-authoring path
    # only, never on the live turn hot path, and fail-open when prerequisites are
    # missing — safe to dogfood in lab. Registry default stays OFF so a fresh
    # install and hosted serve do not expose the seam-spec surface until the
    # operator explicitly opts in.
    "MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED",
    "MAGI_DASHBOARD_PACK_AUTHORING_ENABLED",
    "MAGI_DEEP_WEB_RESEARCH_ENABLED",
    "MAGI_EGRESS_GATE_ENABLED",
    # (MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED / MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED
    # were promoted to profile-aware default-ON flags — they now default ON under
    # the lab profile via the profile resolver, so they no longer belong in the
    # strict-bool lab-opt-in list.)
    # WS6 PR6b: convert the hard fact-grounding/contract pre-final refuse into an
    # appended hedge notice. Lab-only activation; registry default stays OFF.
    # PAIRED with MAGI_FACT_GROUNDING_VERIFICATION_ENABLED below (see the pairing
    # guard apply_evidence_hedge_pairing_guard) so the verification flag is never
    # in effect without this hedge flag, i.e. lab hedges instead of hard-refusing.
    "MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED",
    "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
    # WS6 PR6b: enable the otherwise-disabled live-finalizer FinalOutputGate for
    # opted-in recipes on the engine path (gate evaluates live, not "skipped").
    # Lab-only activation; registry default stays OFF.
    "MAGI_FINAL_OUTPUT_GATE_LOCAL_ENABLED",
    "MAGI_FILE_DELIVERY_LIVE_ENABLED",
    "MAGI_FORMAT_ADHERENCE_ENABLED",
    "MAGI_GATE5B_GOVERNANCE_ENABLED",
    "MAGI_GA_DELIVERABLE_GATE_ENABLED",
    # (MAGI_GOAL_LOOP_ENABLED was promoted to a profile-aware default-ON flag:
    # the ledger-first auto-continue authority is ambient for every turn in the
    # lab / full profile via the profile resolver, so it no longer belongs in
    # this strict-bool lab-opt-in list.)
    "MAGI_KERNEL_RECIPE_PACKS_ENABLED",
    "MAGI_KERNEL_ROLE_PROVIDES_ENABLED",
    "MAGI_LEARNING_ENABLED",
    "MAGI_LEARNING_INJECTION_ENABLED",
    "MAGI_LEARNING_LIVE_ENABLED",
    "MAGI_LEARNING_REFLECTION_ENABLED",
    "MAGI_MEMORY_COMPACTION_ENABLED",
    "MAGI_MEMORY_ENABLED",
    "MAGI_MEMORY_MODE_ROUTING_ENABLED",
    "MAGI_MEMORY_PROJECTION_ENABLED",
    "MAGI_MEMORY_QMD_LIVE_ENABLED",
    "MAGI_MEMORY_RECALL_ENABLED",
    "MAGI_MEMORY_WRITE_ENABLED",
    "MAGI_OBSERVABILITY_ENABLED",
    "MAGI_RECIPE_ROUTING_LLM_ENABLED",
    "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED",
    # WS6 PR6a: soft research-governance notice (promote the hard research
    # pre-final refuse to an appended could-not-verify notice). Lab-only
    # activation; registry default stays OFF so a fresh install / hosted serve
    # keeps the existing hard-refuse behavior. Paired with the already-lab-ON
    # MAGI_FACT_GROUNDING_VERIFICATION_ENABLED so lab hedges instead of refusing.
    "MAGI_RESEARCH_GOVERNANCE_SOFT_BLOCK_ENABLED",
    "MAGI_SERVE_EVIDENCE_ENABLED",
    "MAGI_SESSION_TRANSCRIPT_ENABLED",
    # SHACL rule compiler: turns customize raw.ttl / natural-language rules into
    # SHACL shapes at authoring time (reviewer + human-approval gated). Pure
    # compile-time path, no hot-turn impact, fail-open when pySHACL is absent.
    # Registry default unchanged so fresh install / hosted serve stays OFF until
    # the operator opts in.
    "MAGI_SHACL_COMPILER_ENABLED",
    # SHACL pre-final determinism verifier: validates evidence triples against
    # compiled shapes during the deterministic producer pass. Default-OFF in
    # registry so a fresh install / hosted serve never executes it; lab opts in
    # so dogfood surfaces shape-violation regressions early. Fail-open if pySHACL
    # is missing so the runtime degrades to the existing behavior.
    "MAGI_SHACL_VERIFIER_ENABLED",
    "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
    "MAGI_WORKER_ROUTING_LLM_ENABLED",
)

# Lab seed for the few experimental flags that are NOT plain "1" booleans.
# MAGI_DOCUMENT_AUTHORING_COVERAGE is a 3-mode flag (off/advisory/block); lab
# enables the non-blocking ``advisory`` tier (turning on the document-coverage
# verification config without hard-blocking turns).
LAB_EXPERIMENTAL_MODE_FLAGS: Mapping[str, str] = {
    "MAGI_DOCUMENT_AUTHORING_COVERAGE": "advisory",
}

LAB_RUNTIME_ENV_DEFAULTS: Mapping[str, str] = {
    "MAGI_RUNTIME_PROFILE": "lab",
    **LAB_EXPERIMENTAL_MODE_FLAGS,
    **{name: "1" for name in LAB_EXPERIMENTAL_FLAGS},
}


def apply_lab_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Apply the lab (experimental) profile (``MAGI_RUNTIME_PROFILE=lab``).

    ``lab`` is ``local-full`` plus the full experimental flat-flag set: it first
    layers the local-full overlay (so every profile-aware default-ON seam, the
    keyless web path, child-runner, etc. are seeded exactly as ``full``) and
    then ``setdefault``s the remaining strict-truthy experimental ``_b`` flags
    ON. ``setdefault`` semantics mean explicit operator env always wins, so a
    per-flag ``MAGI_X=0`` walks any feature back. Registry defaults in
    ``config/flags.py`` are NOT changed — a fresh install and the
    safe/eval/minimal/conservative profiles stay conservative.
    """

    # Stamp the profile identity FIRST so the local-full overlay's own
    # ``setdefault("MAGI_RUNTIME_PROFILE", "full")`` no-ops and the env reports
    # ``lab`` rather than ``full``. ``lab`` is not a safe profile, so the
    # local-full overlay still activates (it gates on
    # ``local_full_runtime_defaults_enabled``, which only excludes the safe
    # profiles) and seeds the profile_bool seams + LOCAL_FULL flat flags. An
    # explicit operator profile still wins via setdefault.
    environ.setdefault("MAGI_RUNTIME_PROFILE", "lab")
    # Reuse the local-full overlay verbatim so ``lab`` never drifts from
    # ``full`` for the shared seams.
    apply_local_full_runtime_defaults(environ)
    # Fail-safe defense in depth: only seed the experimental flat flags when the
    # runtime defaults are enabled (same predicate as the local-full overlay).
    # In normal dispatch this applier is reached only when MAGI_RUNTIME_PROFILE
    # is ``lab`` (non-safe), but if an operator has explicitly pinned a safe
    # profile or MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS=0 we must NOT widen the
    # experimental surface.
    if not local_full_runtime_defaults_enabled(environ):
        return
    for key, value in LAB_RUNTIME_ENV_DEFAULTS.items():
        environ.setdefault(key, value)
    # WS6 PR6b pairing invariant (lab-scoped): the fact-grounding verification
    # flag is already seeded "1" above (it is in LAB_EXPERIMENTAL_FLAGS), and
    # without the hedge flag the existing hard pre_final_evidence_gate_blocked
    # refuse is user-visible. Pair them so lab hedges instead of refusing. This
    # also catches an operator who hand-set the verification flag without the
    # hedge flag (the standalone misconfig). setdefault semantics: an explicit
    # MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED=0 still wins.
    apply_evidence_hedge_pairing_guard(environ)


def apply_evidence_hedge_pairing_guard(environ: MutableMapping[str, str]) -> None:
    """Pair the fact-grounding verification flag with the WS6 hedge flag.

    Design: WS6 deterministic-verification activation, PR6b (pairing invariant).
    ``MAGI_FACT_GROUNDING_VERIFICATION_ENABLED`` leaves ``fact_grounding``
    unsatisfied on a guess so the pre-final gate blocks; without
    ``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED`` that block surfaces as the hard
    ``pre_final_evidence_gate_blocked`` refuse (the wrong tone). This guard
    ensures the hedge flag is in effect whenever the verification flag is, by
    ``setdefault``-seeding the hedge flag to ``"1"`` when the verification flag
    is truthy and the hedge flag has not been set. An explicit operator
    ``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED=0`` still wins (the key is present),
    so this never overrides a deliberate opt-out. It never activates anything
    when the verification flag is off (a fresh install / hosted serve is
    untouched).
    """
    from magi_agent.config.env import (
        parse_fact_grounding_verification_enabled,
    )

    if (
        parse_fact_grounding_verification_enabled(environ)
        and "MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED" not in environ
    ):
        environ["MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED"] = "1"


def apply_local_eval_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Apply the one-shot eval profile (MAGI_RUNTIME_PROFILE=eval). setdefault
    semantics: explicit operator env always wins."""
    for key, value in EVAL_RUNTIME_ENV_DEFAULTS.items():
        environ.setdefault(key, value)


def apply_local_full_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Apply the default installed/local profile to a mutable env mapping.

    The underlying feature gates can remain conservative for import-time tests
    and custom deployments. A clean local ``magi`` or ``magi-agent serve``
    install should start the full local runtime unless the operator explicitly
    opts out with ``MAGI_RUNTIME_PROFILE=safe|minimal|off|conservative|eval`` or
    ``MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS=0``.
    """

    if not local_full_runtime_defaults_enabled(environ):
        return
    for key, value in LOCAL_FULL_RUNTIME_ENV_DEFAULTS.items():
        environ.setdefault(key, value)

    # Keyless web acquisition for the local overlay: jina-reader is keyless
    # (optional key only raises rate limits) and insane-fetch runs locally via
    # curl_cffi, so a fresh user gets a working WebFetch path with zero keys
    # (WebSearch keyless = the browser tool). The provider/router gate constants
    # live in research_tools; reference them by name so the legacy-name naming
    # ratchet is not tripped by duplicated string literals. Lazy-imported to keep
    # this module import-light.
    from magi_agent.web_acquisition.research_tools import (  # noqa: PLC0415
        INSANE_FETCH_ENABLED_ENV,
        JINA_READER_ENABLED_ENV,
        LIVE_WEB_ACQUISITION_ENABLED_ENV,
        PROVIDER_ROUTER_ENABLED_ENV,
    )

    for key in (
        LIVE_WEB_ACQUISITION_ENABLED_ENV,
        PROVIDER_ROUTER_ENABLED_ENV,
        JINA_READER_ENABLED_ENV,
        INSANE_FETCH_ENABLED_ENV,
    ):
        environ.setdefault(key, "1")


def local_full_runtime_defaults_enabled(environ: Mapping[str, str]) -> bool:
    raw = environ.get(LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV)
    if raw is not None and not _env_enabled(raw):
        return False
    # I-1: route the runtime-profile read through the typed flag
    # registry. ``MAGI_RUNTIME_PROFILE`` is already registered
    # (``str``, default ``""``); ``flag_str`` returns ``""`` on
    # unset, which collapses identically under ``(value or "")``.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    profile = (flag_str("MAGI_RUNTIME_PROFILE", env=environ) or "").strip().lower()
    return profile not in SAFE_RUNTIME_PROFILES


def _env_enabled(value: str | None) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf.
    from magi_agent.config._truthy import is_true  # noqa: PLC0415

    return value is not None and is_true(value)
