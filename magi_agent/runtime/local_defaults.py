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
    "MAGI_CHANNEL_WORKFLOWS_ENABLED": "1",
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
}


EVAL_RUNTIME_ENV_DEFAULTS: Mapping[str, str] = {
    # Profile identity
    "MAGI_RUNTIME_PROFILE": "eval",
    "MAGI_TASK_TYPES": "coding",
    # Tool caps
    "MAGI_TOOL_MAX_OUTPUT_BYTES": "131072",
    "MAGI_TOOL_COMMAND_TIMEOUT_MS": "30000",
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


def local_full_runtime_defaults_enabled(environ: Mapping[str, str]) -> bool:
    raw = environ.get(LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV)
    if raw is not None and not _env_enabled(raw):
        return False
    profile = (environ.get("MAGI_RUNTIME_PROFILE") or "").strip().lower()
    return profile not in SAFE_RUNTIME_PROFILES


def _env_enabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}
