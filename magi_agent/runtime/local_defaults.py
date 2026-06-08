from __future__ import annotations

from collections.abc import Mapping, MutableMapping

SAFE_RUNTIME_PROFILES = frozenset({"safe", "off", "minimal", "conservative"})
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
    "MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED": "1",
    "MAGI_DEFERRED_TOOLS_ENABLED": "1",
    "MAGI_CHANNEL_WORKFLOWS_ENABLED": "1",
    "MAGI_WORKFLOW_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_SHADOW": "0",
    "MAGI_OBSERVABILITY_ENABLED": "1",
    "MAGI_OBS_HOME": ".openmagi",
    "MAGI_SESSION_PERSISTENCE_ENABLED": "1",
    "MAGI_LEARNING_ENABLED": "true",
    "MAGI_LEARNING_REFLECTION_ENABLED": "1",
    "MAGI_LEARNING_DASHBOARD_ENABLED": "1",
    "MAGI_LEARNING_TELEMETRY_ENABLED": "1",
    "MAGI_LEARNING_INJECTION_ENABLED": "1",
    "MAGI_LEARNING_LIVE_ENABLED": "1",
    "MAGI_SKILL_CURATOR_ENABLED": "1",
    "MAGI_SKILL_CURATOR_SHADOW": "0",
    "MAGI_AUTOPILOT": "1",
}


def apply_local_full_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Apply the default installed/local profile to a mutable env mapping.

    The underlying feature gates can remain conservative for import-time tests
    and custom deployments. A clean local ``magi`` or ``magi-agent serve``
    install should start the full local runtime unless the operator explicitly
    opts out with ``MAGI_RUNTIME_PROFILE=safe|minimal|off|conservative`` or
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
