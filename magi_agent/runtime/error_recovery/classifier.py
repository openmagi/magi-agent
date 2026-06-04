from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    RecoverableError,
    TerminalError,
)

# ---------------------------------------------------------------------------
# Detection patterns -- complementary to error_taxonomy.py which classifies
# for runner boundary decisions (restore_to_typescript vs fail_closed).
# This classifier determines WHICH recovery strategy to apply.
# ---------------------------------------------------------------------------

# Matches input-token overflow errors from API providers.
# Deliberately includes "max_tokens_exceeded" (an OpenAI error type that
# signals the *input* prompt is too long), which is distinct from the
# output-truncation signal matched by _MAX_OUTPUT_TOKENS_RE below.
# Precedence: _PROMPT_TOO_LONG_RE is checked first in _classify_text() so
# that "max_tokens_exceeded" input overflows are classified as PROMPT_TOO_LONG
# and not confused with the output stop-reason "max_tokens".
_PROMPT_TOO_LONG_RE = re.compile(
    r"prompt is too long|context_length_exceeded|max_tokens_exceeded|"
    r"request entity too large|input.*too (?:long|large)|"
    r"exceeds.*context|maximum context length",
    re.IGNORECASE,
)

# Captures the numeric overflow from messages like:
#   "maximum context length is 128000 tokens ... resulted in 130500 tokens"
# re.DOTALL is intentionally omitted — API error messages are single-line.
_TOKENS_OVER_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?resulted in (\d+) tokens",
    re.IGNORECASE,
)

# Matches "max_tokens" as a finish/stop *reason* (output truncated), not as
# an input-overflow error type.  Checked after _PROMPT_TOO_LONG_RE to avoid
# misclassifying the "max_tokens_exceeded" input-overflow string above.
_MAX_OUTPUT_TOKENS_RE = re.compile(
    r"\bmax_tokens\b",
    re.IGNORECASE,
)

_MEDIA_SIZE_RE = re.compile(
    r"(?:media|image|file).*?(?:too (?:large|big)|exceeds)",
    re.IGNORECASE,
)

_RATE_LIMIT_RE = re.compile(
    r"\brate_limit\b|too many requests|quota exceeded",
    re.IGNORECASE,
)


class ErrorClassifier:
    """Classifies exceptions/error responses into RecoverableError or TerminalError.

    This classifier is complementary to ``error_taxonomy.py`` which classifies
    errors for the runner boundary (restore_to_typescript vs fail_closed).
    This classifier determines which *recovery strategy* to apply.
    """

    @staticmethod
    def classify(
        error: BaseException | str | dict[str, object],
    ) -> RecoverableError | TerminalError:
        text, http_status = _extract_signal(error)
        retry_after_seconds = _extract_retry_after_seconds(error, text)

        if not text and http_status is None:
            return TerminalError(original_error=str(error))

        # --- HTTP status short-circuits ---
        if http_status == 413:
            return RecoverableError(
                kind=ErrorKind.PROMPT_TOO_LONG,
                original_error=text,
                http_status=413,
            )
        if http_status == 429:
            return RecoverableError(
                kind=ErrorKind.RATE_LIMIT,
                original_error=text,
                http_status=429,
                retry_after_seconds=retry_after_seconds,
            )

        # --- Pattern matching on text ---
        if _PROMPT_TOO_LONG_RE.search(text):
            tokens_over = _extract_tokens_over(text)
            return RecoverableError(
                kind=ErrorKind.PROMPT_TOO_LONG,
                original_error=text,
                http_status=http_status,
                tokens_over=tokens_over,
            )

        if _MEDIA_SIZE_RE.search(text):
            return RecoverableError(
                kind=ErrorKind.MEDIA_SIZE,
                original_error=text,
                http_status=http_status,
            )

        if _RATE_LIMIT_RE.search(text):
            return RecoverableError(
                kind=ErrorKind.RATE_LIMIT,
                original_error=text,
                http_status=http_status,
                retry_after_seconds=retry_after_seconds,
            )

        # Check dict-level finish/stop reason for max_output_tokens
        if isinstance(error, dict):
            finish = (
                error.get("finish_reason")
                or error.get("finishReason")
                or error.get("stop_reason")
                or error.get("stopReason")
                or ""
            )
            if isinstance(finish, str) and finish in {"length", "max_tokens"}:
                return RecoverableError(
                    kind=ErrorKind.MAX_OUTPUT_TOKENS,
                    original_error=text,
                    http_status=http_status,
                )

        if _MAX_OUTPUT_TOKENS_RE.search(text):
            return RecoverableError(
                kind=ErrorKind.MAX_OUTPUT_TOKENS,
                original_error=text,
                http_status=http_status,
            )

        return TerminalError(
            original_error=text or str(error),
            http_status=http_status,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_signal(
    error: BaseException | str | dict[str, object],
) -> tuple[str, int | None]:
    """Extract a text signal and optional HTTP status from the error input."""
    if isinstance(error, BaseException):
        return str(error), None

    if isinstance(error, str):
        return error, None

    if isinstance(error, dict):
        http_status = _safe_int(error.get("status") or error.get("statusCode"))
        parts: list[str] = []

        err_value = error.get("error", "")
        if isinstance(err_value, dict):
            # Nested: {"error": {"message": "...", "type": "..."}}
            msg = err_value.get("message", "")
            if msg:
                parts.append(str(msg))
            err_type = err_value.get("type", "")
            if err_type:
                parts.append(str(err_type))
        elif err_value:
            parts.append(str(err_value))

        for key in ("message", "reason", "detail"):
            val = error.get(key)
            if val and isinstance(val, str):
                parts.append(val)

        # Include finish/stop reason signals in text for regex matching
        for key in ("finish_reason", "finishReason", "stop_reason", "stopReason"):
            val = error.get(key)
            if val and isinstance(val, str):
                parts.append(val)

        return " ".join(parts), http_status

    return "", None


def _safe_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


# OpenCode parses the ``retry-after-ms`` header first (millisecond precision),
# then falls back to the standard ``Retry-After`` header which is either an
# integer number of seconds or an HTTP-date. We mirror that precedence.
_RETRY_AFTER_MS_RE = re.compile(
    r"retry[-_ ]?after[-_ ]?ms\s*[:=]\s*[\"']?(\d+)",
    re.IGNORECASE,
)
_RETRY_AFTER_SECONDS_RE = re.compile(
    r"retry[-_ ]?after\s*[:=]\s*[\"']?(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_RETRY_AFTER_DATE_RE = re.compile(
    r"retry[-_ ]?after\s*[:=]\s*[\"']?([A-Za-z]{3},\s*[^\"'\r\n]+GMT)",
    re.IGNORECASE,
)
_RETRY_AFTER_CAP_SECONDS = 60.0


def _extract_retry_after_seconds(
    error: BaseException | str | dict[str, object],
    text: str,
) -> float | None:
    """Parse an OpenCode-style Retry-After hint into seconds (capped at 60s).

    Precedence: ``retry-after-ms`` header > ``Retry-After`` seconds >
    ``Retry-After`` HTTP-date. Looks at structured dict fields first (header
    maps), then any free-form error text. Returns ``None`` when no hint is
    present so the caller can fall back to exponential backoff.
    """
    structured = _retry_after_from_structured(error)
    if structured is not None:
        return structured
    return _retry_after_from_text(text)


def _retry_after_from_structured(
    error: BaseException | str | dict[str, object],
) -> float | None:
    headers: dict[str, object] = {}
    if isinstance(error, dict):
        for key in ("headers", "response_headers", "responseHeaders"):
            candidate = error.get(key)
            if isinstance(candidate, dict):
                headers.update(candidate)
        # Some SDKs surface the hint as a top-level field.
        for key in ("retry_after_ms", "retryAfterMs", "retry_after", "retryAfter"):
            if key in error:
                headers.setdefault(key, error.get(key))
    else:
        response = getattr(error, "response", None)
        response_headers = getattr(response, "headers", None)
        if isinstance(response_headers, dict):
            headers.update(response_headers)
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for key in ("retry-after-ms", "retry_after_ms", "retryafterms"):
        ms = _safe_float(lowered.get(key))
        if ms is not None and ms >= 0:
            return _cap_retry_after(ms / 1000.0)
    for key in ("retry-after", "retry_after", "retryafter"):
        value = lowered.get(key)
        seconds = _safe_float(value)
        if seconds is not None and seconds >= 0:
            return _cap_retry_after(seconds)
        date_seconds = _http_date_delay(value)
        if date_seconds is not None:
            return _cap_retry_after(date_seconds)
    return None


def _retry_after_from_text(text: str) -> float | None:
    if not text:
        return None
    ms_match = _RETRY_AFTER_MS_RE.search(text)
    if ms_match:
        return _cap_retry_after(int(ms_match.group(1)) / 1000.0)
    seconds_match = _RETRY_AFTER_SECONDS_RE.search(text)
    if seconds_match:
        return _cap_retry_after(float(seconds_match.group(1)))
    date_match = _RETRY_AFTER_DATE_RE.search(text)
    if date_match:
        date_seconds = _http_date_delay(date_match.group(1))
        if date_seconds is not None:
            return _cap_retry_after(date_seconds)
    return None


def _http_date_delay(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = (parsed - datetime.now(timezone.utc)).total_seconds()
    return delta if delta > 0 else 0.0


def _cap_retry_after(seconds: float) -> float:
    if seconds < 0:
        return 0.0
    return min(seconds, _RETRY_AFTER_CAP_SECONDS)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _extract_tokens_over(text: str) -> int | None:
    match = _TOKENS_OVER_RE.search(text)
    if match:
        limit = int(match.group(1))
        actual = int(match.group(2))
        return actual - limit
    return None


__all__ = ["ErrorClassifier"]
