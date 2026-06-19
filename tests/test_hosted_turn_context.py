"""Tests for hosted_request_to_turn_context mapper (PR2 flip).

Uses the same _payload() / fixture helpers as
tests/test_gate5b4c3_shadow_generation_contract.py — no hand-built Pydantic
from scratch.
"""
from __future__ import annotations

import dataclasses
import pytest

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationRequest,
)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import _shadow_session_id
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context


# ---------------------------------------------------------------------------
# Shared test fixtures (mirror constants from the contract test)
# ---------------------------------------------------------------------------
BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64
BOT_CONFIG_DIGEST = "sha256:" + "4" * 64


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "shadowGenerationId": "shadow_gen_001",
        "requestIdDigest": REQUEST_DIGEST,
        "traceIdDigest": TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": BOT_DIGEST,
            "ownerUserIdDigest": OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": True,
            "toolHostDispatchAllowed": False,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _make_request(**overrides: object) -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload(**overrides))


def _make_request_with_history() -> Gate5B4C3ShadowGenerationRequest:
    """Build a request with sanitized_recent_history (selected_full_toolhost)."""
    payload = _payload()
    payload["turn"] = {
        **payload["turn"],  # type: ignore[dict-item]
        "sanitizedRecentHistory": (
            {
                "role": "user",
                "sanitizedText": "What did you find?",
                "sanitizedTextDigest": "sha256:" + "7" * 64,
            },
            {
                "role": "assistant",
                "sanitizedText": "The fixture contained a redacted note.",
                "sanitizedTextDigest": "sha256:" + "8" * 64,
            },
        ),
    }
    payload["recipeProfile"] = {
        **payload["recipeProfile"],  # type: ignore[dict-item]
        "toolsPolicy": "selected_full_toolhost",
        "sourceAuthority": "bounded_sanitized_recent_history",
    }
    payload["policy"] = {
        **payload["policy"],  # type: ignore[dict-item]
        "toolsDisabled": False,
        "toolHostDispatchAllowed": True,
    }
    payload["budgets"] = {
        **payload["budgets"],  # type: ignore[dict-item]
        "maxSanitizedHistoryMessages": 2,
    }
    return Gate5B4C3ShadowGenerationRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# Test 1: Basic mapping — all fields hit their documented sources
# ---------------------------------------------------------------------------
def test_basic_field_mapping() -> None:
    generation = _make_request()
    ctx = hosted_request_to_turn_context(generation)

    assert ctx.prompt == generation.turn.sanitized_current_turn_text
    assert ctx.turn_id == generation.turn.turn_id
    assert ctx.provider == generation.model_routing.provider_label
    assert ctx.model == generation.model_routing.model_label
    # Defaults mandated by PR2 spec
    assert ctx.recipe is None
    assert ctx.memory_mode == "normal"
    assert ctx.permission_mode == "default"
    assert ctx.permission_cap is None
    assert ctx.depth == 0
    assert ctx.budget_ms == 0


# ---------------------------------------------------------------------------
# Test 2: session_id uses _shadow_session_id semantics
# ---------------------------------------------------------------------------
def test_session_id_uses_shadow_session_id_helper() -> None:
    generation = _make_request()
    ctx = hosted_request_to_turn_context(generation)

    # Assert against the helper directly — not a hardcoded string — so this
    # test proves we're delegating rather than reimplementing.
    assert ctx.session_id == _shadow_session_id(generation)


def test_session_id_changes_with_session_key_digest() -> None:
    """Two requests differing only in sessionKeyDigest must yield different session_ids."""
    generation_a = _make_request()
    # Build second request with a different sessionKeyDigest
    payload_b = _payload()
    payload_b["selection"] = {
        **payload_b["selection"],  # type: ignore[dict-item]
        "sessionKeyDigest": "sha256:" + "9" * 64,
    }
    generation_b = Gate5B4C3ShadowGenerationRequest.model_validate(payload_b)

    ctx_a = hosted_request_to_turn_context(generation_a)
    ctx_b = hosted_request_to_turn_context(generation_b)
    assert ctx_a.session_id != ctx_b.session_id


# ---------------------------------------------------------------------------
# Test 3: initial_messages — non-empty history → correct dicts in order
# ---------------------------------------------------------------------------
def test_initial_messages_from_history() -> None:
    generation = _make_request_with_history()
    ctx = hosted_request_to_turn_context(generation)

    history = generation.turn.sanitized_recent_history
    assert len(ctx.initial_messages) == len(history) == 2

    for i, (msg, ctx_msg) in enumerate(zip(history, ctx.initial_messages)):
        assert ctx_msg == {"role": msg.role, "content": msg.sanitized_text}, (
            f"initial_messages[{i}] mismatch"
        )


def test_initial_messages_empty_when_no_history() -> None:
    generation = _make_request()  # no sanitizedRecentHistory
    ctx = hosted_request_to_turn_context(generation)

    assert ctx.initial_messages == ()


def test_initial_messages_order_preserved() -> None:
    """History order must be preserved (user first, then assistant)."""
    generation = _make_request_with_history()
    ctx = hosted_request_to_turn_context(generation)

    assert ctx.initial_messages[0]["role"] == "user"
    assert ctx.initial_messages[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Test 4: TurnContext is frozen (hashable, real dataclass)
# ---------------------------------------------------------------------------
def test_turn_context_is_frozen() -> None:
    generation = _make_request()
    ctx = hosted_request_to_turn_context(generation)

    # Frozen dataclasses are hashable
    h = hash(ctx)
    assert isinstance(h, int)

    # Confirm it's the real TurnContext dataclass
    fields = {f.name for f in dataclasses.fields(ctx)}
    assert "prompt" in fields
    assert "session_id" in fields
    assert "turn_id" in fields
    assert "initial_messages" in fields

    # Mutation must raise FrozenInstanceError on the frozen dataclass
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.prompt = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 5: No prompt mutation — exact string, no normalization
# ---------------------------------------------------------------------------
def test_prompt_exact_no_mutation() -> None:
    raw_text = "  Leading and trailing spaces and\nnewlines\n  "
    payload = _payload()
    payload["turn"] = {
        **payload["turn"],  # type: ignore[dict-item]
        "sanitizedCurrentTurnText": raw_text,
        "sanitizedInputTextDigest": SANITIZED_DIGEST,
    }
    generation = Gate5B4C3ShadowGenerationRequest.model_validate(payload)
    ctx = hosted_request_to_turn_context(generation)

    assert ctx.prompt == generation.turn.sanitized_current_turn_text
    assert ctx.prompt == raw_text  # exact — no strip/normalize
