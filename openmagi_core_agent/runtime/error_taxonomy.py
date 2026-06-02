from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Literal


ErrorCategory = Literal[
    "provider_error",
    "timeout",
    "validator_block",
    "redaction_failure",
    "route_disabled",
    "kill_switch",
    "budget_exceeded",
    "model_routing_invalid",
    "runner_exception",
    "user_interrupt",
    "context_overflow",
    "empty_output",
    "truncation",
]

DecisionAction = Literal["restore_typescript", "fail_closed", "retry_python"]
FallbackTarget = Literal["typescript_runtime"]


@dataclass(frozen=True)
class ErrorTaxonomyEntry:
    category: ErrorCategory
    restore_to_typescript: bool
    fail_closed: bool
    retryable: bool
    default_python_retries: int = 0


@dataclass(frozen=True)
class ErrorClassification:
    category: ErrorCategory
    ts_error_code: str
    source: Literal["adk_runtime"] = "adk_runtime"
    retryable: bool = False
    restore_to_typescript: bool = False
    fail_closed: bool = False


@dataclass(frozen=True)
class RetryFallbackPolicy:
    enabled: bool = False
    allow_typescript_restore: bool = True


@dataclass(frozen=True)
class RetryFallbackDecision:
    action: DecisionAction
    category: ErrorCategory
    ts_error_code: str
    fail_open_to_typescript: bool
    fail_closed: bool
    python_retry_allowed: bool
    python_max_retries: int
    fallback_target: FallbackTarget | None = None


TAXONOMY: dict[ErrorCategory, ErrorTaxonomyEntry] = {
    "provider_error": ErrorTaxonomyEntry(
        "provider_error",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=True,
    ),
    "timeout": ErrorTaxonomyEntry(
        "timeout",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=True,
    ),
    "validator_block": ErrorTaxonomyEntry(
        "validator_block",
        restore_to_typescript=False,
        fail_closed=True,
        retryable=False,
    ),
    "redaction_failure": ErrorTaxonomyEntry(
        "redaction_failure",
        restore_to_typescript=False,
        fail_closed=True,
        retryable=False,
    ),
    "route_disabled": ErrorTaxonomyEntry(
        "route_disabled",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=False,
    ),
    "kill_switch": ErrorTaxonomyEntry(
        "kill_switch",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=False,
    ),
    "budget_exceeded": ErrorTaxonomyEntry(
        "budget_exceeded",
        restore_to_typescript=False,
        fail_closed=True,
        retryable=False,
    ),
    "model_routing_invalid": ErrorTaxonomyEntry(
        "model_routing_invalid",
        restore_to_typescript=False,
        fail_closed=True,
        retryable=False,
    ),
    "runner_exception": ErrorTaxonomyEntry(
        "runner_exception",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=False,
    ),
    "user_interrupt": ErrorTaxonomyEntry(
        "user_interrupt",
        restore_to_typescript=False,
        fail_closed=True,
        retryable=False,
    ),
    "context_overflow": ErrorTaxonomyEntry(
        "context_overflow",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=True,
    ),
    "empty_output": ErrorTaxonomyEntry(
        "empty_output",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=True,
    ),
    "truncation": ErrorTaxonomyEntry(
        "truncation",
        restore_to_typescript=True,
        fail_closed=False,
        retryable=True,
    ),
}


_DIRECT_ALIASES: dict[str, ErrorCategory] = {
    "provider_error": "provider_error",
    "model_provider_error": "provider_error",
    "rate_limited": "provider_error",
    "http_429": "provider_error",
    "http_500": "provider_error",
    "http_502": "provider_error",
    "http_503": "provider_error",
    "http_504": "provider_error",
    "timeout": "timeout",
    "timed_out": "timeout",
    "runner_timeout": "timeout",
    "validator_block": "validator_block",
    "verifier_blocked": "validator_block",
    "before_commit_blocked": "validator_block",
    "structured_output_invalid": "validator_block",
    "redaction_failure": "redaction_failure",
    "redaction_failed": "redaction_failure",
    "redaction_not_verified": "redaction_failure",
    "redaction_verification_failed": "redaction_failure",
    "redaction_violation": "redaction_failure",
    "route_disabled": "route_disabled",
    "chat_route_disabled": "route_disabled",
    "python_route_disabled": "route_disabled",
    "kill_switch": "kill_switch",
    "kill_switch_enabled": "kill_switch",
    "kill_switch_active": "kill_switch",
    "budget_exceeded": "budget_exceeded",
    "session_budget_exceeded": "budget_exceeded",
    "cost_budget_exhausted": "budget_exceeded",
    "queue_budget_exhausted": "budget_exceeded",
    "daily_budget_exhausted": "budget_exceeded",
    "input_token_budget_exceeded": "budget_exceeded",
    "total_token_budget_exceeded": "budget_exceeded",
    "model_routing_invalid": "model_routing_invalid",
    "invalid_model": "model_routing_invalid",
    "unsupported_model": "model_routing_invalid",
    "missing_model": "model_routing_invalid",
    "runner_exception": "runner_exception",
    "runner_error": "runner_exception",
    "user_interrupt": "user_interrupt",
    "user_interrupt_handoff": "user_interrupt",
    "turn_cancelled": "user_interrupt",
    "turn_canceled": "user_interrupt",
    "cancelled": "user_interrupt",
    "canceled": "user_interrupt",
    "aborterror": "user_interrupt",
    "abort_error": "user_interrupt",
    "context_overflow": "context_overflow",
    "http_413": "context_overflow",
    "empty_output": "empty_output",
    "empty_response": "empty_output",
    "empty_response_retry_exhausted": "empty_output",
    "no_visible_text": "empty_output",
    "truncation": "truncation",
    "truncated": "truncation",
    "max_tokens": "truncation",
    "length": "truncation",
}

_CONTEXT_OVERFLOW_RE = re.compile(
    r"prompt is too long|max_tokens_exceeded|context_length_exceeded|"
    r"request entity too large|input.*too (?:long|large)|"
    r"exceeds.*context|maximum context length",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(r"\b(timed?\s*out|timeout|etimedout)\b", re.IGNORECASE)
_PROVIDER_RE = re.compile(
    r"\b(provider|upstream|rate.?limit|429|500|502|503|504|"
    r"econnreset|epipe|und_err|socket hang up|network error|fetch failed|"
    r"premature close|terminated|abort(?:ed)?)\b",
    re.IGNORECASE,
)
_MODEL_ROUTE_RE = re.compile(
    r"\b(model routing invalid|unsupported model|invalid model|missing model|"
    r"no turn route|model_selection)\b",
    re.IGNORECASE,
)
_REDACTION_RE = re.compile(
    r"(?:^|[^a-z0-9])redaction(?:[^a-z0-9]+)?(?:failure|failed|violation)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_VALIDATOR_RE = re.compile(
    r"\b(validator|verifier|before_commit_blocked|structured output invalid|"
    r"runtime verifier blocked)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(r"\b(user_interrupt|cancelled|canceled)\b", re.IGNORECASE)
_ABORT_ERROR_RE = re.compile(r"\b(?:abort\s*error|aborterror)\b", re.IGNORECASE)
_REDACTION_FAILURE_MARKERS = frozenset(
    {"failed", "failure", "unverified", "verification", "violation"}
)
_VALIDATOR_MARKERS = frozenset({"validator", "verifier"})
_VALIDATOR_BLOCK_SEQUENCES = (
    ("before", "commit", "blocked"),
    ("runtime", "verifier", "blocked"),
    ("structured", "output", "invalid"),
)


def classify_adk_runtime_failure(
    *,
    code: object | None = None,
    message: object | None = None,
    reason: object | None = None,
    exception: BaseException | None = None,
    runner_output: Mapping[str, object] | None = None,
) -> ErrorClassification:
    category = _classify_category(
        code=code,
        message=message,
        reason=reason,
        exception=exception,
        runner_output=runner_output,
    )
    entry = TAXONOMY[category]
    return ErrorClassification(
        category=category,
        ts_error_code=category,
        retryable=entry.retryable,
        restore_to_typescript=entry.restore_to_typescript,
        fail_closed=entry.fail_closed,
    )


def decide_retry_fallback(
    classification: ErrorClassification,
    *,
    policy: RetryFallbackPolicy | None = None,
) -> RetryFallbackDecision:
    active_policy = policy or RetryFallbackPolicy()
    entry = TAXONOMY[classification.category]
    python_max_retries = (
        entry.default_python_retries
        if active_policy.enabled and entry.retryable
        else 0
    )
    python_retry_allowed = python_max_retries > 0

    if python_retry_allowed:
        return RetryFallbackDecision(
            action="retry_python",
            category=classification.category,
            ts_error_code=classification.ts_error_code,
            fail_open_to_typescript=False,
            fail_closed=False,
            python_retry_allowed=True,
            python_max_retries=python_max_retries,
        )

    if entry.restore_to_typescript and active_policy.allow_typescript_restore:
        return RetryFallbackDecision(
            action="restore_typescript",
            category=classification.category,
            ts_error_code=classification.ts_error_code,
            fail_open_to_typescript=True,
            fail_closed=False,
            fallback_target="typescript_runtime",
            python_retry_allowed=False,
            python_max_retries=0,
        )

    return RetryFallbackDecision(
        action="fail_closed",
        category=classification.category,
        ts_error_code=classification.ts_error_code,
        fail_open_to_typescript=False,
        fail_closed=True,
        python_retry_allowed=False,
        python_max_retries=0,
    )


def _classify_category(
    *,
    code: object | None,
    message: object | None,
    reason: object | None,
    exception: BaseException | None,
    runner_output: Mapping[str, object] | None,
) -> ErrorCategory:
    output_values = _mapping_signal_values(runner_output)
    signal_values = [
        _safe_text(code),
        _safe_text(reason),
        *output_values,
        _exception_name(exception),
        _safe_text(message),
        _safe_text(exception),
    ]
    joined = " ".join(value for value in signal_values if value)

    unsafe = _unsafe_category(signal_values, joined)
    if unsafe is not None:
        return unsafe

    if _has_abort_error_signal(signal_values, joined):
        return "user_interrupt"

    direct = _direct_category(signal_values)
    if direct is not None:
        return direct

    if exception is not None:
        exception_name = type(exception).__name__
        if isinstance(exception, (KeyboardInterrupt, InterruptedError)):
            return "user_interrupt"
        if exception_name in {"CancelledError", "AbortError"}:
            return "user_interrupt"
        if isinstance(exception, TimeoutError):
            return "timeout"

    if _runner_output_empty(runner_output):
        return "empty_output"
    if _runner_output_truncated(runner_output):
        return "truncation"

    if _REDACTION_RE.search(joined):
        return "redaction_failure"
    if _VALIDATOR_RE.search(joined):
        return "validator_block"
    if _CONTEXT_OVERFLOW_RE.search(joined):
        return "context_overflow"
    if _TIMEOUT_RE.search(joined):
        return "timeout"
    if _MODEL_ROUTE_RE.search(joined):
        return "model_routing_invalid"
    if _CANCEL_RE.search(joined):
        return "user_interrupt"
    if _PROVIDER_RE.search(joined):
        return "provider_error"
    if exception is not None:
        return "runner_exception"
    return "runner_exception"


def _direct_category(values: list[str]) -> ErrorCategory | None:
    for value in values:
        normalized = _normalize_token(value)
        if normalized in _DIRECT_ALIASES:
            return _DIRECT_ALIASES[normalized]
    return None


def _unsafe_category(values: list[str], joined: str) -> ErrorCategory | None:
    for value in values:
        category = _DIRECT_ALIASES.get(_normalize_token(value))
        if category == "redaction_failure" or _has_redaction_failure_signal(value):
            return "redaction_failure"
    if _has_redaction_failure_signal(joined) or _REDACTION_RE.search(joined):
        return "redaction_failure"

    for value in values:
        category = _DIRECT_ALIASES.get(_normalize_token(value))
        if category == "validator_block" or _has_validator_block_signal(value):
            return "validator_block"
    if _has_validator_block_signal(joined) or _VALIDATOR_RE.search(joined):
        return "validator_block"

    return None


def _has_redaction_failure_signal(value: str) -> bool:
    tokens = _normalized_tokens(value)
    for index, token in enumerate(tokens):
        if token != "redaction":
            continue
        tail = tokens[index + 1 :]
        if any(marker in tail for marker in _REDACTION_FAILURE_MARKERS):
            return True
        if _contains_token_sequence(tail, ("not", "verified")):
            return True
    return False


def _has_validator_block_signal(value: str) -> bool:
    tokens = _normalized_tokens(value)
    if any(token in _VALIDATOR_MARKERS for token in tokens):
        return True
    return any(
        _contains_token_sequence(tokens, sequence)
        for sequence in _VALIDATOR_BLOCK_SEQUENCES
    )


def _contains_token_sequence(tokens: list[str], sequence: tuple[str, ...]) -> bool:
    if len(tokens) < len(sequence):
        return False
    for start in range(len(tokens) - len(sequence) + 1):
        if tuple(tokens[start : start + len(sequence)]) == sequence:
            return True
    return False


def _has_abort_error_signal(values: list[str], joined: str) -> bool:
    for value in values:
        if _normalize_token(value) in {"abort_error", "aborterror"}:
            return True
    return _ABORT_ERROR_RE.search(joined) is not None


def _mapping_signal_values(value: Mapping[str, object] | None) -> list[str]:
    if value is None:
        return []
    fields = (
        "code",
        "error",
        "errorCode",
        "error_code",
        "errorClass",
        "error_class",
        "reason",
        "status",
        "message",
        "errorMessage",
        "error_message",
        "stopReason",
        "stop_reason",
        "finishReason",
        "finish_reason",
    )
    values: list[str] = []
    for field in fields:
        current = value.get(field)
        text = _safe_text(current)
        if text:
            values.append(text)
    return values


def _runner_output_empty(value: Mapping[str, object] | None) -> bool:
    if value is None:
        return False
    stop_reason = _normalize_token(
        _safe_text(value.get("stopReason") or value.get("stop_reason"))
    )
    if stop_reason not in {"end_turn", "stop_sequence"}:
        return False
    text = _safe_text(
        value.get("text")
        or value.get("output")
        or value.get("content")
        or value.get("message")
    )
    return text.strip() == ""


def _runner_output_truncated(value: Mapping[str, object] | None) -> bool:
    if value is None:
        return False
    if value.get("truncated") is True:
        return True
    finish_reason = _normalize_token(
        _safe_text(
            value.get("finishReason")
            or value.get("finish_reason")
            or value.get("stopReason")
            or value.get("stop_reason")
        )
    )
    return finish_reason in {"length", "max_tokens", "truncated", "content_filter_truncated"}


def _safe_text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, str):
        return value
    return str(value)


def _exception_name(exception: BaseException | None) -> str:
    if exception is None:
        return ""
    return type(exception).__name__


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _normalized_tokens(value: str) -> list[str]:
    normalized = _normalize_token(value)
    if not normalized:
        return []
    return [token for token in normalized.split("_") if token]


__all__ = [
    "DecisionAction",
    "ErrorCategory",
    "ErrorClassification",
    "ErrorTaxonomyEntry",
    "FallbackTarget",
    "RetryFallbackDecision",
    "RetryFallbackPolicy",
    "TAXONOMY",
    "classify_adk_runtime_failure",
    "decide_retry_fallback",
]
