from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.cache_safe_params import CacheSafeParams


def test_cache_safe_params_keep_model_runtime_config_separate_from_content() -> None:
    params = CacheSafeParams(
        modelRef="model-config:standard-final",
        runtimeConfigRef="runtime-config:local-fake",
        cacheNamespaceRef="cache-namespace:pr21",
        params={
            "temperature": 0.2,
            "maxOutputTokens": 512,
            "topP": 0.9,
            "responseMimeType": "application/json",
        },
    )

    projection = params.public_projection()
    encoded = json.dumps(projection, sort_keys=True)
    assert projection["modelRef"] == "model-config:standard-final"
    assert projection["runtimeConfigRef"] == "runtime-config:local-fake"
    assert projection["digest"].startswith("sha256:")
    assert "prompt" not in encoded.lower()
    assert "user text" not in encoded.lower()
    assert "raw" not in encoded.lower()


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("prompt", "summarize this"),
        ("userText", "private user request"),
        ("systemInstruction", "hidden prompt"),
        ("messages", ["raw message"]),
        ("authorization", "Bearer live-token"),
        ("cookie", "sid=private"),
        ("sessionKey", "session-private"),
        ("toolLogs", "stdout raw output"),
        ("rawOutput", "model raw output"),
        ("path", "/Users/kevin/private/file.txt"),
        ("apiKey", "sk-live-secret"),
        ("query", "short answer"),
        ("temperature秘密", 0.2),
        ("top_p", 0.9),
        ("temperature", "customer ssn 123-45-6789 account balance 9500"),
        ("maxOutputTokens", "512"),
        ("responseMimeType", "customer/account-balance"),
    ),
)
def test_cache_safe_params_reject_content_secrets_private_paths_and_logs(
    key: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="cache-safe"):
        CacheSafeParams(
            modelRef="model-config:standard-final",
            runtimeConfigRef="runtime-config:local-fake",
            params={key: value},
        )


def test_cache_safe_params_model_copy_revalidates_and_recomputes_digest() -> None:
    params = CacheSafeParams(
        modelRef="model-config:standard-final",
        runtimeConfigRef="runtime-config:local-fake",
        params={"temperature": 0.2, "maxOutputTokens": 512},
    )

    copied = params.model_copy(update={"params": {"temperature": 0.4, "maxOutputTokens": 128}})
    assert copied.params == {"temperature": 0.4, "maxOutputTokens": 128}
    assert copied.digest.startswith("sha256:")
    assert copied.digest != params.digest

    with pytest.raises(ValidationError, match="cache-safe"):
        params.model_copy(
            update={
                "params": {"temperature": "customer ssn 123-45-6789 account balance 9500"},
                "digest": "sha256:" + "0" * 64,
            }
        )
