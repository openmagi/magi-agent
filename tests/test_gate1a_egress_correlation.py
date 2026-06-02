import json

import pytest

from magi_agent.evidence.gate1a_egress_correlation import (
    Gate1AEgressCorrelationContext,
    build_gate1a_proxy_http_options,
    gate1a_correlation_headers,
    safe_proxy_url_from_env,
)


REQUEST_DIGEST = "sha256:" + "a" * 64
CORRELATION_DIGEST = "sha256:" + "b" * 64
MODEL_ATTEMPT_DIGEST = "sha256:" + "c" * 64


def test_gate1a_correlation_headers_are_digest_only_and_stable() -> None:
    context = Gate1AEgressCorrelationContext(
        request_digest=REQUEST_DIGEST,
        correlation_digest=CORRELATION_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
    )

    headers = gate1a_correlation_headers(context)

    assert headers == {
        "x-gate1a-request-digest": REQUEST_DIGEST,
        "x-gate1a-correlation-digest": CORRELATION_DIGEST,
        "x-gate1a-model-attempt-digest": MODEL_ATTEMPT_DIGEST,
    }
    serialized = json.dumps(headers, sort_keys=True)
    for forbidden in (
        "prompt text",
        "model output",
        "Authorization",
        "Bearer",
        "Cookie",
        "api_key",
        "session",
        "/Users/",
        "generativelanguage.googleapis.com",
    ):
        assert forbidden not in serialized


def test_gate1a_correlation_headers_reject_raw_or_missing_digest_values() -> None:
    with pytest.raises(ValueError):
        Gate1AEgressCorrelationContext(
            request_digest="prompt text",
            correlation_digest=CORRELATION_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        )

    with pytest.raises(ValueError):
        Gate1AEgressCorrelationContext(
            request_digest=REQUEST_DIGEST,
            correlation_digest="generativelanguage.googleapis.com",
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        )

    with pytest.raises(ValueError):
        Gate1AEgressCorrelationContext(
            request_digest=REQUEST_DIGEST,
            correlation_digest=CORRELATION_DIGEST,
            model_attempt_digest="/Users/kevin/private/api_key",
        )


def test_build_gate1a_proxy_http_options_sets_proxy_connect_headers_only() -> None:
    context = Gate1AEgressCorrelationContext(
        request_digest=REQUEST_DIGEST,
        correlation_digest=CORRELATION_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
    )

    http_options = build_gate1a_proxy_http_options(
        context,
        proxy_url="http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080",
    )

    for args_name in ("client_args", "async_client_args"):
        args = getattr(http_options, args_name)
        assert args is not None
        assert args["trust_env"] is False
        assert "proxy" not in args
        assert _transport_proxy_headers(args["transport"]) == {
            "x-gate1a-request-digest": REQUEST_DIGEST,
            "x-gate1a-correlation-digest": CORRELATION_DIGEST,
            "x-gate1a-model-attempt-digest": MODEL_ATTEMPT_DIGEST,
        }
    assert http_options.headers is None
    serialized = json.dumps(
        {
            name: _transport_proxy_headers(getattr(http_options, name)["transport"])
            for name in ("client_args", "async_client_args")
        },
        sort_keys=True,
    )
    assert "generativelanguage.googleapis.com" not in serialized
    assert "gate5b-gemini-egress-proxy" not in serialized
    assert "Bearer" not in serialized


def _transport_proxy_headers(transport: object) -> dict[str, str]:
    pool = getattr(transport, "_pool", None)
    proxy_headers = getattr(pool, "_proxy_headers", ())
    return {
        name.decode("ascii"): value.decode("ascii")
        for name, value in proxy_headers
    }


def test_build_gate1a_proxy_http_options_uses_httpx_transports_for_async_calls() -> None:
    from google.genai import Client

    context = Gate1AEgressCorrelationContext(
        request_digest=REQUEST_DIGEST,
        correlation_digest=CORRELATION_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
    )

    http_options = build_gate1a_proxy_http_options(
        context,
        proxy_url="http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080",
    )

    assert "proxy" not in (http_options.async_client_args or {})
    assert "transport" in (http_options.async_client_args or {})

    client = Client(api_key="test-api-key", http_options=http_options)

    assert client._api_client._use_aiohttp() is False


def test_gate1a_proxy_url_from_env_rejects_secret_or_path_bearing_values() -> None:
    unsafe_proxy_url = (
        "http://"
        + "proxy_user"
        + ":proxy_word@"
        + "gate5b-gemini-egress-proxy.local:8080"
    )
    assert (
        safe_proxy_url_from_env(
            {
                "CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL": unsafe_proxy_url
            }
        )
        is None
    )
    assert (
        safe_proxy_url_from_env(
            {
                "CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL": (
                    "http://gate5b-gemini-egress-proxy.local:8080/?token=secret"
                )
            }
        )
        is None
    )
    assert (
        safe_proxy_url_from_env(
            {
                "CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL": (
                    "http://gate5b-gemini-egress-proxy.local:8080/private/path"
                )
            }
        )
        is None
    )
