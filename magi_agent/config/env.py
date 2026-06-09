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
_SAFE_RUNTIME_PROFILES = frozenset({"safe", "off", "minimal", "conservative"})

# ---------------------------------------------------------------------------
# Coding: edit fuzzy-match flag
# ---------------------------------------------------------------------------
# When set to "1" or "true", gate5b FileEdit uses the 9-stage fuzzy-match
# cascade (magi_agent.coding.edit_matching) instead of exact-only matching.
# Default: ON in the local full runtime profile; set
# MAGI_EDIT_FUZZY_MATCH_ENABLED=0 or MAGI_RUNTIME_PROFILE=safe for conservative
# runs.
MAGI_EDIT_FUZZY_MATCH_ENABLED: bool = (
    (
        os.environ.get("MAGI_EDIT_FUZZY_MATCH_ENABLED")
        if os.environ.get("MAGI_EDIT_FUZZY_MATCH_ENABLED") is not None
        else (
            "0"
            if (os.environ.get(RUNTIME_PROFILE_ENV) or "").strip().lower()
            in _SAFE_RUNTIME_PROFILES
            else "1"
        )
    )
    .strip()
    .lower()
    in _TRUE_VALUES
)

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


@dataclass(frozen=True)
class ContextCompactionEnv:
    enabled: bool = False
    token_threshold: int = _COMPACTION_TOKEN_THRESHOLD_DEFAULT
    tail_events: int = _COMPACTION_TAIL_EVENTS_DEFAULT


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
    return ContextCompactionEnv(
        enabled=enabled,
        token_threshold=token_threshold,
        tail_events=tail_events,
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
        _trimmed(
            env.get("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES")
        )
        or ""
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

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    ``InspectSelfEvidence`` tool is bound-but-not-advertised so the model never
    sees it. This deliberately does NOT follow the runtime-profile default-ON
    convention — introspection is an additive, default-disabled seam.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_SELF_INTROSPECTION_ENABLED_ENV))


MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV = "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"


def is_evidence_ledger_lifecycle_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single source of truth for the per-turn EvidenceLedger lifecycle flag.

    Default OFF (strict truthy opt-in: "1"/"true"/"yes"/"on"). When OFF the
    local tool-evidence collector builds NO ``EvidenceLedger`` objects and the
    CLI tool-context factories leave ``source_ledger`` byte-identical to today
    (an empty tuple), so ``InspectSelfEvidence`` keeps returning empty
    ``tool_calls``. When ON the collector synthesizes a minimal per-turn
    ``EvidenceLedger`` from each recorded tool result and the factories thread
    those ledgers onto ``ToolContext.source_ledger`` so the tool reports REAL
    tool calls. Like ``is_self_introspection_enabled`` this deliberately does
    NOT follow the runtime-profile default-ON convention — it is an additive,
    default-disabled seam.
    """
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV))


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
    source = os.environ if env is None else env
    return _is_true(source.get(MAGI_EGRESS_GATE_ENABLED_ENV))


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
    return _is_true(source.get(MAGI_DOCUMENT_AUTHORING_COVERAGE_ENV))


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

    Returns True iff ``MAGI_BROWSER_TOOL_ENABLED`` is truthy AND the
    ``MAGI_BROWSER_TOOL_KILL_SWITCH`` is NOT truthy. The kill-switch always
    wins, so an operator can disable the tool fleet-wide even when the enable
    flag is set.

    Default OFF. When ON (and the ``browser`` extra is installed), the
    ``BrowserTask`` tool is registered and bound. The handler degrades with
    ``status="blocked"`` when the optional dependency is missing rather than
    crashing.
    """
    if env is None:
        import os as _os

        env = _os.environ
    return _is_true(env.get("MAGI_BROWSER_TOOL_ENABLED")) and not _is_true(
        env.get("MAGI_BROWSER_TOOL_KILL_SWITCH")
    )


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


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
