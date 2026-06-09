"""I1 — wiring test: _build_user_visible_generation_request populates
Gate5B4C3ShadowGenerationRequest.turn.sanitized_image_blocks from a
mixed text+image chat payload, and leaves it empty for text-only payloads.

Construction approach:
  Mirrors test_chat_route_contract.py::make_runtime() for the OpenMagiRuntime
  and mirrors the Gate5B4C3ShadowGenerationConfig used throughout that file for
  the generation_config.  The route_config is a minimal
  Gate5BUserVisibleChatRouteConfig with environment="production".
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from magi_agent.config.models import (
    BuildInfo,
    PythonRuntimeAuthorityConfig,
    RuntimeConfig,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationImageBlock,
)
from magi_agent.transport.chat import (
    Gate5BUserVisibleChatRouteConfig,
    _build_user_visible_generation_request,  # type: ignore[attr-defined]
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


def _make_route_config() -> Gate5BUserVisibleChatRouteConfig:
    return Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
    )


def _make_generation_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=_sha256("bot-test"),
        trustedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        allowedProviderLabels=("google",),
        allowedModelLabels=("gemini-3.5-flash",),
        allowedModelRoutes=("google:gemini-3.5-flash",),
        allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
        providerCredentialBindingRequired=False,
        approvedBudgets={
            "maxDailyGenerationRuns": 10,
            "maxDailyGenerationCostUsd": 0.50,
            "maxCostUsd": 0.05,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mixed_text_image_payload_populates_sanitized_image_blocks() -> None:
    """Case (a): mixed text+image payload → turn.sanitized_image_blocks carries
    exactly one image block with media_type image/png."""
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _PNG,
                        },
                    },
                ],
            }
        ]
    }

    request = _build_user_visible_generation_request(
        runtime=_make_runtime(),
        route_config=_make_route_config(),
        generation_config=_make_generation_config(),
        payload=payload,
        trace_id=None,
    )

    blocks = request.turn.sanitized_image_blocks
    assert len(blocks) == 1, f"expected 1 image block, got {len(blocks)}"
    block = blocks[0]
    assert isinstance(block, Gate5B4C3ShadowGenerationImageBlock)
    assert block.media_type == "image/png"
    assert block.data == _PNG


def test_text_only_payload_yields_empty_sanitized_image_blocks() -> None:
    """Case (b): text-only payload → turn.sanitized_image_blocks is empty."""
    payload = {
        "messages": [
            {"role": "user", "content": "just plain text, no image"},
        ]
    }

    request = _build_user_visible_generation_request(
        runtime=_make_runtime(),
        route_config=_make_route_config(),
        generation_config=_make_generation_config(),
        payload=payload,
        trace_id=None,
    )

    assert request.turn.sanitized_image_blocks == ()
