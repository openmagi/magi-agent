from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlparse


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SENSITIVE_RE = re.compile(
    r"(?:"
    r"authorization|bearer|cookie|token|secret|password|api[_-]?key|"
    r"session|prompt|output|provider[_-]?payload|"
    r"generativelanguage\.googleapis\.com|https?://|"
    r"/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/private(?:/[^\s,;}\"']*)?"
    r")",
    re.IGNORECASE,
)
GATE1A_EGRESS_CORRELATION_MODE = "proxy_connect_headers"
GATE1A_EGRESS_TELEMETRY_SOURCE = "gate5b_egress_proxy"
GATE1A_REQUEST_DIGEST_HEADER = "x-gate1a-request-digest"
GATE1A_CORRELATION_DIGEST_HEADER = "x-gate1a-correlation-digest"
GATE1A_MODEL_ATTEMPT_DIGEST_HEADER = "x-gate1a-model-attempt-digest"


@dataclass(frozen=True, repr=False)
class Gate1AEgressCorrelationContext:
    request_digest: str
    correlation_digest: str
    model_attempt_digest: str | None = None

    def __post_init__(self) -> None:
        _validate_digest(self.request_digest, "request digest")
        _validate_digest(self.correlation_digest, "correlation digest")
        if self.model_attempt_digest is not None:
            _validate_digest(self.model_attempt_digest, "model attempt digest")


def gate1a_correlation_headers(
    context: Gate1AEgressCorrelationContext,
) -> dict[str, str]:
    headers = {
        GATE1A_REQUEST_DIGEST_HEADER: context.request_digest,
        GATE1A_CORRELATION_DIGEST_HEADER: context.correlation_digest,
    }
    if context.model_attempt_digest is not None:
        headers[GATE1A_MODEL_ATTEMPT_DIGEST_HEADER] = context.model_attempt_digest
    _validate_header_payload(headers)
    return headers


def build_gate1a_proxy_http_options(
    context: Gate1AEgressCorrelationContext,
    *,
    proxy_url: str,
) -> object:
    import httpx
    from google.genai import types

    safe_proxy_url = _validate_proxy_url(proxy_url)
    proxy_headers = gate1a_correlation_headers(context)
    sync_proxy = httpx.Proxy(
        safe_proxy_url,
        headers=proxy_headers,
    )
    async_proxy = httpx.Proxy(
        safe_proxy_url,
        headers=proxy_headers,
    )
    # google-genai uses aiohttp for async calls unless a custom httpx transport is
    # configured. aiohttp cannot consume httpx.Proxy, so keep CONNECT headers on
    # explicit httpx transports for both sync and async request paths.
    return types.HttpOptions(
        client_args={
            "transport": httpx.HTTPTransport(proxy=sync_proxy),
            "trust_env": False,
        },
        async_client_args={
            "transport": httpx.AsyncHTTPTransport(proxy=async_proxy),
            "trust_env": False,
        },
    )


def safe_proxy_url_from_env(env: Mapping[str, str]) -> str | None:
    # I-1: route the gate1a override through the typed flag registry;
    # the standard ``HTTPS_PROXY`` / ``https_proxy`` fall-throughs stay
    # as raw env reads (not MAGI_/CORE_AGENT_ prefixed — out of scope
    # for the I-1 inventory). ``flag_str`` returns "" for unset which
    # matches the prior ``env.get(...) → None`` short-circuit because
    # the ``or`` chain only treats both as falsy.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    value = (
        flag_str("CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL", env=env)
        or env.get("HTTPS_PROXY")
        or env.get("https_proxy")
        or ""
    )
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        return _validate_proxy_url(cleaned)
    except ValueError:
        return None


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"Gate 1A {field_name} must be a sha256 digest")
    if _SENSITIVE_RE.search(value):
        raise ValueError(f"Gate 1A {field_name} must not contain raw or secret values")


def _validate_header_payload(headers: Mapping[str, str]) -> None:
    serialized = str(dict(headers))
    if _SENSITIVE_RE.search(serialized):
        raise ValueError("Gate 1A egress correlation headers must be digest-only")


def _validate_proxy_url(value: str) -> str:
    cleaned = str(value or "").strip()
    parsed = urlparse(cleaned)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Gate 1A egress proxy URL must be an HTTP(S) proxy origin")
    if parsed.username or parsed.password:
        raise ValueError("Gate 1A egress proxy URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Gate 1A egress proxy URL must not contain query or fragment data")
    if any(char.isspace() for char in cleaned):
        raise ValueError("Gate 1A egress proxy URL must not contain whitespace")
    return cleaned
