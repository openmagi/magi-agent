from __future__ import annotations

import pytest

from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
    TerminalError,
)
from openmagi_core_agent.runtime.error_recovery.classifier import ErrorClassifier


# ---------------------------------------------------------------------------
# prompt_too_long detection
# ---------------------------------------------------------------------------


class TestPromptTooLong:
    def test_anthropic_prompt_too_long(self) -> None:
        result = ErrorClassifier.classify("prompt is too long")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_openai_context_length_exceeded(self) -> None:
        result = ErrorClassifier.classify("context_length_exceeded")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_http_413_status(self) -> None:
        result = ErrorClassifier.classify({"error": "too large", "status": 413})
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG
        assert result.http_status == 413

    def test_max_tokens_exceeded_message(self) -> None:
        result = ErrorClassifier.classify("max_tokens_exceeded: input exceeds limit")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_google_request_entity_too_large(self) -> None:
        result = ErrorClassifier.classify("Request entity too large")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_maximum_context_length(self) -> None:
        result = ErrorClassifier.classify(
            "This model's maximum context length is 200000 tokens. "
            "However, your messages resulted in 218005 tokens."
        )
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_tokens_over_extraction(self) -> None:
        result = ErrorClassifier.classify(
            "This model's maximum context length is 200000 tokens. "
            "However, your messages resulted in 218005 tokens."
        )
        assert isinstance(result, RecoverableError)
        assert result.tokens_over == 18005


# ---------------------------------------------------------------------------
# max_output_tokens detection
# ---------------------------------------------------------------------------


class TestMaxOutputTokens:
    def test_finish_reason_length(self) -> None:
        result = ErrorClassifier.classify({"finish_reason": "length"})
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MAX_OUTPUT_TOKENS

    def test_stop_reason_max_tokens(self) -> None:
        result = ErrorClassifier.classify({"stop_reason": "max_tokens"})
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MAX_OUTPUT_TOKENS

    def test_max_tokens_string(self) -> None:
        result = ErrorClassifier.classify("Output truncated: max_tokens reached")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# media_size detection
# ---------------------------------------------------------------------------


class TestMediaSize:
    def test_image_too_large(self) -> None:
        result = ErrorClassifier.classify("image is too large")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MEDIA_SIZE

    def test_file_exceeds_limit(self) -> None:
        result = ErrorClassifier.classify("file exceeds maximum size limit")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MEDIA_SIZE

    def test_media_too_big(self) -> None:
        result = ErrorClassifier.classify("media content too big for processing")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.MEDIA_SIZE


# ---------------------------------------------------------------------------
# rate_limit detection
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_http_429(self) -> None:
        result = ErrorClassifier.classify({"error": "slow down", "status": 429})
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT
        assert result.http_status == 429

    def test_rate_limit_message(self) -> None:
        result = ErrorClassifier.classify("rate_limit exceeded, please retry")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT

    def test_too_many_requests(self) -> None:
        result = ErrorClassifier.classify("Too many requests")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT

    def test_quota_exceeded(self) -> None:
        result = ErrorClassifier.classify("quota exceeded for this API key")
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT


# ---------------------------------------------------------------------------
# unrecoverable / TerminalError
# ---------------------------------------------------------------------------


class TestTerminalError:
    def test_unknown_error_string(self) -> None:
        result = ErrorClassifier.classify("something completely unexpected happened")
        assert isinstance(result, TerminalError)
        assert result.kind == ErrorKind.UNRECOVERABLE

    def test_empty_string(self) -> None:
        result = ErrorClassifier.classify("")
        assert isinstance(result, TerminalError)
        assert result.kind == ErrorKind.UNRECOVERABLE

    def test_generic_exception(self) -> None:
        result = ErrorClassifier.classify(ValueError("bad value"))
        assert isinstance(result, TerminalError)
        assert result.kind == ErrorKind.UNRECOVERABLE
        assert "bad value" in result.original_error

    def test_dict_without_known_pattern(self) -> None:
        result = ErrorClassifier.classify({"error": "internal server error", "status": 500})
        assert isinstance(result, TerminalError)
        assert result.kind == ErrorKind.UNRECOVERABLE


# ---------------------------------------------------------------------------
# Exception-based classification
# ---------------------------------------------------------------------------


class TestExceptionClassification:
    def test_exception_with_prompt_too_long(self) -> None:
        exc = RuntimeError("prompt is too long for this model")
        result = ErrorClassifier.classify(exc)
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_exception_with_rate_limit(self) -> None:
        exc = Exception("429 Too many requests")
        result = ErrorClassifier.classify(exc)
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT


# ---------------------------------------------------------------------------
# Dict-based error responses
# ---------------------------------------------------------------------------


class TestDictErrorResponses:
    def test_nested_error_message(self) -> None:
        result = ErrorClassifier.classify(
            {"error": {"message": "context_length_exceeded", "type": "invalid_request_error"}}
        )
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.PROMPT_TOO_LONG

    def test_error_key_string(self) -> None:
        result = ErrorClassifier.classify({"error": "rate_limit"})
        assert isinstance(result, RecoverableError)
        assert result.kind == ErrorKind.RATE_LIMIT


# ---------------------------------------------------------------------------
# Frozen model invariants
# ---------------------------------------------------------------------------


class TestFrozenModels:
    def test_recoverable_error_is_frozen(self) -> None:
        err = RecoverableError(kind=ErrorKind.RATE_LIMIT, original_error="429")
        with pytest.raises(Exception):
            err.kind = ErrorKind.MEDIA_SIZE  # type: ignore[misc]

    def test_terminal_error_is_frozen(self) -> None:
        err = TerminalError(original_error="boom")
        with pytest.raises(Exception):
            err.original_error = "changed"  # type: ignore[misc]

    def test_recovery_context_is_frozen(self) -> None:
        ctx = RecoveryContext(
            error=RecoverableError(kind=ErrorKind.RATE_LIMIT, original_error="429"),
            messages=[],
            session_key="sk",
            turn_id="t1",
        )
        with pytest.raises(Exception):
            ctx.attempt = 5  # type: ignore[misc]

    def test_recovery_result_is_frozen(self) -> None:
        res = RecoveryResult(success=True, strategy_name="test")
        with pytest.raises(Exception):
            res.success = False  # type: ignore[misc]

    def test_recovery_attempt_state_is_frozen(self) -> None:
        state = RecoveryAttemptState()
        with pytest.raises(Exception):
            state.attempt_number = 99  # type: ignore[misc]

    def test_error_recovery_config_is_frozen(self) -> None:
        config = ErrorRecoveryConfig()
        with pytest.raises(Exception):
            config.recovery_enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestErrorRecoveryConfig:
    def test_defaults(self) -> None:
        config = ErrorRecoveryConfig()
        assert config.recovery_enabled is False
        assert config.max_recovery_attempts == 3
        assert config.max_collapse_fraction == 0.2
        assert config.max_output_tokens_escalation == 65536
        assert config.rate_limit_max_retries == 3
        assert config.rate_limit_base_delay_seconds == 1.0
