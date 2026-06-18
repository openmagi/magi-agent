from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING
from types import SimpleNamespace

from pydantic import ValidationError

from .models import (
    BuildInfo,
    PythonContextContinuityConfig,
    PythonGate2ReadinessConfig,
    PythonGate3ReadinessConfig,
    PythonGate4ReadinessConfig,
    PythonGate5ReadinessConfig,
    PythonGate7ReadinessConfig,
    PythonGate8ReadinessConfig,
    PythonMemoryAdapterConfig,
    PythonRuntimeAuthorityConfig,
    PythonSecurityPostureConfig,
    PythonToolHostAttachmentConfig,
    RuntimeConfig,
)

if TYPE_CHECKING:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationBudgets,
        Gate5B4C3ShadowGenerationProviderCredentialBinding,
    )
    from magi_agent.transport.shadow_generations import (
        Gate5B4C3ShadowGenerationRouteConfig,
    )


class RuntimeEnvError(ValueError):
    pass


REQUIRED_ENV = (
    "BOT_ID",
    "USER_ID",
    "GATEWAY_TOKEN",
    "CORE_AGENT_API_PROXY_URL",
    "CORE_AGENT_CHAT_PROXY_URL",
    "CORE_AGENT_REDIS_URL",
    "CORE_AGENT_MODEL",
)

# Placeholder model id used by the no-env local fallback (see
# ``magi_agent.main._parse_runtime_config``). ``CORE_AGENT_MODEL`` is required and
# must be non-empty, so the local fallback injects this sentinel rather than a real
# model id. Surfaces that pick a provider-specific default (the CLI/dashboard
# headless runner) must treat this value as "unset" so the per-provider default
# model applies instead of being clobbered by the placeholder.
LOCAL_DEV_MODEL_SENTINEL = "local-dev"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
RUNTIME_PROFILE_ENV = "MAGI_RUNTIME_PROFILE"
_SAFE_RUNTIME_PROFILES = frozenset({"safe", "off", "minimal", "conservative", "eval"})

# ---------------------------------------------------------------------------
# Coding: edit fuzzy-match flag
# ---------------------------------------------------------------------------
# When set to "1" or "true", gate5b FileEdit uses the 9-stage fuzzy-match
# cascade (magi_agent.coding.edit_matching) instead of exact-only matching.
# Default: ON in the local full runtime profile; set
# MAGI_EDIT_FUZZY_MATCH_ENABLED=0 or MAGI_RUNTIME_PROFILE=safe|eval for
# conservative/profile-scoped runs.
def edit_fuzzy_match_enabled(env: "Mapping[str, str] | None" = None) -> bool:
    """Call-time read of ``MAGI_EDIT_FUZZY_MATCH_ENABLED``.

    The legacy module-level constant below froze at import time — BEFORE
    ``apply_local_eval_runtime_defaults`` ran in ``cli/app.py`` — so eval runs
    silently lost the fuzzy cascade even though the eval profile sets the env
    to "1". Dispatch-time consumers (gate5b FileEdit) must use this function.
    """
    source = os.environ if env is None else env
    explicit = source.get("MAGI_EDIT_FUZZY_MATCH_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in _TRUE_VALUES
    profile = (source.get(RUNTIME_PROFILE_ENV) or "").strip().lower()
    return profile not in _SAFE_RUNTIME_PROFILES


# Deprecated import-time snapshot; kept for callers that still import the
# constant. New code must call ``edit_fuzzy_match_enabled()``.
MAGI_EDIT_FUZZY_MATCH_ENABLED: bool = edit_fuzzy_match_enabled()

# ---------------------------------------------------------------------------
# Coding: edit-match evidence enforcement flag (PR1)
# ---------------------------------------------------------------------------
# Controls whether low-confidence fuzzy edits block the final answer.
# Valid values (EvidenceEnforcement): "off", "audit", "block_final_answer".
# Default: "off" — receipts are built and emitted but nothing blocks.
# Zero behaviour change vs today (exact same code paths) when set to "off".
MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT: str = (
    os.environ.get("MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT", "off").strip().lower()
)
# Normalise to one of the three valid values; fall back to "off" for unknowns.
if MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT not in {"off", "audit", "block_final_answer"}:
    MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT = "off"

_GATE5B4C3_GOOGLE_CREDENTIAL_ENVS = frozenset(
    {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)
_GATE5B4C3_FIRST_SMOKE_PROVIDER = "google"
_GATE5B4C3_FIRST_SMOKE_MODEL = "gemini-3.5-flash"
_GATE5B4C3_FIRST_SMOKE_CREDENTIAL_REF = "gate5b-google-api-key-smoke-v1"
_GATE5B4C3_FIRST_SMOKE_CREDENTIAL_ENV = "GOOGLE_API_KEY"


@dataclass(frozen=True)
class LspDiagnosticsEnv:
    """Single source for the after-edit LSP diagnostics flag.

    Threaded into gate5b via ``build_gate5b_full_toolhost_config_from_env``.
    The local full runtime profile enables diagnostics by default; explicit
    ``MAGI_LSP_DIAGNOSTICS_ENABLED=0`` or ``MAGI_RUNTIME_PROFILE=safe`` keeps
    the gate5b contract inert.
    """

    enabled: bool = False
    cap: int = 20
    timeout_ms: int = 5000


def parse_lsp_diagnostics_env(env: Mapping[str, str]) -> LspDiagnosticsEnv:
    enabled = _runtime_feature_enabled(env, "MAGI_LSP_DIAGNOSTICS_ENABLED")
    if not enabled:
        return LspDiagnosticsEnv()
    cap = _int_env(env, "MAGI_LSP_DIAGNOSTICS_CAP", 20)
    if cap < 1 or cap > 100:
        raise RuntimeEnvError("MAGI_LSP_DIAGNOSTICS_CAP must be between 1 and 100")
    timeout_ms = _int_env(env, "MAGI_LSP_DIAGNOSTICS_TIMEOUT_MS", 5000)
    if timeout_ms < 250 or timeout_ms > 30000:
        raise RuntimeEnvError(
            "MAGI_LSP_DIAGNOSTICS_TIMEOUT_MS must be between 250 and 30000"
        )
    return LspDiagnosticsEnv(enabled=True, cap=cap, timeout_ms=timeout_ms)


@dataclass(frozen=True)
class Gate3ARecordedReplayEnv:
    enabled: bool = False
    input_dir: Path | None = None
    output_dir: Path | None = None
    allow_model_calls: bool = False
    max_bundles: int = 1


# Single source of truth for the edit-failure reflection/retry wiring flags.
# PR2: when enabled, a FileEdit tool failure (e.g. ValueError("old_text_not_found"))
# re-injects an OpenCode-style corrective hidden message into the next model turn
# via the live ADK Runner plugin boundary, fail-closed at MAX_ATTEMPTS.
EDIT_RETRY_REFLECTION_ENABLED_ENV = "MAGI_EDIT_RETRY_REFLECTION_ENABLED"
EDIT_RETRY_MAX_ATTEMPTS_ENV = "MAGI_EDIT_RETRY_MAX_ATTEMPTS"
_EDIT_RETRY_MAX_ATTEMPTS_DEFAULT = 2


@dataclass(frozen=True)
class EditRetryReflectionEnv:
    enabled: bool = False
    max_attempts: int = _EDIT_RETRY_MAX_ATTEMPTS_DEFAULT


def parse_edit_retry_reflection_env(
    env: Mapping[str, str],
) -> EditRetryReflectionEnv:
    enabled = _runtime_feature_enabled(
        env,
        EDIT_RETRY_REFLECTION_ENABLED_ENV,
    )
    max_attempts = _int_env(
        env,
        EDIT_RETRY_MAX_ATTEMPTS_ENV,
        _EDIT_RETRY_MAX_ATTEMPTS_DEFAULT,
    )
    if max_attempts < 1:
        raise RuntimeEnvError(
            f"{EDIT_RETRY_MAX_ATTEMPTS_ENV} must be >= 1"
        )
    return EditRetryReflectionEnv(enabled=enabled, max_attempts=max_attempts)


# Single source of truth for the generic tool-exception reflection flags
# (hermes mechanism 1, raise path). When enabled, a raising tool (any tool
# except FileEdit/PatchApply, which keep their specialized edit-retry handler)
# is converted into a model-visible corrective tool_result with retry guidance
# and a per-invocation attempt budget instead of killing the whole turn.
#
# Deliberately a STRICT default-OFF truthy parse (NOT _runtime_feature_enabled,
# which defaults ON under the unset/full profile): the flag is profile-
# independent so eval-profile benchmark runs can opt in explicitly.
TOOL_EXCEPTION_REFLECTION_ENABLED_ENV = "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED"
TOOL_EXCEPTION_MAX_ATTEMPTS_ENV = "MAGI_TOOL_EXCEPTION_MAX_ATTEMPTS"
_TOOL_EXCEPTION_MAX_ATTEMPTS_DEFAULT = 2


@dataclass(frozen=True)
class ToolExceptionReflectionEnv:
    enabled: bool = False
    max_attempts: int = _TOOL_EXCEPTION_MAX_ATTEMPTS_DEFAULT


def parse_tool_exception_reflection_env(
    env: Mapping[str, str],
) -> ToolExceptionReflectionEnv:
    enabled = _is_true(env.get(TOOL_EXCEPTION_REFLECTION_ENABLED_ENV))
    max_attempts = _int_env(
        env,
        TOOL_EXCEPTION_MAX_ATTEMPTS_ENV,
        _TOOL_EXCEPTION_MAX_ATTEMPTS_DEFAULT,
    )
    if max_attempts < 1:
        raise RuntimeEnvError(
            f"{TOOL_EXCEPTION_MAX_ATTEMPTS_ENV} must be >= 1"
        )
    return ToolExceptionReflectionEnv(enabled=enabled, max_attempts=max_attempts)


# Single source of truth for the schema-invalid argument feedback flags
# (hermes mechanism 1, returned-result path / R3). When enabled, a dispatcher
# result with errorCode == "tool_input_schema_invalid" is enriched at the
# control-plane on_after_tool seam with plain-text missing/unknown argument
# NAMES (recomputed locally from the tool's declaration — schema vocabulary
# the model already sees; argument VALUES are never surfaced) plus hermes-style
# retry guidance, under a per-invocation attempt budget. The redaction layer
# in magi_agent.tools.schema_validation is untouched.
#
# Deliberately a STRICT default-OFF truthy parse (NOT _runtime_feature_enabled,
# which defaults ON under the unset/full profile): the flag is profile-
# independent so eval-profile benchmark runs can opt in explicitly.
TOOL_SCHEMA_FEEDBACK_ENABLED_ENV = "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED"
TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_ENV = "MAGI_TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS"
_TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_DEFAULT = 2


@dataclass(frozen=True)
class ToolSchemaFeedbackEnv:
    enabled: bool = False
    max_attempts: int = _TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_DEFAULT


def parse_tool_schema_feedback_env(
    env: Mapping[str, str],
) -> ToolSchemaFeedbackEnv:
    enabled = _is_true(env.get(TOOL_SCHEMA_FEEDBACK_ENABLED_ENV))
    max_attempts = _int_env(
        env,
        TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_ENV,
        _TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_DEFAULT,
    )
    if max_attempts < 1:
        raise RuntimeEnvError(
            f"{TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS_ENV} must be >= 1"
        )
    return ToolSchemaFeedbackEnv(enabled=enabled, max_attempts=max_attempts)


# Single source of truth for the PR12 loop-guard wiring flags.
# When enabled, the live ADK Runner attaches a MagiResiliencePlugin whose
# after_tool_callback drives the existing ToolCallLoopDetector: N identical
# consecutive tool calls -> soft nudge (model warned, tool result preserved);
# a higher threshold -> hard stop (the tool result is replaced with a stop
# directive so the model does not keep looping). Enabled by default in the local
# full runtime profile; set MAGI_LOOP_GUARD_ENABLED=0 or MAGI_RUNTIME_PROFILE=safe
# for conservative runs.
LOOP_GUARD_ENABLED_ENV = "MAGI_LOOP_GUARD_ENABLED"
LOOP_GUARD_SOFT_THRESHOLD_ENV = "MAGI_LOOP_GUARD_SOFT_THRESHOLD"
LOOP_GUARD_HARD_THRESHOLD_ENV = "MAGI_LOOP_GUARD_HARD_THRESHOLD"
LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD_ENV = "MAGI_LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD"
LOOP_GUARD_FREQUENCY_HARD_THRESHOLD_ENV = "MAGI_LOOP_GUARD_FREQUENCY_HARD_THRESHOLD"
_LOOP_GUARD_SOFT_DEFAULT = 3
_LOOP_GUARD_HARD_DEFAULT = 5
_LOOP_GUARD_FREQ_SOFT_DEFAULT = 15
_LOOP_GUARD_FREQ_HARD_DEFAULT = 30


@dataclass(frozen=True)
class LoopGuardEnv:
    enabled: bool = False
    soft_threshold: int = _LOOP_GUARD_SOFT_DEFAULT
    hard_threshold: int = _LOOP_GUARD_HARD_DEFAULT
    frequency_soft_threshold: int = _LOOP_GUARD_FREQ_SOFT_DEFAULT
    frequency_hard_threshold: int = _LOOP_GUARD_FREQ_HARD_DEFAULT


def parse_loop_guard_env(env: Mapping[str, str]) -> LoopGuardEnv:
    enabled = _runtime_feature_enabled(env, LOOP_GUARD_ENABLED_ENV)
    if not enabled:
        return LoopGuardEnv()
    soft = _int_env(env, LOOP_GUARD_SOFT_THRESHOLD_ENV, _LOOP_GUARD_SOFT_DEFAULT)
    hard = _int_env(env, LOOP_GUARD_HARD_THRESHOLD_ENV, _LOOP_GUARD_HARD_DEFAULT)
    freq_soft = _int_env(
        env, LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD_ENV, _LOOP_GUARD_FREQ_SOFT_DEFAULT
    )
    freq_hard = _int_env(
        env, LOOP_GUARD_FREQUENCY_HARD_THRESHOLD_ENV, _LOOP_GUARD_FREQ_HARD_DEFAULT
    )
    if soft < 1:
        raise RuntimeEnvError(f"{LOOP_GUARD_SOFT_THRESHOLD_ENV} must be >= 1")
    if hard < soft:
        raise RuntimeEnvError(
            f"{LOOP_GUARD_HARD_THRESHOLD_ENV} must be >= {LOOP_GUARD_SOFT_THRESHOLD_ENV}"
        )
    if freq_soft < 1:
        raise RuntimeEnvError(
            f"{LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD_ENV} must be >= 1"
        )
    if freq_hard < freq_soft:
        raise RuntimeEnvError(
            f"{LOOP_GUARD_FREQUENCY_HARD_THRESHOLD_ENV} must be >= "
            f"{LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD_ENV}"
        )
    return LoopGuardEnv(
        enabled=True,
        soft_threshold=soft,
        hard_threshold=hard,
        frequency_soft_threshold=freq_soft,
        frequency_hard_threshold=freq_hard,
    )


# Single source of truth for the PR12 error-recovery wiring flags. Reuses the
# existing ``MAGI_ERROR_RECOVERY_ENABLED`` / ``MAGI_MAX_RECOVERY_ATTEMPTS`` names
# (also consumed by ErrorRecoveryConfig.from_env). When enabled, the live ADK
# Runner attaches the MagiResiliencePlugin whose on_model_error_callback runs the
# existing RecoveryEngine: classify the model error and apply the first
# applicable strategy (RateLimit honors Retry-After). Enabled by default in the
# local full runtime profile; set MAGI_ERROR_RECOVERY_ENABLED=0 or
# MAGI_RUNTIME_PROFILE=safe for conservative runs.
ERROR_RECOVERY_ENABLED_ENV = "MAGI_ERROR_RECOVERY_ENABLED"
MAX_RECOVERY_ATTEMPTS_ENV = "MAGI_MAX_RECOVERY_ATTEMPTS"


@dataclass(frozen=True)
class ErrorRecoveryEnv:
    enabled: bool = False
    max_recovery_attempts: int = 3


def parse_error_recovery_env(env: Mapping[str, str]) -> ErrorRecoveryEnv:
    enabled = _runtime_feature_enabled(env, ERROR_RECOVERY_ENABLED_ENV)
    max_attempts = _int_env(env, MAX_RECOVERY_ATTEMPTS_ENV, 3)
    if max_attempts < 1:
        raise RuntimeEnvError(f"{MAX_RECOVERY_ATTEMPTS_ENV} must be >= 1")
    return ErrorRecoveryEnv(enabled=enabled, max_recovery_attempts=max_attempts)


# Output-continuation wiring. A single model response is capped at the model's
# per-response output-token limit; when a long deliverable hits that cap the
# answer is truncated mid-sentence (finish_reason length/max_tokens). When
# enabled, the live run seam re-invokes the model with a "continue where you
# left off" message and appends, up to MAGI_MAX_OUTPUT_CONTINUATIONS times.
# Enabled by default outside the safe runtime profile (like error recovery);
# set MAGI_OUTPUT_CONTINUATION_ENABLED=0 or MAGI_RUNTIME_PROFILE=safe to disable.
OUTPUT_CONTINUATION_ENABLED_ENV = "MAGI_OUTPUT_CONTINUATION_ENABLED"
MAX_OUTPUT_CONTINUATIONS_ENV = "MAGI_MAX_OUTPUT_CONTINUATIONS"


@dataclass(frozen=True)
class OutputContinuationEnv:
    enabled: bool = False
    max_continuations: int = 4


def parse_output_continuation_env(env: Mapping[str, str]) -> OutputContinuationEnv:
    enabled = _runtime_feature_enabled(env, OUTPUT_CONTINUATION_ENABLED_ENV)
    max_continuations = _int_env(env, MAX_OUTPUT_CONTINUATIONS_ENV, 4)
    if max_continuations < 1:
        raise RuntimeEnvError(f"{MAX_OUTPUT_CONTINUATIONS_ENV} must be >= 1")
    return OutputContinuationEnv(
        enabled=enabled, max_continuations=max_continuations
    )


# Empty-response recovery wiring (R2, hermes mechanism 3). One flag covers two
# behaviors that together mean "never end a turn with nothing": (a) a bounded
# corrective re-invocation when tools ran but the model returned no text, and
# (b) one grace re-invocation ("produce your final answer now") after the
# per-turn event budget is exhausted. Default OFF with a STRICT truthy opt-in
# ("1"/"true"/"yes"/"on") — deliberately NOT the runtime-profile default-ON
# convention, because the corrective messages persist in session history.
EMPTY_RESPONSE_RECOVERY_ENABLED_ENV = "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED"
EMPTY_RESPONSE_MAX_RECOVERIES_ENV = "MAGI_EMPTY_RESPONSE_MAX_RECOVERIES"


@dataclass(frozen=True)
class EmptyResponseRecoveryEnv:
    enabled: bool = False
    max_recoveries: int = 1


def parse_empty_response_recovery_env(
    env: Mapping[str, str],
) -> EmptyResponseRecoveryEnv:
    enabled = _is_true(env.get(EMPTY_RESPONSE_RECOVERY_ENABLED_ENV))
    max_recoveries = _int_env(env, EMPTY_RESPONSE_MAX_RECOVERIES_ENV, 1)
    if max_recoveries < 1:
        raise RuntimeEnvError(f"{EMPTY_RESPONSE_MAX_RECOVERIES_ENV} must be >= 1")
    return EmptyResponseRecoveryEnv(enabled=enabled, max_recoveries=max_recoveries)


# Single source of truth for the live context-compaction activation flags.
# PR13: when enabled, an ADK ``before_model_callback`` plugin reduces the
# outgoing ``llm_request.contents`` to the recent tail (reusing
# ``ContextLifecycleBoundary.compact_if_needed`` as the threshold/tail decision
# engine) once the estimated context exceeds budget. Enabled by default in the
# local full runtime profile; set MAGI_CONTEXT_COMPACTION_ENABLED=0 or
# MAGI_RUNTIME_PROFILE=safe for conservative runs.
CONTEXT_COMPACTION_ENABLED_ENV = "MAGI_CONTEXT_COMPACTION_ENABLED"
COMPACTION_TOKEN_THRESHOLD_ENV = "MAGI_COMPACTION_TOKEN_THRESHOLD"
COMPACTION_TAIL_EVENTS_ENV = "MAGI_COMPACTION_TAIL_EVENTS"
_COMPACTION_TOKEN_THRESHOLD_DEFAULT = 24_000
_COMPACTION_TAIL_EVENTS_DEFAULT = 16

# G2: real-token accounting. Strict default-OFF master switch plus the
# %-of-window threshold knobs. When OFF the parser returns the same
# enabled/token_threshold/tail_events triple as before with the additive fields
# at their conservative defaults, so the compaction decision is byte-identical.
COMPACTION_REAL_TOKENS_ENABLED_ENV = "MAGI_COMPACTION_REAL_TOKENS_ENABLED"
COMPACTION_REAL_TOKENS_PCT_ENV = "MAGI_COMPACTION_REAL_TOKENS_PCT"
COMPACTION_OUTPUT_RESERVE_ENV = "MAGI_COMPACTION_OUTPUT_RESERVE"
_COMPACTION_REAL_TOKENS_PCT_DEFAULT = 0.75
_COMPACTION_OUTPUT_RESERVE_DEFAULT = 8_000

# G4: deterministic tool-output prune pre-tier. Strict default-OFF master switch
# plus two int knobs. When OFF the parser returns the same triple as before with
# the additive prune fields at their defaults, so compaction is byte-identical.
COMPACTION_TOOL_PRUNE_ENABLED_ENV = "MAGI_COMPACTION_TOOL_PRUNE_ENABLED"
COMPACTION_PRUNE_PROTECT_ENV = "MAGI_COMPACTION_PRUNE_PROTECT"
COMPACTION_PRUNE_MINIMUM_ENV = "MAGI_COMPACTION_PRUNE_MINIMUM"
_COMPACTION_PRUNE_PROTECT_DEFAULT = 40_000
_COMPACTION_PRUNE_MINIMUM_DEFAULT = 20_000

# G1: LLM summary injection on the tail-drop. Strict default-OFF master switch
# plus an optional session-model override and a summarize timeout. When OFF the
# parser returns the same triple as before with the additive summary fields at
# their defaults, so the tail-drop is byte-identical (no LLM call).
COMPACTION_SUMMARIZE_ENABLED_ENV = "MAGI_COMPACTION_SUMMARIZE_ENABLED"
COMPACTION_SUMMARY_MODEL_ENV = "MAGI_COMPACTION_SUMMARY_MODEL"
COMPACTION_SUMMARY_TIMEOUT_ENV = "MAGI_COMPACTION_SUMMARY_TIMEOUT"
_COMPACTION_SUMMARY_TIMEOUT_DEFAULT = 30.0

# G5/G6: anchored (incremental) summary + consecutive-failure circuit breaker.
# Both strict default-OFF/default-3 (NOT profile-aware). Anchoring is only
# effective when BOTH summarize and anchored are ON (layered in the builder);
# the breaker is folded under summarize (active whenever summarize is ON and the
# max is > 0). OFF / default => byte-identical to Phase-3.
COMPACTION_ANCHORED_SUMMARY_ENABLED_ENV = "MAGI_COMPACTION_ANCHORED_SUMMARY_ENABLED"
COMPACTION_SUMMARY_MAX_FAILURES_ENV = "MAGI_COMPACTION_SUMMARY_MAX_FAILURES"
_COMPACTION_SUMMARY_MAX_FAILURES_DEFAULT = 3

# G7: manual /compact force-compaction. Strict default-OFF (NOT profile-aware).
# When ON the plugin consumes the cross-turn one-shot signal and forces a
# compaction on the next model turn regardless of threshold. Only has effect when
# MAGI_CONTEXT_COMPACTION_ENABLED is ALSO on (the plugin is only attached then).
# OFF => byte-identical to Phase-4.
COMPACTION_MANUAL_ENABLED_ENV = "MAGI_COMPACTION_MANUAL_ENABLED"


@dataclass(frozen=True)
class ContextCompactionEnv:
    enabled: bool = False
    token_threshold: int = _COMPACTION_TOKEN_THRESHOLD_DEFAULT
    tail_events: int = _COMPACTION_TAIL_EVENTS_DEFAULT
    real_tokens_enabled: bool = False
    real_tokens_pct: float = _COMPACTION_REAL_TOKENS_PCT_DEFAULT
    output_reserve: int = _COMPACTION_OUTPUT_RESERVE_DEFAULT
    tool_prune_enabled: bool = False
    prune_protect: int = _COMPACTION_PRUNE_PROTECT_DEFAULT
    prune_minimum: int = _COMPACTION_PRUNE_MINIMUM_DEFAULT
    summarize_enabled: bool = False
    summary_model: str = ""
    summary_timeout: float = _COMPACTION_SUMMARY_TIMEOUT_DEFAULT
    anchored_summary_enabled: bool = False
    summary_max_failures: int = _COMPACTION_SUMMARY_MAX_FAILURES_DEFAULT
    manual_enabled: bool = False


def parse_context_compaction_env(env: Mapping[str, str]) -> ContextCompactionEnv:
    """Single source for the live context-compaction flags."""
    enabled = _runtime_feature_enabled(env, CONTEXT_COMPACTION_ENABLED_ENV)
    token_threshold = _int_env(
        env,
        COMPACTION_TOKEN_THRESHOLD_ENV,
        _COMPACTION_TOKEN_THRESHOLD_DEFAULT,
    )
    if token_threshold < 1:
        raise RuntimeEnvError(f"{COMPACTION_TOKEN_THRESHOLD_ENV} must be >= 1")
    tail_events = _int_env(
        env,
        COMPACTION_TAIL_EVENTS_ENV,
        _COMPACTION_TAIL_EVENTS_DEFAULT,
    )
    if tail_events < 1:
        raise RuntimeEnvError(f"{COMPACTION_TAIL_EVENTS_ENV} must be >= 1")
    # G2: strict default-OFF real-token accounting (NOT profile-aware).
    real_tokens_enabled = _is_true(env.get(COMPACTION_REAL_TOKENS_ENABLED_ENV))
    real_tokens_pct = _float_env(
        env,
        COMPACTION_REAL_TOKENS_PCT_ENV,
        _COMPACTION_REAL_TOKENS_PCT_DEFAULT,
    )
    if not (0.0 < real_tokens_pct <= 1.0):
        raise RuntimeEnvError(
            f"{COMPACTION_REAL_TOKENS_PCT_ENV} must be in the range (0, 1]"
        )
    output_reserve = _int_env(
        env,
        COMPACTION_OUTPUT_RESERVE_ENV,
        _COMPACTION_OUTPUT_RESERVE_DEFAULT,
    )
    if output_reserve < 0:
        raise RuntimeEnvError(f"{COMPACTION_OUTPUT_RESERVE_ENV} must be >= 0")
    # G4: strict default-OFF tool-output prune pre-tier (NOT profile-aware,
    # matching the real-tokens master switch convention above).
    tool_prune_enabled = _is_true(env.get(COMPACTION_TOOL_PRUNE_ENABLED_ENV))
    prune_protect = _int_env(
        env,
        COMPACTION_PRUNE_PROTECT_ENV,
        _COMPACTION_PRUNE_PROTECT_DEFAULT,
    )
    if prune_protect < 1:
        raise RuntimeEnvError(f"{COMPACTION_PRUNE_PROTECT_ENV} must be >= 1")
    prune_minimum = _int_env(
        env,
        COMPACTION_PRUNE_MINIMUM_ENV,
        _COMPACTION_PRUNE_MINIMUM_DEFAULT,
    )
    if prune_minimum < 1:
        raise RuntimeEnvError(f"{COMPACTION_PRUNE_MINIMUM_ENV} must be >= 1")
    # G1: strict default-OFF summary injection (NOT profile-aware, matching the
    # real-tokens / tool-prune master switches above).
    summarize_enabled = _is_true(env.get(COMPACTION_SUMMARIZE_ENABLED_ENV))
    summary_model = _trimmed(env.get(COMPACTION_SUMMARY_MODEL_ENV)) or ""
    summary_timeout = _float_env(
        env,
        COMPACTION_SUMMARY_TIMEOUT_ENV,
        _COMPACTION_SUMMARY_TIMEOUT_DEFAULT,
    )
    if summary_timeout <= 0:
        raise RuntimeEnvError(f"{COMPACTION_SUMMARY_TIMEOUT_ENV} must be > 0")
    # G5/G6: strict default-OFF anchored summary + configurable failure breaker
    # (NOT profile-aware, matching the summarize master switch above).
    anchored_summary_enabled = _is_true(
        env.get(COMPACTION_ANCHORED_SUMMARY_ENABLED_ENV)
    )
    summary_max_failures = _int_env(
        env,
        COMPACTION_SUMMARY_MAX_FAILURES_ENV,
        _COMPACTION_SUMMARY_MAX_FAILURES_DEFAULT,
    )
    if summary_max_failures < 0:
        raise RuntimeEnvError(
            f"{COMPACTION_SUMMARY_MAX_FAILURES_ENV} must be >= 0"
        )
    # G7: strict default-OFF manual /compact force-compaction (NOT profile-aware,
    # matching the summarize / real-tokens / tool-prune master switches above).
    manual_enabled = _is_true(env.get(COMPACTION_MANUAL_ENABLED_ENV))
    return ContextCompactionEnv(
        enabled=enabled,
        token_threshold=token_threshold,
        tail_events=tail_events,
        real_tokens_enabled=real_tokens_enabled,
        real_tokens_pct=real_tokens_pct,
        output_reserve=output_reserve,
        tool_prune_enabled=tool_prune_enabled,
        prune_protect=prune_protect,
        prune_minimum=prune_minimum,
        summarize_enabled=summarize_enabled,
        summary_model=summary_model,
        summary_timeout=summary_timeout,
        anchored_summary_enabled=anchored_summary_enabled,
        summary_max_failures=summary_max_failures,
        manual_enabled=manual_enabled,
    )


def parse_runtime_env(env: Mapping[str, str]) -> RuntimeConfig:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise RuntimeEnvError(f"Missing required runtime env: {', '.join(missing)}")

    build = BuildInfo(
        version=env.get("CORE_AGENT_VERSION") or "0.1.0-adk-scaffold",
        build_sha=_first_non_empty(
            env,
            "CORE_AGENT_BUILT_BUILD_SHA",
            "CORE_AGENT_BUILD_SHA",
            "VERCEL_GIT_COMMIT_SHA",
        ),
        image_repo=_first_non_empty(env, "CORE_AGENT_BUILT_IMAGE_REPO", "CORE_AGENT_IMAGE_REPO"),
        image_tag=_first_non_empty(env, "CORE_AGENT_BUILT_IMAGE_TAG", "CORE_AGENT_IMAGE_TAG"),
        image_digest=_first_non_empty(
            env,
            "CORE_AGENT_BUILT_IMAGE_DIGEST",
            "CORE_AGENT_EXPECTED_IMAGE_DIGEST",
            "CORE_AGENT_IMAGE_DIGEST",
        ),
    )
    return RuntimeConfig(
        bot_id=env["BOT_ID"],
        user_id=env["USER_ID"],
        gateway_token=env["GATEWAY_TOKEN"],
        api_proxy_url=env["CORE_AGENT_API_PROXY_URL"],
        chat_proxy_url=env["CORE_AGENT_CHAT_PROXY_URL"],
        redis_url=env["CORE_AGENT_REDIS_URL"],
        model=env["CORE_AGENT_MODEL"],
        build=build,
        memory=parse_python_memory_adapter_env(env),
        toolhost=parse_python_toolhost_attachment_env(env),
        security_posture=parse_python_security_posture_env(env),
        context_continuity=parse_python_context_continuity_env(env),
        gate2_readiness=parse_python_gate2_readiness_env(env),
        gate3_readiness=parse_python_gate3_readiness_env(env),
        gate4_readiness=parse_python_gate4_readiness_env(env),
        gate5_readiness=parse_python_gate5_readiness_env(env),
        gate7_readiness=parse_python_gate7_readiness_env(env),
        gate8_readiness=parse_python_gate8_readiness_env(env),
        authority=parse_python_runtime_authority_env(env),
    )


#: Operator's per-deployment vetted model-route allowlist (``provider:model``
#: CSV). Authoritative single source of truth for which routes a deployment
#: permits; consumed by the gate5b shadow-generation parser AND
#: :func:`operator_allowed_model_routes` (child route validation).
_ALLOWED_MODEL_ROUTES_ENV = (
    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES"
)


def gate5b_live_subagents_flag_on(env: Mapping[str, str]) -> bool:
    """True iff the serve-path live-sub-agents flag is set.

    The serve flag ONLY; callers AND it with the live child-runner master gate to
    reconstruct ``transport.live_subagents_serve_enabled``. Lives here (the config
    flag-read allowlist) so a consumer above the transport layer can gate on the
    flag without an inline env read.
    """
    return _is_true(env.get("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED"))


def operator_allowed_model_routes(
    env: Mapping[str, str],
) -> frozenset[tuple[str, str]]:
    """Fail-soft ``(provider, model)`` allowlist from the operator route env.

    Returns casefolded ``(provider, model)`` pairs the operator explicitly vetted
    for this deployment, or an empty set when unset/malformed. Used so a child
    spawn can route to an operator-approved model that is not (yet) in the
    built-in ``ModelTierRegistry`` — making the deployment env the single source
    of truth and removing the registry/env drift. Never raises.
    """
    try:
        raw = _trimmed(env.get(_ALLOWED_MODEL_ROUTES_ENV)) or ""
        routes: set[tuple[str, str]] = set()
        for route in _csv_values(raw):
            if route.count(":") != 1:
                continue
            provider_label, model_label = (
                part.strip().casefold() for part in route.split(":", 1)
            )
            if provider_label and model_label:
                routes.add((provider_label, model_label))
        return frozenset(routes)
    except Exception:  # noqa: BLE001 — config read must never crash the caller.
        return frozenset()


def parse_gate5b4c3_shadow_generation_route_env(
    env: Mapping[str, str],
) -> Gate5B4C3ShadowGenerationRouteConfig:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationConfig,
        Gate5B4C3ShadowGenerationProviderCredentialBinding,
    )
    from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
        Gate5B4C3ShadowCounterStore,
    )
    from magi_agent.transport.shadow_generations import (
        Gate5B4C3ShadowGenerationRouteConfig,
    )

    enabled = _is_true(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED")
    )
    if not enabled:
        return Gate5B4C3ShadowGenerationRouteConfig(
            generationConfig=Gate5B4C3ShadowGenerationConfig()
        )
    approved_budgets = _parse_gate5b4c3_shadow_generation_budgets(env)

    configured_routes = _csv_values(
        _trimmed(env.get(_ALLOWED_MODEL_ROUTES_ENV)) or ""
    )
    if configured_routes:
        (
            allowed_provider_labels,
            allowed_model_labels,
            allowed_model_routes,
        ) = _parse_gate5b4c3_shadow_generation_allowed_model_routes(configured_routes)
        (
            allowed_shadow_credential_refs,
            bindings,
        ) = _parse_gate5b4c3_shadow_generation_provider_credential_bindings(
            env,
            allowed_provider_labels=allowed_provider_labels,
        )
        if not allowed_shadow_credential_refs:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS "
                "is required when CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES "
                "is configured"
            )
        _validate_gate5b4c3_shadow_generation_google_env(env, allowed_provider_labels)
        return Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=False,
            liveRunnerBoundaryEnabled=True,
            counterStore=(
                Gate5B4C3ShadowCounterStore(
                    _trimmed(
                        env.get(
                            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH"
                        )
                    ),
                    stale_after_ms=_int_env(
                        env,
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STALE_AFTER_MS",
                        120_000,
                    ),
                )
                if _trimmed(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH")
                )
                is not None
                else None
            ),
            generationConfig=Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=_env_bool_default_true(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH")
                ),
                capStateInitialized=_is_true(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED")
                ),
                generationBudgetExhausted=_is_true(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BUDGET_EXHAUSTED")
                ),
                providerProjectSpendControlsVerified=_is_true(
                    env.get(
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED"
                    )
                ),
                costOwnerWaiver=_is_true(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COST_OWNER_WAIVER")
                ),
                inFlightGenerationRuns=_int_env(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_IN_FLIGHT_RUNS",
                    0,
                ),
                pendingGenerationRuns=_int_env(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PENDING_RUNS",
                    0,
                ),
                dailyGenerationRunsUsed=_int_env(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_DAILY_RUNS_USED",
                    0,
                ),
                dailyGenerationCostUsdUsed=_float_env(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_DAILY_COST_USD_USED",
                    0,
                ),
                selectedBotDigest=_trimmed(
                    _first_non_empty(
                        env,
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST",
                        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
                    )
                ),
                trustedOwnerUserIdDigest=_trimmed(
                    _first_non_empty(
                        env,
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST",
                        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
                    )
                ),
                environment=_trimmed(
                    _first_non_empty(
                        env,
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT",
                        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
                    )
                ),
                allowedProviderLabels=allowed_provider_labels,
                allowedModelLabels=allowed_model_labels,
                allowedModelRoutes=allowed_model_routes,
                allowedShadowCredentialRefs=allowed_shadow_credential_refs,
                providerCredentialBindings=bindings,
                providerCredentialBindingRequired=True,
                botConfigFallbackAllowed=_is_true(
                    env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BOT_CONFIG_FALLBACK_ALLOWED")
                ),
                botConfigFallbackApprovalDigest=_trimmed(
                    env.get(
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BOT_CONFIG_FALLBACK_APPROVAL_DIGEST"
                    )
                ),
                approvedBudgets=approved_budgets,
            ),
        )

    provider_label = _trimmed(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL")
    )
    if provider_label and provider_label != _GATE5B4C3_FIRST_SMOKE_PROVIDER:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL must be google "
            "for the first live smoke"
        )

    model_label = _trimmed(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL")
    )
    if model_label and model_label != _GATE5B4C3_FIRST_SMOKE_MODEL:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL must be "
            f"{_GATE5B4C3_FIRST_SMOKE_MODEL} for the first live smoke"
        )
    credential_ref = _trimmed(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF")
    )
    if credential_ref and credential_ref != _GATE5B4C3_FIRST_SMOKE_CREDENTIAL_REF:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF must be "
            f"{_GATE5B4C3_FIRST_SMOKE_CREDENTIAL_REF} for the first live smoke"
        )
    credential_env = _trimmed(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV")
    )
    if credential_env and credential_env != _GATE5B4C3_FIRST_SMOKE_CREDENTIAL_ENV:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV must be "
            f"{_GATE5B4C3_FIRST_SMOKE_CREDENTIAL_ENV} for the first live smoke"
        )
    if (
        provider_label == _GATE5B4C3_FIRST_SMOKE_PROVIDER
        and model_label == _GATE5B4C3_FIRST_SMOKE_MODEL
    ):
        credential_ref = credential_ref or _GATE5B4C3_FIRST_SMOKE_CREDENTIAL_REF
        credential_env = credential_env or _GATE5B4C3_FIRST_SMOKE_CREDENTIAL_ENV
    google_genai_use_vertexai = _trimmed(env.get("GOOGLE_GENAI_USE_VERTEXAI"))
    if provider_label == _GATE5B4C3_FIRST_SMOKE_PROVIDER and (
        google_genai_use_vertexai is None
        or google_genai_use_vertexai.lower() not in _FALSE_VALUES
    ):
        raise RuntimeEnvError(
            "GOOGLE_GENAI_USE_VERTEXAI must be false for the first live smoke"
        )
    google_genai_use_enterprise = _trimmed(env.get("GOOGLE_GENAI_USE_ENTERPRISE"))
    if (
        provider_label == _GATE5B4C3_FIRST_SMOKE_PROVIDER
        and google_genai_use_enterprise is not None
        and google_genai_use_enterprise.lower() not in _FALSE_VALUES
    ):
        raise RuntimeEnvError(
            "GOOGLE_GENAI_USE_ENTERPRISE must be false for the first live smoke"
        )
    bindings: tuple[Gate5B4C3ShadowGenerationProviderCredentialBinding, ...] = ()
    if (
        provider_label == _GATE5B4C3_FIRST_SMOKE_PROVIDER
        and credential_ref
        and credential_env
    ):
        if credential_env not in _GATE5B4C3_GOOGLE_CREDENTIAL_ENVS:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV must name an "
                "approved Google/Gemini credential env var"
            )
        if _trimmed(env.get(credential_env)):
            bindings = (
                Gate5B4C3ShadowGenerationProviderCredentialBinding(
                    providerLabel=provider_label,
                    credentialRef=credential_ref,
                    credentialSource="env_presence",
                    requiredEnvVars=(credential_env,),
                    presentEnvVars=(credential_env,),
                    projectIdDigest=_trimmed(
                        env.get(
                            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_DIGEST"
                        )
                    ),
                    adkNative=True,
                ),
            )

    return Gate5B4C3ShadowGenerationRouteConfig(
        mockedRunnerBoundaryEnabled=False,
        liveRunnerBoundaryEnabled=True,
        counterStore=(
            Gate5B4C3ShadowCounterStore(
                _trimmed(
                    env.get(
                        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH"
                    )
                ),
                stale_after_ms=_int_env(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STALE_AFTER_MS",
                    120_000,
                ),
            )
            if _trimmed(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH")
            )
            is not None
            else None
        ),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=_env_bool_default_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH")
            ),
            capStateInitialized=_is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED")
            ),
            generationBudgetExhausted=_is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BUDGET_EXHAUSTED")
            ),
            providerProjectSpendControlsVerified=_is_true(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED"
                )
            ),
            costOwnerWaiver=_is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COST_OWNER_WAIVER")
            ),
            inFlightGenerationRuns=_int_env(
                env,
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_IN_FLIGHT_RUNS",
                0,
            ),
            pendingGenerationRuns=_int_env(
                env,
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PENDING_RUNS",
                0,
            ),
            dailyGenerationRunsUsed=_int_env(
                env,
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_DAILY_RUNS_USED",
                0,
            ),
            dailyGenerationCostUsdUsed=_float_env(
                env,
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_DAILY_COST_USD_USED",
                0,
            ),
            selectedBotDigest=_trimmed(
                _first_non_empty(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST",
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
                )
            ),
            trustedOwnerUserIdDigest=_trimmed(
                _first_non_empty(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST",
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
                )
            ),
            environment=_trimmed(
                _first_non_empty(
                    env,
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT",
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
                )
            ),
            allowedProviderLabels=(provider_label,) if provider_label else (),
            allowedModelLabels=(model_label,) if model_label else (),
            allowedModelRoutes=(
                (f"{provider_label}:{model_label}",)
                if provider_label and model_label
                else ()
            ),
            allowedShadowCredentialRefs=(credential_ref,) if credential_ref else (),
            providerCredentialBindings=bindings,
            providerCredentialBindingRequired=True,
            botConfigFallbackAllowed=_is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BOT_CONFIG_FALLBACK_ALLOWED")
            ),
            botConfigFallbackApprovalDigest=_trimmed(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_BOT_CONFIG_FALLBACK_APPROVAL_DIGEST"
                )
            ),
            approvedBudgets=approved_budgets,
        ),
    )


def _parse_gate5b4c3_shadow_generation_budgets(
    env: Mapping[str, str],
) -> Gate5B4C3ShadowGenerationBudgets:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationBudgets,
    )

    values: dict[str, object] = {}
    int_budget_envs = {
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_RUNNER_TIMEOUT_MS": "pythonRunnerTimeoutMs",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_SANITIZED_INPUT_BYTES": "maxSanitizedInputBytes",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_SANITIZED_HISTORY_MESSAGES": "maxSanitizedHistoryMessages",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_INPUT_TOKENS": "maxEstimatedInputTokens",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_OUTPUT_TOKENS": "maxOutputTokens",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_TOTAL_TOKENS": "maxTotalEstimatedTokens",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_ADK_LLM_CALLS": "maxAdkLlmCalls",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_CONCURRENT": "maxConcurrentGenerationRuns",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_PENDING": "maxPendingGenerationRuns",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY": "maxDailyGenerationRuns",
    }
    for env_name, alias in int_budget_envs.items():
        if env.get(env_name) is not None:
            values[alias] = _int_env(env, env_name, 0)
    if env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_COST_USD") is not None:
        values["maxCostUsd"] = _float_env(
            env,
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_COST_USD",
            0,
        )
    if env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD") is not None:
        values["maxDailyGenerationCostUsd"] = _float_env(
            env,
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD",
            0,
        )
    retry_policy = _trimmed(
        env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_RETRY_POLICY")
    )
    if retry_policy is not None:
        values["retryPolicy"] = retry_policy
    try:
        return Gate5B4C3ShadowGenerationBudgets.model_validate(values)
    except (ValidationError, ValueError) as exc:
        failing_name = _first_present_name(
            env,
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_OUTPUT_TOKENS",
            *int_budget_envs.keys(),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_COST_USD",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_RETRY_POLICY",
        ) or "budget env"
        raise RuntimeEnvError(f"{failing_name} exceeds approved Gate 5B-4c-3e caps") from exc


def _parse_gate5b4c3_shadow_generation_allowed_model_routes(
    routes: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    provider_labels: list[str] = []
    model_labels: list[str] = []
    normalized_routes: list[str] = []
    seen_routes: set[str] = set()
    for route in routes:
        if route.count(":") != 1:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES "
                "entries must be provider:model"
            )
        provider_label, model_label = (part.strip() for part in route.split(":", 1))
        if not provider_label or not model_label:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES "
                "entries must include provider and model"
            )
        normalized = f"{provider_label}:{model_label}"
        if normalized in seen_routes:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES "
                "must not contain duplicates"
            )
        seen_routes.add(normalized)
        normalized_routes.append(normalized)
        if provider_label not in provider_labels:
            provider_labels.append(provider_label)
        if model_label not in model_labels:
            model_labels.append(model_label)
    return tuple(provider_labels), tuple(model_labels), tuple(normalized_routes)


def _parse_gate5b4c3_shadow_generation_provider_credential_bindings(
    env: Mapping[str, str],
    *,
    allowed_provider_labels: tuple[str, ...],
) -> tuple[
    tuple[str, ...],
    tuple["Gate5B4C3ShadowGenerationProviderCredentialBinding", ...],
]:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationProviderCredentialBinding,
    )

    raw_bindings = _csv_values(
        _trimmed(
            env.get(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS"
            )
        )
        or ""
    )
    refs: list[str] = []
    bindings: list[Gate5B4C3ShadowGenerationProviderCredentialBinding] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_bindings:
        parts = tuple(part.strip() for part in raw.split(":"))
        if len(parts) not in {3, 4}:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS "
                "entries must be provider:credential-ref:ENV[:adk|litellm]"
            )
        provider_label, credential_ref, env_name = parts[:3]
        mode = parts[3].lower() if len(parts) == 4 else "adk"
        if mode not in {"adk", "litellm"}:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS "
                "mode must be adk or litellm"
            )
        if provider_label not in allowed_provider_labels:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS "
                "provider must be present in the model route allowlist"
            )
        key = (provider_label, credential_ref)
        if key in seen:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS "
                "must not contain duplicate provider/ref pairs"
            )
        seen.add(key)
        if credential_ref not in refs:
            refs.append(credential_ref)
        if not _trimmed(env.get(env_name)):
            continue
        bindings.append(
            Gate5B4C3ShadowGenerationProviderCredentialBinding(
                providerLabel=provider_label,
                credentialRef=credential_ref,
                credentialSource="env_presence",
                requiredEnvVars=(env_name,),
                presentEnvVars=(env_name,),
                adkNative=(mode == "adk"),
            )
        )
    return tuple(refs), tuple(bindings)


def _validate_gate5b4c3_shadow_generation_google_env(
    env: Mapping[str, str],
    allowed_provider_labels: tuple[str, ...],
) -> None:
    if _GATE5B4C3_FIRST_SMOKE_PROVIDER not in allowed_provider_labels:
        return
    google_genai_use_vertexai = _trimmed(env.get("GOOGLE_GENAI_USE_VERTEXAI"))
    if google_genai_use_vertexai is None or google_genai_use_vertexai.lower() not in _FALSE_VALUES:
        raise RuntimeEnvError(
            "GOOGLE_GENAI_USE_VERTEXAI must be false when Google/Gemini is allowlisted"
        )
    google_genai_use_enterprise = _trimmed(env.get("GOOGLE_GENAI_USE_ENTERPRISE"))
    if (
        google_genai_use_enterprise is not None
        and google_genai_use_enterprise.lower() not in _FALSE_VALUES
    ):
        raise RuntimeEnvError(
            "GOOGLE_GENAI_USE_ENTERPRISE must be false when Google/Gemini is allowlisted"
        )


def parse_python_memory_adapter_env(env: Mapping[str, str]) -> PythonMemoryAdapterConfig:
    adapter_raw = (
        env.get("CORE_AGENT_PYTHON_MEMORY_ADAPTER") or "off"
    ).strip().lower().replace("-", "_")
    mode_raw = (env.get("CORE_AGENT_PYTHON_MEMORY_ADAPTER_MODE") or "disabled").strip().lower()

    if not adapter_raw.replace("_", "").isalnum() or len(adapter_raw) > 80:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_MEMORY_ADAPTER must be off or a safe adapter ref")
    if mode_raw not in {"disabled", "readonly_fixture", "readonly_local"}:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_MEMORY_ADAPTER_MODE must be disabled, readonly_fixture, or readonly_local"
        )

    prompt_projection = _is_true(env.get("CORE_AGENT_PYTHON_MEMORY_PROMPT_PROJECTION"))
    live_provider = _is_true(env.get("CORE_AGENT_PYTHON_MEMORY_LIVE_PROVIDER_CALLS"))
    adk_attachment = _is_true(env.get("CORE_AGENT_PYTHON_MEMORY_ADK_SERVICE_ATTACHMENT"))
    if prompt_projection:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_MEMORY_PROMPT_PROJECTION is not approved")
    if live_provider:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_MEMORY_LIVE_PROVIDER_CALLS is not approved")
    if adk_attachment:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_MEMORY_ADK_SERVICE_ATTACHMENT is not approved")

    enabled = adapter_raw != "off" and mode_raw in {"readonly_fixture", "readonly_local"}
    if adapter_raw == "off" or mode_raw == "disabled":
        enabled = False
        adapter_raw = "off"
        mode_raw = "disabled"

    return PythonMemoryAdapterConfig(
        enabled=enabled,
        mode=mode_raw,
        adapter=adapter_raw,
        workspace_root=env.get("CORE_AGENT_PYTHON_MEMORY_WORKSPACE_ROOT"),
        prompt_projection_enabled=False,
        live_provider_calls_enabled=False,
        adk_memory_service_attachment_enabled=False,
    )


def parse_python_toolhost_attachment_env(
    env: Mapping[str, str],
) -> PythonToolHostAttachmentConfig:
    attach_enabled = _is_true(env.get("CORE_AGENT_PYTHON_ADK_TOOLHOST_ATTACH"))
    mode_raw = (env.get("CORE_AGENT_PYTHON_ADK_TOOLHOST_MODE") or "disabled").strip().lower()
    production_attachment = _is_true(
        env.get("CORE_AGENT_PYTHON_TOOLHOST_PRODUCTION_ATTACHMENT")
    )
    live_mutation = _is_true(env.get("CORE_AGENT_PYTHON_TOOLHOST_LIVE_TOOL_MUTATION"))

    if production_attachment:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_TOOLHOST_PRODUCTION_ATTACHMENT is not approved")
    if live_mutation:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_TOOLHOST_LIVE_TOOL_MUTATION is not approved")
    if mode_raw not in {"disabled", "shadow_readonly"}:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_ADK_TOOLHOST_MODE must be disabled or shadow_readonly"
        )

    enabled = attach_enabled and mode_raw == "shadow_readonly"
    if not enabled:
        mode_raw = "disabled"

    return PythonToolHostAttachmentConfig(
        enabled=enabled,
        mode=mode_raw,
        production_attachment_enabled=False,
        live_tool_mutation_enabled=False,
    )


def parse_python_security_posture_env(
    env: Mapping[str, str],
) -> PythonSecurityPostureConfig:
    false_only_flags = (
        "CORE_AGENT_PYTHON_SECURITY_EXTERNAL_SURFACE_DISPATCH",
        "CORE_AGENT_PYTHON_SECURITY_CREDENTIAL_BROKER_ATTACHMENT",
        "CORE_AGENT_PYTHON_SECURITY_CONTEXT_GUARD_BLOCK_MODE",
        "CORE_AGENT_PYTHON_SECURITY_SUPPLY_CHAIN_STARTUP_BANNER",
    )
    for name in false_only_flags:
        if _is_true(env.get(name)):
            raise RuntimeEnvError(f"{name} is not approved")

    preflight = _is_true(env.get("CORE_AGENT_PYTHON_SECURITY_POSTURE_PREFLIGHT"))
    return PythonSecurityPostureConfig(
        enabled=preflight,
        posturePreflightAttached=preflight,
        externalSurfaceDispatchAttached=False,
        credentialBrokerAttached=False,
        contextGuardBlocksPromptProjection=False,
        supplyChainStartupBannerAttached=False,
    )


def parse_python_context_continuity_env(
    env: Mapping[str, str],
) -> PythonContextContinuityConfig:
    false_only_flags = (
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PRODUCTION_AUTHORITY",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_TRANSCRIPT_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SSE_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_DB_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_EVIDENCE_VERIFIED",
    )
    for name in false_only_flags:
        if _is_true(env.get(name)):
            raise RuntimeEnvError(f"{name} is not approved")

    enabled = _is_true(env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED"))
    mode = (
        env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE") or ""
    ).strip().lower()
    if not enabled:
        mode = "disabled"
    elif not mode:
        mode = "local_diagnostic"
    if mode not in {"disabled", "local_diagnostic", "selected_canary"}:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE must be disabled, "
            "local_diagnostic, or selected_canary"
        )
    if enabled and mode == "disabled":
        enabled = False
    local_canary_harness_enabled = _is_true(
        env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS")
    )
    if local_canary_harness_enabled:
        if not enabled or mode != "selected_canary":
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS requires "
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED=1 and MODE=selected_canary"
            )
        return _build_context_continuity_local_canary_config()

    canary_status = (
        env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_STATUS") or "missing"
    ).strip().lower()
    if canary_status not in {"missing", "pass", "fail"}:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_STATUS must be missing, pass, or fail"
        )

    fallback_status = (
        env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_FALLBACK_STATUS") or "missing"
    ).strip().lower()
    if fallback_status not in {
        "missing",
        "none",
        "closed",
        "typescript_fallback",
        "failed",
    }:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_FALLBACK_STATUS is invalid"
        )

    reason_codes = _safe_reason_codes(
        env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REASON_CODES") or ""
    )
    return PythonContextContinuityConfig(
        enabled=enabled,
        mode=mode,
        canaryStatus=canary_status,
        importedEventCount=_int_env(
            env,
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_IMPORTED_EVENT_COUNT",
            0,
        ),
        rejectedEntryCount=_int_env(
            env,
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REJECTED_ENTRY_COUNT",
            0,
        ),
        compactionApplied=_is_true(
            env.get("CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_COMPACTION_APPLIED")
        ),
        projectionDigestPresent=_digest_env_present(
            env,
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PROJECTION_DIGEST",
        ),
        modelVisibleDigestPresent=_digest_env_present(
            env,
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODEL_VISIBLE_DIGEST",
        ),
        sourceTranscriptHeadDigestPresent=_digest_env_present(
            env,
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SOURCE_TRANSCRIPT_HEAD_DIGEST",
        ),
        fallbackStatus=fallback_status,
        reasonCodes=reason_codes,
        productionAuthorityAllowed=False,
        transcriptWriteAllowed=False,
        sseWriteAllowed=False,
        dbWriteAllowed=False,
    )


def _build_context_continuity_local_canary_config() -> PythonContextContinuityConfig:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    expected_antecedent = "the prior synthetic preference"
    current_followup = "what should I do with that preference?"
    model_visible_message = (
        "Use the prior synthetic preference when answering what should happen next."
    )
    runner_result = SimpleNamespace(
        status="completed",
        context_continuity=SimpleNamespace(
            imported_event_count=4,
            rejected_entry_count=1,
            compaction_applied=True,
            projection_digest=_sha256_digest("local-readiness-projection"),
            model_visible_digest=_sha256_digest("local-readiness-model-visible"),
            source_transcript_head_digest=_sha256_digest(
                "local-readiness-source-head"
            ),
        ),
    )
    evidence = build_pre_gate8_continuity_canary_evidence(
        runner_result,
        adk_session_texts=(
            "Synthetic local continuity setup.",
            f"Remember {expected_antecedent}.",
        ),
        model_visible_message=f"{model_visible_message} {current_followup}",
        expected_antecedent=expected_antecedent,
        current_followup=current_followup,
        forbidden_payloads=("private-token", "raw transcript"),
        require_compaction_applied=True,
        require_rejected_entries=True,
        fallback_status="none",
    )
    return PythonContextContinuityConfig.from_canary_evidence(evidence)


def parse_python_gate2_readiness_env(env: Mapping[str, str]) -> PythonGate2ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED"))
    return PythonGate2ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH")
        ),
        localSandboxHarnessEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS")
        ),
        selectedBotDigest=(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST") or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT") or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST") or ""
        ),
        profileRef=(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF")
            or "openmagi.gate2.workspace-canary.v1"
        ).strip(),
        profileDigest=(
            env.get("CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST") or ""
        ).strip(),
        maxMutationAttemptsPerTurn=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE2_READINESS_MAX_MUTATION_ATTEMPTS_PER_TURN",
            8 if enabled else 0,
        ),
        routeAttached=False,
        productionWorkspaceMutationAllowed=False,
        writeMutationAuthorityAllowed=False,
        userVisibleOutputAllowed=False,
        toolHostDispatchAllowed=False,
        liveToolExecutionAllowed=False,
        memoryWriteAllowed=False,
        browserWebChannelAllowed=False,
        schedulerMutationAllowed=False,
        connectorCredentialUseAllowed=False,
        networkEgressAllowed=False,
    )


def parse_python_gate3_readiness_env(env: Mapping[str, str]) -> PythonGate3ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE3_READINESS_ENABLED"))
    return PythonGate3ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE3_READINESS_KILL_SWITCH")
        ),
        localReplayHarnessEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE3_READINESS_LOCAL_REPLAY_HARNESS")
        ),
        selectedBotDigest=(
            env.get("CORE_AGENT_PYTHON_GATE3_READINESS_SELECTED_BOT_DIGEST") or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE3_READINESS_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE3_READINESS_ENVIRONMENT") or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE3_READINESS_ENV_ALLOWLIST") or ""
        ),
        maxReplayBundles=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE3_READINESS_MAX_REPLAY_BUNDLES",
            1 if enabled else 0,
        ),
        routeAttached=False,
        liveCaptureAllowed=False,
        modelCallAllowed=False,
        userVisibleOutputAllowed=False,
        toolHostDispatchAllowed=False,
        workspaceMutationAllowed=False,
        memoryWriteAllowed=False,
        browserWebNetworkAllowed=False,
        channelDeliveryAllowed=False,
        schedulerMutationAllowed=False,
        dbWriteAllowed=False,
    )


def parse_python_gate4_readiness_env(env: Mapping[str, str]) -> PythonGate4ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE4_READINESS_ENABLED"))
    return PythonGate4ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE4_READINESS_KILL_SWITCH")
        ),
        localShadowHarnessEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE4_READINESS_LOCAL_SHADOW_HARNESS")
        ),
        selectedBotDigest=(
            env.get("CORE_AGENT_PYTHON_GATE4_READINESS_SELECTED_BOT_DIGEST") or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE4_READINESS_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE4_READINESS_ENVIRONMENT") or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE4_READINESS_ENV_ALLOWLIST") or ""
        ),
        maxLocalBundles=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE4_READINESS_MAX_LOCAL_BUNDLES",
            1 if enabled else 0,
        ),
        routeAttached=False,
        adkRunnerInvoked=False,
        liveRunnerAttached=False,
        modelCallAllowed=False,
        userVisibleOutputAllowed=False,
        toolHostDispatchAllowed=False,
        liveToolsExecuted=False,
        workspaceMutationAllowed=False,
        memoryWriteAllowed=False,
        browserWebNetworkAllowed=False,
        channelDeliveryAllowed=False,
        schedulerMutationAllowed=False,
        dbWriteAllowed=False,
    )


def parse_python_gate5_readiness_env(env: Mapping[str, str]) -> PythonGate5ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE5_READINESS_ENABLED"))
    return PythonGate5ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE5_READINESS_KILL_SWITCH")
        ),
        nonUserVisibleHarnessEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE5_READINESS_NON_USER_VISIBLE_HARNESS")
        ),
        selectedBotDigest=(
            env.get("CORE_AGENT_PYTHON_GATE5_READINESS_SELECTED_BOT_DIGEST") or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE5_READINESS_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE5_READINESS_ENVIRONMENT") or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE5_READINESS_ENV_ALLOWLIST") or ""
        ),
        maxShadowChecks=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE5_READINESS_MAX_SHADOW_CHECKS",
            1 if enabled else 0,
        ),
        routeAttached=False,
        shadowEndpointEnabled=False,
        adkRunnerInvoked=False,
        liveRunnerAttached=False,
        modelCallAllowed=False,
        userVisibleOutputAllowed=False,
        providerCredentialAllowed=False,
        proxyEgressAllowed=False,
        toolHostDispatchAllowed=False,
        liveToolsExecuted=False,
        workspaceMutationAllowed=False,
        memoryWriteAllowed=False,
        browserWebNetworkAllowed=False,
        channelDeliveryAllowed=False,
        schedulerMutationAllowed=False,
        dbWriteAllowed=False,
    )


def parse_python_gate7_readiness_env(env: Mapping[str, str]) -> PythonGate7ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED"))
    return PythonGate7ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH")
        ),
        localReplayHarnessEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS")
        ),
        selectedBotDigest=(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST") or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT") or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST") or ""
        ),
        maxLocalChildTasks=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS",
            1 if enabled else 0,
        ),
        maxEnvelopeBytes=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES",
            8192 if enabled else 0,
        ),
        maxAdoptionPreflights=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS",
            1 if enabled else 0,
        ),
        requiredSurfaceRefs=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_REQUIRED_SURFACES") or ""
        ),
        optionalSurfaceRefs=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE7_READINESS_OPTIONAL_SURFACES") or ""
        ),
        routeAttached=False,
        adkRunnerInvoked=False,
        childExecutionAllowed=False,
        realChildRunnerExecuted=False,
        workspaceAdoptionApplied=False,
        workspaceMutationAllowed=False,
        modelCallAllowed=False,
        userVisibleOutputAllowed=False,
        providerCredentialAllowed=False,
        proxyEgressAllowed=False,
        toolHostDispatchAllowed=False,
        liveToolsExecuted=False,
        memoryWriteAllowed=False,
        browserWebNetworkAllowed=False,
        channelDeliveryAllowed=False,
        schedulerMutationAllowed=False,
        dbWriteAllowed=False,
    )


def parse_python_gate8_readiness_env(env: Mapping[str, str]) -> PythonGate8ReadinessConfig:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED"))
    return PythonGate8ReadinessConfig(
        enabled=enabled,
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH")
        ),
        selectedBotDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST"
            )
            or ""
        ).strip(),
        selectedOwnerUserIdDigest=(
            env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST"
            )
            or ""
        ).strip(),
        environment=(
            env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT")
            or "local"
        ).strip()
        or "local",
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST") or ""
        ),
        maxContinuityEvidenceAgeSeconds=_int_env(
            env,
            "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_MAX_CONTINUITY_EVIDENCE_AGE_SECONDS",
            600,
        ),
        routeAttached=False,
        productionRouteAttached=False,
        userVisibleOutputAllowed=False,
        writeMutationAllowed=False,
        toolDispatchAllowed=False,
        readOnlyToolDispatchAllowed=False,
        transcriptWriteAllowed=False,
        sseWriteAllowed=False,
        dbWriteAllowed=False,
        memoryWriteAllowed=False,
        channelDeliveryAllowed=False,
        workspaceMutationAllowed=False,
        missionSchedulerAllowed=False,
        backgroundTaskAllowed=False,
        selfImprovementAllowed=False,
    )


def parse_python_runtime_authority_env(env: Mapping[str, str]) -> PythonRuntimeAuthorityConfig:
    false_only_flags = (
        "CORE_AGENT_PYTHON_TRANSCRIPT_WRITE",
        "CORE_AGENT_PYTHON_SSE_WRITE",
        "CORE_AGENT_PYTHON_CHANNEL_DELIVERY",
        "CORE_AGENT_PYTHON_DB_WRITE",
        "CORE_AGENT_PYTHON_WORKSPACE_MUTATION",
        "CORE_AGENT_PYTHON_CHILD_EXECUTION",
        "CORE_AGENT_PYTHON_MISSION_RUNTIME",
        "CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE",
    )
    for name in false_only_flags:
        if _is_true(env.get(name)):
            raise RuntimeEnvError(f"{name} is not approved")

    output_mode = (env.get("CORE_AGENT_PYTHON_OUTPUT_MODE") or "diagnostic_only").strip().lower()
    if output_mode not in {
        "diagnostic_only",
        "health_only",
        "off",
        "user_visible_canary",
    }:
        raise RuntimeEnvError(
            "CORE_AGENT_PYTHON_OUTPUT_MODE must be diagnostic_only, health_only, off, or user_visible_canary"
        )

    user_visible_requested = _is_true(env.get("CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT"))
    canary_requested = _is_true(env.get("CORE_AGENT_PYTHON_CANARY_ROUTING"))
    if user_visible_requested or canary_requested:
        if user_visible_requested is not canary_requested:
            missing_or_partial = (
                "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT"
                if user_visible_requested
                else "CORE_AGENT_PYTHON_CANARY_ROUTING"
            )
            raise RuntimeEnvError(f"{missing_or_partial} is not approved")
        if _is_true(env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED")):
            _validate_gate8_selected_authority(env, output_mode)
        else:
            _validate_gate5b_user_visible_canary_authority(env, output_mode)
        return PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )

    return PythonRuntimeAuthorityConfig()


def _validate_gate5b_user_visible_canary_authority(
    env: Mapping[str, str],
    output_mode: str,
) -> None:
    required_true = (
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT",
        "CORE_AGENT_PYTHON_CANARY_ROUTING",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED",
    )
    for name in required_true:
        if not _is_true(env.get(name)):
            raise RuntimeEnvError(f"{name} is required for Gate 5B user-visible canary authority")
    if output_mode != "user_visible_canary":
        raise RuntimeEnvError("CORE_AGENT_PYTHON_OUTPUT_MODE must be user_visible_canary")
    if (env.get("CORE_AGENT_PYTHON_CHAT_ROUTE") or "").strip().lower() != "on":
        raise RuntimeEnvError("CORE_AGENT_PYTHON_CHAT_ROUTE must be on")
    if _is_true(env.get("CORE_AGENT_PYTHON_GATE5B_KILL_SWITCH")):
        raise RuntimeEnvError("Gate 5B global kill switch is active")
    if _is_true(env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH")):
        raise RuntimeEnvError("Gate 5B user-visible canary kill switch is active")

    bot_digest = _sha256_digest(env.get("BOT_ID") or "")
    owner_digest = _sha256_digest(env.get("USER_ID") or "")
    expected_bot_digest = (
        env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST")
        or ""
    ).strip()
    expected_owner_digest = (
        env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST")
        or ""
    ).strip()
    if not expected_bot_digest or expected_bot_digest != bot_digest:
        raise RuntimeEnvError("Gate 5B selected bot digest mismatch")
    if not expected_owner_digest or expected_owner_digest != owner_digest:
        raise RuntimeEnvError("Gate 5B trusted owner digest mismatch")

    environment = (
        env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT") or ""
    ).strip()
    environment_allowlist = _csv_values(
        env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST") or ""
    )
    if not environment or environment not in {"local", "development", "staging", "production"}:
        raise RuntimeEnvError("Gate 5B user-visible canary environment is invalid")
    if environment not in environment_allowlist:
        raise RuntimeEnvError("Gate 5B user-visible canary environment is not allowlisted")


def _validate_gate8_selected_authority(
    env: Mapping[str, str],
    output_mode: str,
) -> None:
    required_true = (
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT",
        "CORE_AGENT_PYTHON_CANARY_ROUTING",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED",
    )
    for name in required_true:
        if not _is_true(env.get(name)):
            raise RuntimeEnvError(
                f"{name} is required for Gate 8 selected Python authority"
            )
    if output_mode != "user_visible_canary":
        raise RuntimeEnvError("CORE_AGENT_PYTHON_OUTPUT_MODE must be user_visible_canary")
    if (env.get("CORE_AGENT_PYTHON_CHAT_ROUTE") or "").strip().lower() != "on":
        raise RuntimeEnvError("CORE_AGENT_PYTHON_CHAT_ROUTE must be on")
    if _env_bool_default_true(
        env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH")
    ):
        raise RuntimeEnvError("Gate 8 selected Python authority kill switch is active")

    bot_digest = _sha256_digest(env.get("BOT_ID") or "")
    owner_digest = _sha256_digest(env.get("USER_ID") or "")
    expected_bot_digest = (
        env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST")
        or ""
    ).strip()
    expected_owner_digest = (
        env.get(
            "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST"
        )
        or ""
    ).strip()
    if not expected_bot_digest or expected_bot_digest != bot_digest:
        raise RuntimeEnvError("Gate 8 selected bot digest mismatch")
    if not expected_owner_digest or expected_owner_digest != owner_digest:
        raise RuntimeEnvError("Gate 8 trusted owner digest mismatch")

    environment = (
        env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT") or ""
    ).strip()
    environment_allowlist = _csv_values(
        env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST") or ""
    )
    if not environment or environment not in {"local", "development", "staging", "production"}:
        raise RuntimeEnvError("Gate 8 selected authority environment is invalid")
    if environment not in environment_allowlist:
        raise RuntimeEnvError("Gate 8 selected authority environment is not allowlisted")
    if not parse_python_context_continuity_env(env).continuity_canary_ready:
        raise RuntimeEnvError(
            "Pre-Gate8 continuity PASS evidence is required for Gate 8 authority"
        )


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_env_present(env: Mapping[str, str], name: str) -> bool:
    value = _trimmed(env.get(name))
    if value is None:
        return False
    if not (
        value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    ):
        raise RuntimeEnvError(f"{name} must be a sha256 digest")
    return True


def _safe_reason_codes(value: str) -> tuple[str, ...]:
    codes = _csv_values(value)
    for code in codes:
        normalized = code.replace("_", "").replace("-", "")
        if not normalized.isalnum() or len(code) > 96:
            raise RuntimeEnvError(
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REASON_CODES must contain safe labels"
            )
    return codes


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def is_read_ledger_enabled(env: Mapping[str, str]) -> bool:
    """Single source of truth for the read-before-edit ledger activation flag.

    Default ON in the local full runtime profile. When enabled, the Gate 5B full
    toolhost records full reads and blocks edits/overwrites of existing files
    that were not freshly read first (read-before-edit enforcement).
    """

    return _runtime_feature_enabled(env, "MAGI_READ_LEDGER_ENABLED")


MAGI_SELF_INTROSPECTION_ENABLED_ENV = "MAGI_SELF_INTROSPECTION_ENABLED"


def is_self_introspection_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the self-introspection tool activation flag.

    Default ON in the local full runtime profile. Explicit false/off values or
    safe runtime profiles keep the ``InspectSelfEvidence`` tool bound but not
    advertised, so the model never sees it.
    """
    source = os.environ if env is None else env
    return _runtime_feature_enabled(source, MAGI_SELF_INTROSPECTION_ENABLED_ENV)


MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV = "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"


def is_evidence_ledger_lifecycle_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the per-turn EvidenceLedger lifecycle flag.

    Default ON in the local full runtime profile. Explicit false/off values or
    safe runtime profiles keep the local tool-evidence collector from building
    ``EvidenceLedger`` objects and leave CLI ``source_ledger`` empty. When ON
    the collector synthesizes minimal per-turn ledgers from recorded tool
    results and the factories thread those ledgers onto ``ToolContext`` so
    ``InspectSelfEvidence`` can report real local tool calls.
    """
    source = os.environ if env is None else env
    return _runtime_feature_enabled(source, MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV)


MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV = "MAGI_GROUNDED_ANSWER_GUARD_ENABLED"


def is_grounded_answer_guard_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the grounded-answer guard activation flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the
    grounded-answer guard never runs: callers emit no grounding metadata and no
    prompt/answer surface is altered, so behaviour is byte-identical to today.
    When ON, a caller (the GAIA harness / CLI layer) may compute a
    :class:`~magi_agent.research.grounded_answer_guard.GroundedAnswerVerdict`
    against its collected tool corpus and record ``verifierEvidenceStatus`` as
    out-of-band metadata. Like ``is_egress_gate_enabled`` /
    ``is_goal_nudge_enabled`` this deliberately does NOT follow the
    runtime-profile default-ON convention — it is an additive, default-disabled
    seam.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV))


MAGI_EGRESS_GATE_ENABLED_ENV = "MAGI_EGRESS_GATE_ENABLED"


def is_egress_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the egress critic gate activation flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    user-visible chat egress path is byte-identical to today: no evidence-view
    building and no critic call. When ON, fact-critical turns run a lean,
    evidence-grounded critic before egress and set ``verifierEvidenceStatus`` on
    the response. Like ``is_self_introspection_enabled`` this deliberately does
    NOT follow the runtime-profile default-ON convention — it is an additive,
    default-disabled seam.
    """
    # Delegate to the canonical config.flags registry (PR2). Behaviour is
    # byte-identical to the previous ``_is_true(source.get(...))`` form because
    # MAGI_EGRESS_GATE_ENABLED is registered with a False default and the same
    # strict-truthy parser. Imported lazily to avoid a config<->flags import cycle.
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_EGRESS_GATE_ENABLED_ENV, env=source)


MAGI_GOAL_NUDGE_ENABLED_ENV = "MAGI_GOAL_NUDGE_ENABLED"


def is_goal_nudge_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the production goal-nudge activation flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the
    production CLI/serve engine wiring injects ``goal_nudge=None`` so
    ``MagiEngineDriver._drive`` behaves byte-identically to pre-PR4. When ON,
    ``cli.goal_nudge_wiring.build_goal_nudge_from_env`` constructs a
    :class:`~magi_agent.runtime.goal_nudge.GoalNudge` (default ``mode="goal"``)
    and threads it onto the engine so a clean stop short of the goal triggers a
    bounded continuation. Like ``is_egress_gate_enabled`` this deliberately does
    NOT follow the runtime-profile default-ON convention — it is an additive,
    default-disabled seam.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_GOAL_NUDGE_ENABLED_ENV))


MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV = "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED"


def is_research_fact_guidance_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the research_fact cross-check guidance flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the
    ``research_fact`` evidence brief and ``build_cli_instruction`` output are
    byte-identical to the pre-flag baseline. When ON, ``research_fact`` wraps a
    successful multi-source brief in a consolidation scaffold (question echo +
    fetched-source count header, deterministic cross-check footer) and — when
    BRAVE_API_KEY + FIRECRAWL_API_KEY are also present — the system prompt
    carries one ``<web_research>`` block advertising the tool with a
    read-and-compare few-shot. Like ``is_goal_nudge_enabled`` this deliberately
    does NOT follow the runtime-profile default-ON convention — it is an
    additive, default-disabled seam (A/B evidence gates any default flip).
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV))


MAGI_FACTS_REPLAN_ENABLED_ENV = "MAGI_FACTS_REPLAN_ENABLED"


def is_facts_replan_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the facts-survey replanning activation flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF,
    ``build_default_plane`` never registers the
    :class:`~magi_agent.adk_bridge.facts_replan_control.FactsReplanControl`, so
    the live model loop is byte-identical to before. When ON, the control
    injects a periodic in-context facts survey + plan refresh every
    ``MAGI_FACTS_REPLAN_INTERVAL`` working steps (capped per turn by
    ``MAGI_FACTS_REPLAN_MAX_PER_TURN``). Like ``is_goal_nudge_enabled`` this
    deliberately does NOT follow the runtime-profile default-ON convention — it
    is an additive, default-disabled seam.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_FACTS_REPLAN_ENABLED_ENV))


def parse_facts_replan_env(env: Mapping[str, str] | None = None):
    """Re-export of :func:`magi_agent.runtime.facts_replan.parse_facts_replan_env`.

    Imported lazily because ``runtime.facts_replan`` consumes
    :func:`is_facts_replan_enabled` from this module (a top-level import here
    would be circular). Returns a ``FactsReplanConfig | None``.
    """
    from magi_agent.runtime.facts_replan import (  # noqa: PLC0415
        parse_facts_replan_env as _parse_facts_replan_env,
    )

    return _parse_facts_replan_env(env)
MAGI_STEP_DECOMPOSITION_ENABLED_ENV = "MAGI_STEP_DECOMPOSITION_ENABLED"


def is_step_decomposition_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the multi-step decomposition guidance flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the
    ``build_cli_instruction`` system prompt and the GAIA harness instruction are
    byte-identical to the pre-flag baseline. When ON, the system prompt carries
    one ``<step_decomposition>`` block that asks the agent to enumerate the
    dependent sub-steps of a multi-hop question up front and resolve/confirm each
    before proceeding — a *light*, prompt-only nudge that reuses the existing
    planning/TodoWrite seams (no new control loop, no orchestrator, no extra
    model calls). This targets long L3 chains where one broken intermediate link
    yields a wrong final answer.

    Delegates to the canonical ``config.flags`` registry (``flag_bool``) backed by
    the ``MAGI_STEP_DECOMPOSITION_ENABLED`` ``FlagSpec``, matching
    ``is_egress_gate_enabled`` exactly: byte-identical to the raw
    ``_is_true(source.get(...))`` form because the flag is registered with a
    ``False`` default and the same strict-truthy parser. Like
    ``is_egress_gate_enabled`` / ``is_goal_nudge_enabled`` this is an additive,
    default-disabled seam and does NOT follow the runtime-profile default-ON
    convention (A/B evidence gates any default flip). Imported lazily to avoid a
    config<->flags import cycle.
    """
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, env=source)


MAGI_USER_HOOKS_ENABLED_ENV = "MAGI_USER_HOOKS_ENABLED"


def is_user_hooks_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Master gate for CC-style user ``settings.json`` hooks (cluster doc 11 PR2).

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the CLI
    engine never loads ``~/.magi/settings.json`` / ``<workspace>/.magi/settings.json``
    hooks and never constructs a user :class:`~magi_agent.hooks.bus.HookBus`, so a
    turn is byte-identical to today. When ON (self-host / local CLI only — never
    hosted multi-tenant, since command hooks run operator-supplied ``bash -c``),
    the engine loads the user hooks, builds one HookBus wired to the **command**
    executor (http/llm deferred to a later PR), and bridges the
    ``PreToolUse``/``PostToolUse`` lifecycle points onto the ADK
    before/after-tool callbacks. Like ``is_egress_gate_enabled`` / ``is_goal_nudge_enabled``
    this is an additive, default-disabled seam and does NOT follow the
    runtime-profile default-ON convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_USER_HOOKS_ENABLED_ENV))


MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV = "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED"


def is_tool_synthesis_nudge_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Master gate for the Live-SWE-style tool-synthesis reflection nudge.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF, the
    per-step reflection nudge plugin is never registered on the control plane
    and ``build_cli_instruction`` never appends the "creating your own tools"
    recipe block — a turn is byte-identical to today. When ON, BOTH surfaces
    activate ONLY for frontier-tier models (``sota``/``reasoning`` in the
    ``ModelTierRegistry``; see ``magi_agent.runtime.tool_synthesis``) because
    the mechanism measurably HURTS weak models (Live-SWE ablation:
    GPT-5-Nano 44%->14%). Like ``is_goal_nudge_enabled`` this is an additive,
    default-disabled seam and does NOT follow the runtime-profile default-ON
    convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV))


MAGI_RECIPE_ROUTING_LLM_ENABLED_ENV = "MAGI_RECIPE_ROUTING_LLM_ENABLED"


def recipe_routing_llm_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Default OFF (strict truthy). ON = model selects recipe packs by
    when_to_use descriptions; OFF = byte-identical to today's selector-membership
    path. Distinct from the runtime-profile default-ON convention."""
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_RECIPE_ROUTING_LLM_ENABLED_ENV))


MAGI_WORKER_ROUTING_LLM_ENABLED_ENV = "MAGI_WORKER_ROUTING_LLM_ENABLED"


def worker_routing_llm_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Default OFF (strict truthy). ON = planner-emitted worker_role honored;
    OFF = byte-identical keyword inference (_infer_evidence_hint)."""
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_WORKER_ROUTING_LLM_ENABLED_ENV))


MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV = "MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED"


def is_key_aware_model_routes_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for key-aware child-spawn model route filtering.

    Default OFF (strict truthy opt-in). When ON, :func:`available_child_model_routes`
    and :func:`resolve_child_route` filter routes to only those whose provider
    has a configured API key. OFF (or no keys at all, or any error) is
    byte-identical to today (fail-open).
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV))


MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV = "MAGI_TOOL_USAGE_GUIDANCE_ENABLED"


def is_tool_usage_guidance_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for per-tool usage-guidance synthesis into gate5b ADK descriptions.

    Default OFF (strict truthy opt-in). OFF keeps every gate5b tool docstring
    byte-identical to today; ON appends a lean "Use when / Do NOT use when"
    block (``magi_agent.gates.tool_usage_guidance``) for registered tools.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV))


MAGI_PROMPT_EXAMPLES_ENABLED_ENV = "MAGI_PROMPT_EXAMPLES_ENABLED"


def is_prompt_examples_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for the action-discipline example-pairs prompt block.

    Default OFF. ON appends ``<action_discipline_examples>`` (positive/negative
    contrast pairs: act-vs-ask, finish-vs-defer) in ``build_cli_instruction``.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_PROMPT_EXAMPLES_ENABLED_ENV))


MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV = "MAGI_PROMPT_SEARCH_RULES_ENABLED"


def is_prompt_search_rules_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for the search-decision heuristics prompt block.

    Default OFF. Even when ON, the block only fires when web tools are
    available (``BRAVE_API_KEY`` AND ``FIRECRAWL_API_KEY`` — same rule as
    ``web_research_guidance_block``: never direct the model to absent tools).
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV))


MAGI_PROMPT_REDFLAGS_ENABLED_ENV = "MAGI_PROMPT_REDFLAGS_ENABLED"


def is_prompt_redflags_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for the anti-rationalization red-flags prompt block.

    Default OFF. ON appends ``<red_flags>`` ("this thought means stop and
    correct course" table) in ``build_cli_instruction``.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_PROMPT_REDFLAGS_ENABLED_ENV))


MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV = "MAGI_RESEARCH_METHODOLOGY_ENABLED"


def is_research_methodology_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for the research-methodology prompt block.

    Default OFF. ON appends ``<research_methodology>`` (multi-source
    cross-check / grounding-first / primary-source preference / citation
    discipline) in ``build_cli_instruction``. Guidance only, not enforcing.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV))


MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV = "MAGI_AUTOMATION_METHODOLOGY_ENABLED"


def is_automation_methodology_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Gate for the automation-methodology prompt block.

    Default OFF. ON appends ``<automation_methodology>`` (deliverable up
    front / goal->plan->evidence lifecycle / step confirmation) in
    ``build_cli_instruction``. Guidance only, not enforcing.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV))


MAGI_DOCUMENT_AUTHORING_COVERAGE_ENV = "MAGI_DOCUMENT_AUTHORING_COVERAGE"


def is_document_authoring_coverage_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the document-authoring coverage-blocking gate.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    ``DocumentCoverage`` evidence emitted by ``docx_write`` (Task B) stays
    audit-only: the pre-final verifier bus never blocks on it and a non-document
    turn is byte-identical to today. When ON, the ``document-authoring-coverage``
    verifier-bus gate blocks turn/commit completion whenever a ``DocumentCoverage``
    record reports failed coverage (``fields["status"] != "pass"``), pushing the
    agent to regenerate. Like ``is_self_introspection_enabled`` /
    ``is_egress_gate_enabled`` this deliberately does NOT follow the
    runtime-profile default-ON convention — it is an additive, default-disabled,
    optional-blocking seam.
    """
    source = os.environ if env is None else env
    return resolve_document_authoring_coverage_mode(source) != "off"


DOCUMENT_AUTHORING_COVERAGE_MODES = ("off", "advisory", "block")


def resolve_document_authoring_coverage_mode(env: Mapping[str, str] | None = None) -> str:
    """Resolve the 3-state document-coverage gate mode (14-PR3, C11).

    Returns one of ``off`` | ``advisory`` | ``block``. This generalizes the
    historical boolean ``MAGI_DOCUMENT_AUTHORING_COVERAGE`` flag so the hosted
    control-stage overlay can promote the gate gradually (``off`` -> ``advisory``
    -> ``block``) instead of flipping straight to a hard block, which is the
    highest false-block risk in the C11 cluster.

    Resolution (all case/whitespace-insensitive):

    * unset / empty / ``0`` / falsy   -> ``off``
    * legacy truthy (``1``/``true``/``yes``/``on``) -> ``block`` (back-compat:
      the old boolean ON meant hard-block)
    * explicit ``off`` / ``advisory`` / ``block`` -> that mode
    * anything else (typo) -> ``off`` (fail safe: never silently hard-block)

    In ``advisory`` mode the verifier bus still computes the failed-coverage
    count (for telemetry / false-block-rate measurement) but the engine does not
    let it flip the pre-final decision to ``block``.
    """
    source = os.environ if env is None else env
    raw = (source.get(MAGI_DOCUMENT_AUTHORING_COVERAGE_ENV) or "").strip().lower()
    if not raw:
        return "off"
    if raw in DOCUMENT_AUTHORING_COVERAGE_MODES:
        return raw
    if _is_true(raw):
        return "block"
    return "off"


MAGI_CONTROL_STAGE_ENV = "MAGI_CONTROL_STAGE"
MAGI_DEPLOYMENT_ENV = "MAGI_DEPLOYMENT"


def resolve_control_stage(env: Mapping[str, str] | None = None) -> str:
    """Single source of truth for the hosted control-stage selector.

    Resolves ``MAGI_CONTROL_STAGE`` (``off|resilience|full|hardgate``), failing
    safe to ``off`` for unknown/empty values so a typo never silently flips a
    more aggressive stage. The actual env overlay lives in
    :mod:`magi_agent.runtime.hosted_defaults`; this helper exists so callers read
    the flag through ``config/env`` (15-flag-governance P1-6).
    """
    source = os.environ if env is None else env
    from ..runtime.hosted_defaults import resolve_control_stage as _resolve  # noqa: PLC0415

    return _resolve(source)


def is_hosted_deployment(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for explicit hosted-deployment detection.

    True only when ``MAGI_DEPLOYMENT=hosted`` is explicitly set. Reverse-detection
    from the local-dev identity is intentionally avoided (doc 14 open-decision #2).
    """
    source = os.environ if env is None else env
    from ..runtime.hosted_defaults import is_hosted_deployment as _is_hosted  # noqa: PLC0415

    return _is_hosted(source)


MAGI_HOSTED_STREAMING_SERVE_ENV = "MAGI_HOSTED_STREAMING_SERVE"


def is_hosted_streaming_serve_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for hosted serving over the SSE stream route (08-PR3).

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    ``/v1/chat/stream`` route is byte-identical to today: a request that does not
    match the selected gate5b canary gate falls through to the local headless
    engine path. When ON, the stream route serves with completions-equivalent
    gating — gate2 sandbox-canary dispatch, honest ``python_disabled`` /
    ``invalid_authority`` fallback JSON when the canary gate is not active, and
    no local-engine fallthrough — so hosted chat-proxy can converge onto the
    streaming route without minting a gate/counter/receipt bypass surface. Like
    ``is_egress_gate_enabled`` this deliberately does NOT follow the
    runtime-profile default-ON convention — it is an additive, default-disabled
    serving mode.
    """
    # Delegate to the canonical config.flags registry; registered with a False
    # default and the strict-truthy parser. Imported lazily to avoid a
    # config<->flags import cycle.
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_HOSTED_STREAMING_SERVE_ENV, env=source)


MAGI_HOSTED_SESSION_REUSE_ENV = "MAGI_HOSTED_SESSION_REUSE"
MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES_ENV = "MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES"
MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS_ENV = "MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS"


def is_hosted_session_reuse_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for hosted session-service reuse (08-PR5).

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    live runner boundary builds a fresh ``InMemorySessionService`` per turn —
    byte-identical to today, with no registry interaction at all. When ON the
    boundary acquires the session service from a process-scope LRU+TTL registry
    keyed by ``(bot_id_digest, session_id)`` so multiturn context survives
    across turns and the re-sent sanitized history is only used to seed a
    registry miss. Hosted is multitenant — session leakage equals cross-user
    data exposure — so like ``is_hosted_streaming_serve_enabled`` this is an
    additive, default-disabled serving mode and deliberately does NOT follow
    the runtime-profile default-ON convention.
    """
    # Delegate to the canonical config.flags registry; imported lazily to
    # avoid a config<->flags import cycle.
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_HOSTED_SESSION_REUSE_ENV, env=source)


def hosted_session_reuse_max_entries(env: Mapping[str, str] | None = None) -> int:
    """LRU capacity of the hosted session-reuse registry (default 64).

    Invalid values fall back to the registered default; non-positive values
    clamp to 1 so a mis-set cap can never produce an unbounded registry.
    """
    from .flags import flag_int, get_flag

    source = os.environ if env is None else env
    value = flag_int(MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES_ENV, env=source)
    if value is None:
        value = int(get_flag(MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES_ENV).default or 64)
    return max(1, value)


def hosted_session_reuse_ttl_seconds(env: Mapping[str, str] | None = None) -> float:
    """Idle TTL of reusable hosted sessions in seconds (default 1800 = 30min).

    Invalid values fall back to the registered default; non-positive values
    clamp to 1 second so eviction can never be disabled by configuration.
    """
    from .flags import flag_int, get_flag

    source = os.environ if env is None else env
    value = flag_int(MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS_ENV, env=source)
    if value is None:
        value = int(get_flag(MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS_ENV).default or 1800)
    return float(max(1, value))


def is_format_on_write_enabled(env: Mapping[str, str]) -> bool:
    """Single source for the format-after-edit flag.

    When ON, Gate 5B FileWrite/FileEdit/PatchApply run the matching formatter
    on the written file and re-read it so the returned digest reflects the
    formatted content (keeps the model's next edit aligned). Fail-open: a
    missing/failing/timed-out formatter never fails the write.
    """
    return _runtime_feature_enabled(env, "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED")


def parse_gate3a_recorded_replay_env(env: Mapping[str, str]) -> Gate3ARecordedReplayEnv:
    enabled = _is_true(env.get("CORE_AGENT_PYTHON_GATE3A_RECORDED_REPLAY"))
    if not enabled:
        return Gate3ARecordedReplayEnv()

    input_dir_raw = env.get("CORE_AGENT_PYTHON_GATE3A_INPUT_DIR")
    output_dir_raw = env.get("CORE_AGENT_PYTHON_GATE3A_OUTPUT_DIR")

    from magi_agent.shadow.gate3a_replay import validate_gate3a_local_path

    allow_model_calls = _is_true(env.get("CORE_AGENT_PYTHON_GATE3A_ALLOW_MODEL_CALLS"))
    max_bundles_raw = env.get("CORE_AGENT_PYTHON_GATE3A_MAX_BUNDLES") or "1"
    try:
        max_bundles = int(max_bundles_raw)
    except ValueError as exc:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_GATE3A_MAX_BUNDLES must be an integer") from exc
    if max_bundles < 1:
        raise RuntimeEnvError("CORE_AGENT_PYTHON_GATE3A_MAX_BUNDLES must be >= 1")

    return Gate3ARecordedReplayEnv(
        enabled=enabled,
        input_dir=validate_gate3a_local_path(input_dir_raw),
        output_dir=validate_gate3a_local_path(output_dir_raw),
        allow_model_calls=allow_model_calls,
        max_bundles=max_bundles,
    )


READ_QUALITY_FLAG = "MAGI_READ_QUALITY_ENABLED"


def is_read_quality_enabled(env: Mapping[str, str] | None = None) -> bool:
    """PR6 read-tool quality flag. Single source of truth.

    When ON, FileRead output gets 1-indexed line numbers, line/byte caps with an
    'offset=N to continue' footer, binary-file detection, and 'Did you mean?'
    filename suggestions on miss.
    """
    source = os.environ if env is None else env
    return _runtime_feature_enabled(source, READ_QUALITY_FLAG)


_RIPGREP_ENABLED_ENV = "MAGI_RIPGREP_ENABLED"


def ripgrep_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the ``MAGI_RIPGREP_ENABLED`` flag.

    Default ON in the local full runtime profile. When ON, coding-mode Glob/Grep (gate5b full toolhost and the
    local read-only toolhost) prefer the ripgrep backend, falling back to the
    existing Python implementation whenever ``rg`` is unavailable.
    """

    import os as _os

    source = env if env is not None else _os.environ
    return _runtime_feature_enabled(source, _RIPGREP_ENABLED_ENV)


def apply_patch_enabled(env: Mapping[str, str]) -> bool:
    """Single source of truth for the ``MAGI_APPLY_PATCH_ENABLED`` flag.

    Default ON in the local full runtime profile. When ON, gate5b ``PatchApply`` accepts Codex-style envelope
    patches (add/update/delete/move) via the 4-pass matcher in
    ``magi_agent.coding.patch_apply`` and GPT-5-class models are offered
    apply_patch in place of edit/write.
    """

    return _runtime_feature_enabled(env, "MAGI_APPLY_PATCH_ENABLED")


def parse_provider_repair_enabled(env: Mapping[str, str]) -> bool:
    """Whether per-provider tool-schema repair (PR9) is enabled.

    Single source of truth for the ``MAGI_PROVIDER_REPAIR_ENABLED`` flag. Default
    ON in the local full runtime profile. When ON, the ADK tool adapter applies provider-family-keyed schema
    repairs (today: Gemini integer/number/boolean enum -> string enum) to the
    tool declarations exposed to the active model. See
    ``magi_agent.adk_bridge.tool_adapter.apply_provider_repair``.
    """
    return _runtime_feature_enabled(env, "MAGI_PROVIDER_REPAIR_ENABLED")


def parse_trusted_local_shell_enabled(env: Mapping[str, str]) -> bool:
    """Whether read-safe complex shell is allowed in the trusted local scope.

    Single source of truth for the ``MAGI_TRUSTED_LOCAL_SHELL_ENABLED`` flag.
    Default ON in the local full runtime profile. When ON, the first-party local
    coding agent (``selected_full_toolhost`` Bash scope) permits pipe/compound
    shell commands whose every segment is read-only safe (e.g. ``grep ... |
    head``) instead of hard-denying them with ``complex_shell_requires_approval``;
    destructive or opaque segments still deny. Set
    ``MAGI_TRUSTED_LOCAL_SHELL_ENABLED=0`` (or ``MAGI_RUNTIME_PROFILE=safe``) to
    restore the conservative deny-all-complex behavior.
    """
    return _runtime_feature_enabled(env, "MAGI_TRUSTED_LOCAL_SHELL_ENABLED")


def parse_evidence_completion_gate_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_EVIDENCE_COMPLETION_GATE_ENABLED — gates the recipe-materializer runner-policy assembly (pre-final evidence gate / GA / phase routing / policy callback). Default ON; eval mode turns it off."""
    return _runtime_feature_enabled(env, "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED")


def parse_eval_autonomy_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_EVAL_AUTONOMY_ENABLED — when ON (default OFF; enabled by the eval
    profile), appends an eval-specific autonomy + self-verify directive block
    to the CLI system prompt. This instructs the agent to apply every fix by
    editing files, never ask for confirmation, and verify its changes by
    running existing tests before concluding. Default OFF so non-eval sessions
    are byte-identical to origin/main. The eval profile opts in by setting
    ``MAGI_EVAL_AUTONOMY_ENABLED=1`` in ``EVAL_RUNTIME_ENV_DEFAULTS``."""
    return _is_true(env.get("MAGI_EVAL_AUTONOMY_ENABLED"))


# Single source of truth for the compute-via-code directive flag.
MAGI_COMPUTE_VIA_CODE_ENABLED_ENV = "MAGI_COMPUTE_VIA_CODE_ENABLED"


def compute_via_code_enabled(env: Mapping[str, str] | None = None) -> bool:
    """MAGI_COMPUTE_VIA_CODE_ENABLED — when ON (default OFF), appends a general
    agent-hygiene directive to the system prompt instructing the agent to WRITE
    AND RUN code via the existing Bash/Calculation tools for ANY arithmetic,
    unit conversion, statistics, or checksum/validation — and never compute the
    value in its head.

    This is a general capability (not GAIA-specific): in-head arithmetic is a
    measured failure mode even when the agent has working compute tools. Default
    OFF so non-opted-in sessions assemble a byte-identical prompt to
    origin/main. Strict default-OFF via :func:`_is_true` (mirrors
    :func:`parse_eval_autonomy_enabled`)."""
    source = env if env is not None else os.environ
    return _is_true(source.get(MAGI_COMPUTE_VIA_CODE_ENABLED_ENV))


def parse_format_adherence_enabled(env: Mapping[str, str] | None = None) -> bool:
    """MAGI_FORMAT_ADHERENCE_ENABLED — when ON (default OFF), appends a general
    output-format-adherence guidance block to the CLI system prompt. The block
    instructs the agent to re-read the question's explicit output requirements
    (units/scale, rounding precision, requested name/format) before finalizing,
    and to not add unrequested units or words.

    This is a GENERAL agent capability — the block contains no benchmark-specific
    text. Default OFF so non-opted-in sessions are byte-identical to origin/main
    (the ``<output_format_adherence>`` marker is simply absent when the flag is
    unset). Operators/eval profiles opt in by setting
    ``MAGI_FORMAT_ADHERENCE_ENABLED=1``."""
    import os as _os  # noqa: PLC0415

    source = env if env is not None else _os.environ
    return _is_true(source.get("MAGI_FORMAT_ADHERENCE_ENABLED"))


def parse_eval_zero_edit_guard_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED — when ON (default OFF; enabled by
    the eval profile), the engine turn driver re-prompts once with "Apply the
    code change you described above by editing the file(s) now." if a coding
    turn ends without any file-mutating tool call. Prevents the agent from
    describing a fix without applying it. Default OFF so non-eval sessions are
    byte-identical to origin/main. The eval profile opts in by setting
    ``MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED=1`` in ``EVAL_RUNTIME_ENV_DEFAULTS``."""
    return _is_true(env.get("MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED"))


def multi_file_join_enabled(env: Mapping[str, str] | None = None) -> bool:
    """MAGI_MULTI_FILE_JOIN_ENABLED — multi-file cross-reference robustness.

    Strict default-OFF opt-in (only "1"/"true"/"yes"/"on" enable it; like
    :func:`parse_eval_autonomy_enabled` it deliberately does NOT follow the
    runtime-profile default-ON convention). When ON, a domain-neutral
    ``<multi_file_join>`` guidance block is appended to the agent's system
    instruction: after ``ArchiveExtract``, exhaustively enumerate ALL extracted
    files, read structured data (XLSX/XML) in full, and perform the cross-file
    join/dedup PROGRAMMATICALLY via Bash rather than by eye.

    The SAME helper builds the block on both the production CLI/serve path
    (:func:`magi_agent.cli.tool_runtime.build_cli_instruction`) and the GAIA
    bench path (:func:`benchmarks.gaia.harness.run_gaia_question`), so the A/B
    plan measures the lever the flag actually exercises. Default OFF so every
    path is byte-identical to origin/main when unset.
    """
    source = os.environ if env is None else env
    return _is_true(source.get("MAGI_MULTI_FILE_JOIN_ENABLED"))


def parse_recipe_default_packs_expanded(env: Mapping[str, str]) -> bool:
    """MAGI_RECIPE_DEFAULT_PACKS_EXPANDED — stage gate for recipe default-pack
    expansion (doc 05 PR-2 / A1-G1). Default OFF.

    When ON, a *safe* subset of first-party recipe packs
    (``SAFE_DEFAULT_PACK_EXPANSION_IDS`` in ``magi_agent.recipes.compiler``) is
    auto-selected during profile resolution even without an explicit
    task-profile selector. The safe subset is restricted to packs that are
    read-only / idempotent, carry zero production-authority approval gates, and
    declare no live dependency — so turning the gate ON cannot auto-enable any
    side-effecting/authority pack (coding, channel, scheduler, office, etc.).

    OFF (default) keeps the compiled snapshot byte-identical to origin/main:
    only the two ``hardSafety`` packs (``openmagi.context-safety`` /
    ``openmagi.evidence``) are default-selected.
    """
    return _is_true(env.get("MAGI_RECIPE_DEFAULT_PACKS_EXPANDED"))


def parse_recipe_intent_binding_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_RECIPE_INTENT_BINDING_ENABLED — stage gate for binding the
    emit-only recipe intents to runner effects (doc 05 PR-3 / A1-G2). Default
    OFF.

    The four intent families (``provider_intents`` / ``channel_intents`` /
    ``artifact_intents`` / ``scheduler_intents``) are materialized and emitted
    as public-payload metadata but, unlike ``tool_intents``, have no consumer
    driving an actual runner effect. When ON, the local runner-policy route
    selection surfaces hint-level bindings:

    * provider intents  -> model preference hints
    * channel intents   -> channel delivery hints
    * artifact intents  -> artifact delivery requirements (joins pre-final gate)
    * scheduler intents -> scheduler readiness hints (handed to 03-always-on)

    Bindings are intentionally *hint* level — they never assert production-write
    authority and never hard-force a model/channel/provider. Hard enforcement is
    deferred to 14-controlplane. OFF (default) keeps the emitted route selection
    byte-identical to origin/main.
    """
    return _is_true(env.get("MAGI_RECIPE_INTENT_BINDING_ENABLED"))


# Single source of truth for the CLI session-log write path (PR-04-PR1).
CLI_SESSION_LOG_ENABLED_ENV = "MAGI_CLI_SESSION_LOG_ENABLED"


def cli_session_log_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the headless CLI persists a per-turn JSONL transcript.

    Single source of truth for the ``MAGI_CLI_SESSION_LOG_ENABLED`` flag. This
    gates the live drain tap that calls ``SessionLog.append`` for every turn,
    which is the on-disk substrate ``--resume``/``--continue`` rehydration reads.

    Stage-1 default-OFF: unlike most runtime feature flags this is **strict**
    default-OFF (only an explicit truthy value enables it) — it is NOT tied to
    the runtime profile, so a local-full install does not silently start writing
    raw transcripts to disk until the value is flipped on. The local-full / eval
    profiles register the flag at ``"0"`` so a later release can stage it ON.
    """

    import os as _os

    source = env if env is not None else _os.environ
    return _is_true(source.get(CLI_SESSION_LOG_ENABLED_ENV))


# Single source of truth for the CLI ``--resume``/``--continue`` rehydration
# safety net (PR-04-PR2).
CLI_RESUME_ENABLED_ENV = "MAGI_CLI_RESUME_ENABLED"


def cli_resume_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether ``--resume``/``--continue`` rehydrate prior conversation context.

    Single source of truth for the ``MAGI_CLI_RESUME_ENABLED`` flag. When OFF,
    ``--resume``/``--continue`` still thread a session id (and the headless turn
    runs), but no prior transcript is replayed — preserving the pre-PR2 "id only"
    behavior. When ON, the entrypoint calls ``session_log.prepare_resume`` and
    feeds the reconstructed ``initial_messages`` into the engine.

    Stage-1 default-OFF (strict, like ``MAGI_CLI_SESSION_LOG_ENABLED``): only an
    explicit truthy value enables it, independent of the runtime profile. If the
    session-log write path is OFF there is no transcript to read, so resume is a
    graceful no-op regardless of this flag. The local-full / eval profiles
    register the flag at ``"0"`` so a later release can stage it ON.
    """

    import os as _os

    source = env if env is not None else _os.environ
    return _is_true(source.get(CLI_RESUME_ENABLED_ENV))


def tool_concurrency_enabled(env: Mapping[str, str]) -> bool:
    """Single source of truth for the ``MAGI_TOOL_CONCURRENCY_ENABLED`` flag.

    Default ON in the local full runtime profile. When ON, readonly tools (``FileRead``/``Glob``/``Grep``/
    ``GitDiff`` and any manifest whose ``parallel_safety`` is ``"readonly"`` or
    ``"concurrency_safe"``) are dispatched off the event loop via
    ``asyncio.to_thread`` so that the parallelism Google ADK already provides
    (``handle_function_call_list_async`` fans out same-turn function calls with
    ``asyncio.gather``) yields real I/O overlap instead of being serialised by a
    blocking synchronous handler. Workspace-mutating and ``unsafe`` tools are
    never offloaded — they run inline on the event loop thread, preserving the
    write-barrier guarantee. OFF => current fully-inline behaviour (zero
    regression).
    """
    return _runtime_feature_enabled(env, "MAGI_TOOL_CONCURRENCY_ENABLED")


def max_tool_concurrency(env: Mapping[str, str]) -> int:
    """Single source of truth for the ``MAGI_MAX_TOOL_CONCURRENCY`` flag.

    Bounds the number of readonly tool handlers that may run off-thread
    simultaneously when ``MAGI_TOOL_CONCURRENCY_ENABLED`` is ON. Defaults to 8.
    Values below 1 are clamped to 1.
    """
    return max(1, _int_env(env, "MAGI_MAX_TOOL_CONCURRENCY", 8))


def model_aware_prompts_enabled(env: Mapping[str, str]) -> bool:
    """Read ``MAGI_MODEL_AWARE_PROMPTS_ENABLED``.

    Single source of truth for the model-aware prompt feature (PR10 per-model
    coding hints + identity adaptation). Follows the same truthy convention as
    the other ``MAGI_*`` flags (``"1"``/``"true"``/``"yes"``/``"on"``).
    """
    return _runtime_feature_enabled(env, "MAGI_MODEL_AWARE_PROMPTS_ENABLED")


def general_automation_live_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the GA live harness master flag is enabled.

    Single source of truth for ``MAGI_GA_LIVE_ENABLED`` (Track 19). Defaults ON
    in the local full runtime profile. When *env* is ``None`` the process
    environment is consulted.
    """
    if env is None:
        import os

        env = os.environ
    return _runtime_feature_enabled(env, "MAGI_GA_LIVE_ENABLED")


def plan_act_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the plan_act runner-wiring gate is explicitly enabled.

    Single source of truth for ``MAGI_PLAN_ACT_GATE_ENABLED`` (cluster 06 PR4 /
    inventory B9). This is a **strict default-OFF** gate: unlike the
    profile-aware ``MAGI_*_ENABLED`` flags, it never defaults ON in the full
    runtime profile. It only flips to ``True`` for an explicit truthy value
    (``"1"``/``"true"``/``"yes"``/``"on"``), so the GA
    ``plan_gate -> plan_act_switch -> delegation`` chain stays inert (and
    byte-identical to ``main``) unless an operator opts in.
    """
    if env is None:
        import os as _os

        env = _os.environ
    return _is_true(env.get("MAGI_PLAN_ACT_GATE_ENABLED"))


def parse_ga_deliverable_gate_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_GA_DELIVERABLE_GATE_ENABLED — GA deliverable completion gate (A4).

    Promotes the Track 19 PR3 General-Automation deliverable check (an artifact
    receipt must exist before finalise) onto the LIVE pre-final evidence gate in
    ``cli.engine`` and keeps ``localArtifactReceipt`` visible in the local tool
    evidence projection so a delivered artifact satisfies the gate. This is a
    **strict default-OFF** gate: it never defaults ON in any runtime profile and
    only flips for an explicit truthy value, so flag-OFF behavior stays
    byte-identical to ``main``.
    """
    from .flags import flag_bool

    return flag_bool("MAGI_GA_DELIVERABLE_GATE_ENABLED", env=env)


def parse_fact_grounding_verification_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_FACT_GROUNDING_VERIFICATION_ENABLED — semantic grounding gate.

    Wires the deterministic ``evaluate_answer_grounding`` detector into the live
    pre-final evidence gate in ``cli.engine``: when ON, a research answer that
    asserts a specific numeric/identifier value NOT present in the opened-source
    corpus stays ungrounded and the bare ``fact_grounding`` required-validator is
    left unsatisfied, so the gate blocks. This is a **strict default-OFF** gate:
    it never defaults ON in any runtime profile and only flips for an explicit
    truthy value, so flag-OFF behavior stays byte-identical to ``main`` (the
    satisfier is inert and the existing ``fact_grounding`` label behaves exactly
    as it does today).
    """
    from .flags import flag_bool

    return flag_bool("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", env=env)


def parse_source_ledger_evidence_gate_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED — live source-ledger evidence ref.

    Projects the live turn's inspected-source ledger into the engine's harvested
    public refs as the NAMED ref ``verifier:research-source-evidence`` (mirroring
    the ``research/research_first_canary`` projection). When ON, a recipe whose
    final gate requires that named ref is satisfied by any turn that actually
    read at least one source, and blocks a turn that read none — so the gate can
    require source grounding without false-blocking. This is a **strict
    default-OFF** gate: it never defaults ON in any runtime profile and only
    flips for an explicit truthy value, so flag-OFF behavior stays byte-identical
    to ``main`` (today only ``sha256:`` receipts reach the harvest; the named ref
    is never emitted on the live path, so the projector is inert when OFF).
    """
    from .flags import flag_bool

    return flag_bool("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", env=env)


def parse_taskboard_completion_verification_enabled(env: Mapping[str, str]) -> bool:
    """MAGI_VERIFY_TASKBOARD_COMPLETION — block completion while tasks remain.

    When ON, a turn whose workspace ``.magi/taskboard.jsonl`` still has a task in
    a non-terminal status (the latest record per title) is blocked at the
    pre-final gate. Strict **default-OFF**: inert unless explicitly set (or the
    task-board-completion Customize preset is enabled), so flag-OFF behavior is
    byte-identical (no taskboard read happens).
    """
    from .flags import flag_bool

    return flag_bool("MAGI_VERIFY_TASKBOARD_COMPLETION", env=env)


MAGI_GATE5B_GOVERNANCE_ENABLED_ENV = "MAGI_GATE5B_GOVERNANCE_ENABLED"


def is_gate5b_governance_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the gate5b-governance enablement flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    gate5b user-visible serving path is byte-identical to today: the live runner
    boundary builds its Agent/Runner with NO control-plane plugin and the
    serving boundary runs NO pre-final evidence/fact-grounding gate. When ON it
    activates the cli/engine-parity wiring on the gate5b path — the control-plane
    plugin (each control still behind its OWN existing flag) is attached to the
    gate5b runner, and a pre-final fact-grounding/evidence check runs over the
    turn's collected tool evidence before the user-visible response is emitted.
    Like ``is_egress_gate_enabled`` this deliberately does NOT follow the
    runtime-profile default-ON convention — it is an additive, default-disabled
    master switch for the gate5b governance wiring.
    """
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_GATE5B_GOVERNANCE_ENABLED_ENV, env=source)


def plan_mode_tools_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the manifest-routed plan-mode tools are explicitly enabled.

    Single source of truth for ``MAGI_PLAN_MODE_TOOLS_ENABLED`` (inventory B14 /
    doc 12 PR2). This gate advertises the catalog ``AskUserQuestion`` /
    ``EnterPlanMode`` / ``ExitPlanMode`` tools to the model by routing them to
    their EXISTING General-Automation implementations
    (:mod:`magi_agent.harness.general_automation.question_tool` /
    :mod:`~magi_agent.harness.general_automation.plan_act_switch`).

    Like :func:`plan_act_gate_enabled` this is a **strict default-OFF** gate: it
    never defaults ON in the full runtime profile and flips to ``True`` only for
    an explicit truthy value (``"1"``/``"true"``/``"yes"``/``"on"``). When OFF
    the three tools stay manifest-only (no handler bound, not advertised), so
    exposure is byte-identical to ``main``.
    """
    if env is None:
        import os as _os

        env = _os.environ
    return _is_true(env.get("MAGI_PLAN_MODE_TOOLS_ENABLED"))


def document_qa_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the question-conditioned DocumentQA sidecar tool is enabled.

    Single source of truth for ``MAGI_DOCUMENT_QA_ENABLED``. Like
    :func:`plan_mode_tools_enabled` this is a **strict default-OFF** gate: it
    never defaults ON in any runtime profile (the outer
    ``MAGI_FILE_TOOLS_ENABLED`` suite gate is profile-default-ON locally, so
    riding only that gate would silently flip the new tool ON for local users)
    and flips to ``True`` only for an explicit truthy value. When OFF the
    ``DocumentQA`` manifest is not registered and no handler is bound, so
    registry contents stay byte-identical to before.
    """
    from .flags import flag_bool

    return flag_bool("MAGI_DOCUMENT_QA_ENABLED", env=env)


def is_message_cache_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the message-tail prompt-cache flag.

    Reads ``MAGI_MESSAGE_CACHE_ENABLED``. When enabled, the
    runtime may mark the last ~2 non-system conversation messages with an
    Anthropic ``cache_control: {type: ephemeral}`` marker so the growing
    conversation tail is cached in addition to the system prefix.

    Args:
        env: Optional environment mapping. Defaults to ``os.environ`` so the
            flag can be evaluated against the live process environment.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    return _runtime_feature_enabled(source, "MAGI_MESSAGE_CACHE_ENABLED")


def file_tools_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the ``MAGI_FILE_TOOLS_ENABLED`` flag.

    Default ON in the local full runtime profile. When ON, the four file/multimodal tools (XLSXRead,
    DocumentRead, ImageUnderstand, AudioTranscribe) are registered in the tool
    registry and exposed via ``build_cli_adk_tools``. Requires the ``files``
    (and optionally ``audio``) optional extras to be installed; handlers
    degrade gracefully with ``status="blocked"`` when their optional dependency
    is missing rather than crashing.
    """
    if env is None:
        import os as _os

        env = _os.environ
    return _runtime_feature_enabled(env, "MAGI_FILE_TOOLS_ENABLED")


def browser_tool_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the autonomous browser tool gate.

    Returns True iff the runtime profile enables ``MAGI_BROWSER_TOOL_ENABLED``
    AND the ``MAGI_BROWSER_TOOL_KILL_SWITCH`` is NOT truthy. The kill-switch
    always wins, so an operator can disable the tool fleet-wide even when the
    enable flag or full/local profile is active.

    Default ON in the local full runtime profile. When ON, the ``BrowserTask``
    tool is registered and bound. The handler degrades with ``status="blocked"``
    when the optional dependency is missing rather than crashing.
    """
    if env is None:
        import os as _os

        env = _os.environ
    return _runtime_feature_enabled(env, "MAGI_BROWSER_TOOL_ENABLED") and not _is_true(
        env.get("MAGI_BROWSER_TOOL_KILL_SWITCH")
    )


MAGI_CODE_ACTION_ENABLED_ENV = "MAGI_CODE_ACTION_ENABLED"


def code_action_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the persistent PythonExec code-action gate.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    ``PythonExec`` tool module is never imported and the tool is absent from
    the registry, manifests, and the advertised instruction — byte-identical
    to before. When ON, a persistent per-session Python interpreter tool is
    registered (variables/imports survive across calls in one session). Like
    ``is_egress_gate_enabled`` this deliberately does NOT follow the
    runtime-profile default-ON convention — it is an additive,
    default-disabled seam.
    """
    # Delegate to the canonical config.flags registry. Imported lazily to
    # avoid a config<->flags import cycle.
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_CODE_ACTION_ENABLED_ENV, env=source)


MAGI_PERSISTENT_PYTHON_ENABLED_ENV = "MAGI_PERSISTENT_PYTHON_ENABLED"


def persistent_python_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the ``PersistentPython`` pack runtime gate.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    ``tools_persistent_python`` pack's manifest is NOT registered into the CLI
    runtime registry and its additive first-party handler binder is never
    invoked — byte-identical to before. When ON, the pack manifest is registered
    and ``bind_persistent_python_handler`` attaches the persistent-namespace
    Python handler (CodeAct: variables/imports survive across steps within a
    turn). The pack remains independently removable via ``config.toml [packs]
    disable``; this gate only governs the runtime build-path wiring. Like
    ``code_action_enabled`` this is an additive, default-disabled seam and
    deliberately does NOT follow the runtime-profile default-ON convention.
    """
    from .flags import flag_bool

    source = os.environ if env is None else env
    return flag_bool(MAGI_PERSISTENT_PYTHON_ENABLED_ENV, env=source)


MAGI_PERMISSION_SCOPE_FROM_MODE_ENV = "MAGI_PERMISSION_SCOPE_FROM_MODE"


def permission_scope_from_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the mode-derived permission-scope gate.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the CLI
    tool runtime keeps stamping the legacy hardcoded
    ``permission_scope={"mode": "selected_full_toolhost", ...}`` onto every
    ``ToolContext`` — byte-identical to before. When ON, the scope is derived
    from the active permission mode via
    :class:`magi_agent.tools.permission_scope.PermissionScopeResolver`, so the
    ``default`` mode no longer preapproves mutating tools and the arbiter "ask"
    branch can actually be reached. Like ``is_egress_gate_enabled`` this is an
    additive, default-disabled seam and deliberately does NOT follow the
    runtime-profile default-ON convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_PERMISSION_SCOPE_FROM_MODE_ENV))


MAGI_CONTROL_STORE_DURABLE_ENV = "MAGI_CONTROL_STORE_DURABLE"
MAGI_CONTROL_STORE_PATH_ENV = "MAGI_CONTROL_STORE_PATH"


def control_store_durable_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the durable ControlRequestStore gate (A7).

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the CLI
    permission gate keeps using the volatile in-memory
    :class:`magi_agent.runtime.control.ControlRequestStore` — byte-identical to
    before, and pending approvals are lost on process exit. When ON, the gate
    swaps in
    :class:`magi_agent.runtime.durable_control_store.DurableControlRequestStore`,
    which appends every lifecycle mutation to an append-only JSONL log and
    replays it on startup so out-of-band / always-on approvals survive a
    restart. Like ``permission_scope_from_mode_enabled`` this is an additive,
    default-disabled seam and deliberately does NOT follow the runtime-profile
    default-ON convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_CONTROL_STORE_DURABLE_ENV))


def control_store_durable_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the JSONL log path for the durable ControlRequestStore.

    Returns ``None`` when ``MAGI_CONTROL_STORE_PATH`` is unset/blank so the
    caller can fall back to its own default location. The path is returned
    as-is (not created) — the durable store creates parent directories lazily
    on first write.
    """
    source = os.environ if env is None else env
    raw = (source.get(MAGI_CONTROL_STORE_PATH_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw)


MAGI_CONTROL_STORE_OOB_RESOLVE_ENV = "MAGI_CONTROL_STORE_OOB_RESOLVE"


def control_store_oob_resolve_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the out-of-band control-resolve gate (A7 / PR-5).

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). Building on the
    durable JSONL queue (see :func:`control_store_durable_enabled`), this gate
    governs whether the out-of-band resolve seam in
    :mod:`magi_agent.runtime.control_oob` is exposed to external callers (a
    channel / gateway daemon / dashboard approving a pending request from a
    *separate* process). When OFF the seam is dormant and no behaviour changes —
    pending approvals are still only resolvable by the in-turn CLI gate. When ON,
    an external resolve is appended to the durable log and the originating
    process consumes it on its next queue refresh. Like
    ``control_store_durable_enabled`` this is an additive, default-disabled seam
    and deliberately does NOT follow the runtime-profile default-ON convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_CONTROL_STORE_OOB_RESOLVE_ENV))


MAGI_COMPOSIO_DISPATCH_ENFORCED_ENV = "MAGI_COMPOSIO_DISPATCH_ENFORCED"


def composio_dispatch_enforced(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for routing composio MCP tools through the
    dispatcher hard-safety arbiter.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    composio toolsets are attached directly to ``agent.tools`` (legacy ADK MCP
    path) — byte-identical to before — so they only see the agent-level
    ``RulesPermissionGate`` callback and bypass the
    :class:`magi_agent.tools.safety.RuntimePermissionArbiter` (secret / sealed /
    workspace-escape invariants). When ON, each composio tool call is wrapped so
    it first passes through the arbiter's hard-safety check (a deny blocks the
    call before the MCP body runs). Like ``permission_scope_from_mode_enabled``
    this is an additive, default-disabled security seam and deliberately does
    NOT follow the runtime-profile default-ON convention.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_COMPOSIO_DISPATCH_ENFORCED_ENV))


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


# ---------------------------------------------------------------------------
# Native receipt honesty (cluster 13 D2)
# ---------------------------------------------------------------------------
# Master gate for the "honest-by-default, live-when-backed" native handler
# behaviour. When enabled (the default), receipt-theater handlers that have no
# real backing return a blocked ``*_not_configured`` error instead of a fake
# ``status: ok`` digest the model would mis-report as a real state change. Set
# MAGI_NATIVE_RECEIPTS_HONEST=0 to restore the legacy fake-ok behaviour
# (rollback safety valve).
NATIVE_RECEIPTS_HONEST_ENV = "MAGI_NATIVE_RECEIPTS_HONEST"


def native_receipts_honest(env: Mapping[str, str] | None = None) -> bool:
    if env is None:
        import os as _os

        env = _os.environ
    return _env_bool_default_true(env.get(NATIVE_RECEIPTS_HONEST_ENV))


def _runtime_profile_default_enabled(env: Mapping[str, str]) -> bool:
    profile = (env.get(RUNTIME_PROFILE_ENV) or "").strip().lower()
    return profile not in _SAFE_RUNTIME_PROFILES


def _runtime_feature_enabled(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name)
    if value is None:
        return _runtime_profile_default_enabled(env)
    normalized = value.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    return _runtime_profile_default_enabled(env)


def _env_bool_default_true(value: str | None) -> bool:
    if value is None:
        return True
    normalized = (value or "").strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    return True


def _trimmed(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    value = _trimmed(env.get(name))
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeEnvError(f"{name} must be an integer") from exc


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    value = _trimmed(env.get(name))
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeEnvError(f"{name} must be a number") from exc


def _first_present_name(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        if env.get(name) is not None:
            return name
    return None


def _first_non_empty(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value and value.strip():
            return value.strip()
    return None
