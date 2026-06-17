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
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
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
    "MAGI_FILE_TOOLS_ENABLED": "1",
    "MAGI_BROWSER_TOOL_ENABLED": "1",
    "MAGI_SELF_INTROSPECTION_ENABLED": "1",
    "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED": "1",
    "MAGI_MEMORY_WRITE_READINESS_ENABLED": "1",
    "MAGI_MEMORY_WRITE_ENABLED": "1",
    "MAGI_MEMORY_LOCAL_DEV": "1",
    "MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED": "1",
    "MAGI_DEFERRED_TOOLS_ENABLED": "1",
    "MAGI_WORKFLOW_EXECUTOR_ENABLED": "1",
    "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
    "MAGI_CHILD_RUNNER_TOOLSET": "readonly",
    "MAGI_SCHEDULER_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_SHADOW": "0",
    "MAGI_OBSERVABILITY_ENABLED": "1",
    "MAGI_OBS_HOME": ".openmagi",
    "MAGI_SESSION_PERSISTENCE_ENABLED": "1",
    # PR-04-PR1: CLI transcript write path. Registered here but stage-1 OFF
    # ("0") so the disk-write + sanitization behavior can be validated before a
    # later release flips it on.
    "MAGI_CLI_SESSION_LOG_ENABLED": "0",
    # PR-04-PR2: --resume/--continue rehydration. Stage-1 OFF ("0") safety net;
    # depends on the session-log write path above, so a later release stages both
    # on together.
    "MAGI_CLI_RESUME_ENABLED": "0",
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
    "MAGI_CHILD_MEMORY_INHERIT_ENABLED",
    "MAGI_CODE_ACTION_ENABLED",
    "MAGI_CODING_REPAIR_LOOP_ENABLED",
    "MAGI_COMPACTION_ANCHORED_SUMMARY_ENABLED",
    "MAGI_COMPACTION_MANUAL_ENABLED",
    "MAGI_COMPACTION_REAL_TOKENS_ENABLED",
    "MAGI_COMPACTION_SUMMARIZE_ENABLED",
    "MAGI_COMPACTION_TOOL_PRUNE_ENABLED",
    "MAGI_COMPUTE_VIA_CODE_ENABLED",
    "MAGI_CROSS_VERIFY_ENABLED",
    # NOTE: MAGI_CUSTOMIZE_VERIFICATION_ENABLED / MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED
    # are NOT here: they are profile-aware default-ON (``_pb``), so they resolve ON
    # in every non-safe profile (full AND lab) on their own. The lab seed only
    # forces the strict-truthy ``_b`` experimental flags whose registry default is
    # OFF.
    "MAGI_DEEP_WEB_RESEARCH_ENABLED",
    "MAGI_DEFERRED_TOOLS_ENABLED",
    "MAGI_DOCUMENT_QA_ENABLED",
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
    "MAGI_EGRESS_GATE_ENABLED",
    "MAGI_FACTS_REPLAN_ENABLED",
    "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
    "MAGI_FILE_DELIVERY_LIVE_ENABLED",
    "MAGI_FORMAT_ADHERENCE_ENABLED",
    "MAGI_GATE5B_GOVERNANCE_ENABLED",
    "MAGI_GA_DELIVERABLE_GATE_ENABLED",
    "MAGI_GOAL_LOOP_ENABLED",
    "MAGI_HEADTAIL_TRUNCATION_ENABLED",
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
    "MAGI_MULTI_FILE_JOIN_ENABLED",
    "MAGI_OBSERVABILITY_ENABLED",
    "MAGI_PERSISTENT_PYTHON_ENABLED",
    "MAGI_RECIPE_ROUTING_LLM_ENABLED",
    "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED",
    "MAGI_SERVE_EVIDENCE_ENABLED",
    "MAGI_SESSION_TRANSCRIPT_ENABLED",
    "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
    "MAGI_STEP_DECOMPOSITION_ENABLED",
    "MAGI_SUBAGENT_GOVERNED_TURN_ENABLED",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
    "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED",
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


def apply_local_runtime_profile_defaults(environ: MutableMapping[str, str]) -> None:
    """Apply the local overlay chosen by ``MAGI_RUNTIME_PROFILE``.

    The DEFAULT (unset profile) and an explicit ``lab`` both get the experimental
    dogfood tier, so a fresh local ``magi-agent serve`` / ``magi`` is maximally
    capable out of the box (PythonExec, deep web, memory recall, etc.). Opting
    into the conservative overlay with an explicit ``MAGI_RUNTIME_PROFILE=full``
    still works. ``setdefault`` semantics mean explicit operator env always wins,
    so a per-flag ``MAGI_X=0`` walks any feature back. The ``eval`` profile is
    dispatched by callers BEFORE this helper.
    """
    # Honor the opt-out / safe-profile gate up front so neither overlay (and in
    # particular ``apply_lab_runtime_defaults``, which stamps the profile before
    # its own internal gate) leaves a profile stamped when the runtime defaults
    # are disabled. Mirrors the predicate both overlays gate on.
    if not local_full_runtime_defaults_enabled(environ):
        return
    profile = (environ.get("MAGI_RUNTIME_PROFILE") or "").strip().lower()
    if profile == "full":
        apply_local_full_runtime_defaults(environ)
    else:
        apply_lab_runtime_defaults(environ)


def local_full_runtime_defaults_enabled(environ: Mapping[str, str]) -> bool:
    raw = environ.get(LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV)
    if raw is not None and not _env_enabled(raw):
        return False
    profile = (environ.get("MAGI_RUNTIME_PROFILE") or "").strip().lower()
    return profile not in SAFE_RUNTIME_PROFILES


def _env_enabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}
