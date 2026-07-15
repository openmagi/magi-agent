"""Shared gate5b4c3 request/config/ADK fixtures + keeper-helper unit tests.

P5-M1b retired the legacy ``Gate5B4C3LiveRunnerBoundary`` engine and all of its
orchestration (manual-tool loops, no-tool finalizer, event parsing, session
reuse), whose unit tests lived in this module. That behavior now lives in the
governed ``MagiEngineDriver`` / ``collect_engine_to_boundary_result`` path and
its serving lease/seed logic in ``transport/gate5b_serving.py``, covered by
``tests/test_gate5b_serving_session_lease.py`` and
``tests/test_gate5b_serving_seed_on_empty.py``.

What remains here:

* the shared request/config/ADK-primitive fixtures other suites import
  (``_request``, ``_enabled_config``, ``_payload``, ``_selected_full_toolhost_
  request``, ``_real_session_primitives``, etc.); and
* focused unit tests for the KEEPER helpers that survived the retirement and are
  still called by the governed serving path (``_gate1a_correlated_model_or_label``
  model routing + the ``Gate5B4C3LiveRunnerBoundaryResult`` authority invariant).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.shadow import gate5b4c3_live_runner_boundary as live_boundary_module
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
    Gate5B4C3LiveRunnerBoundaryResult,
    _gate1a_correlated_model_or_label,
    _gate5b_litellm_model,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationBudgets,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationProviderCredentialBinding,
    Gate5B4C3ShadowGenerationRequest,
)
from magi_agent.shadow.session_service_registry import SessionServiceRegistry


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
MODEL_ATTEMPT_DIGEST = "sha256:" + "5" * 64


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
            "tsResponseCorrelationId": "ts_corr_001",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
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


def _request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload())


def _readonly_request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "shadow_readonly",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )


def _selected_full_toolhost_request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "selected_full_toolhost",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )


def _selected_full_toolhost_history_request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedRecentHistory": (
                    {
                        "role": "user",
                        "sanitizedText": "What did you find last turn?",
                        "sanitizedTextDigest": "sha256:" + "7" * 64,
                    },
                    {
                        "role": "assistant",
                        "sanitizedText": "I found a redacted fixture anomaly.",
                        "sanitizedTextDigest": "sha256:" + "8" * 64,
                    },
                ),
            },
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
            },
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "selected_full_toolhost",
                "sourceAuthority": "bounded_sanitized_recent_history",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
            budgets={
                "maxSanitizedHistoryMessages": 2,
            },
        )
    )


def _gate1a_google_request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
            },
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "shadow_readonly",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )


def _enabled_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("anthropic",),
        allowedModelLabels=("claude-3-5-sonnet-latest",),
        allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
        allowedShadowCredentialRefs=("server-shadow-ref",),
    )


def _gate1a_google_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("google",),
        allowedModelLabels=("gemini-3.5-flash",),
        allowedModelRoutes=("google:gemini-3.5-flash",),
        allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
    )


def _fireworks_full_toolhost_request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "fireworks",
                "modelLabel": "kimi-k2p6",
                "shadowCredentialRef": "platform-proxy-fireworks",
            },
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "selected_full_toolhost",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )


def _fireworks_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("fireworks",),
        allowedModelLabels=("kimi-k2p6",),
        allowedModelRoutes=("fireworks:kimi-k2p6",),
        allowedShadowCredentialRefs=("platform-proxy-fireworks",),
        providerCredentialBindings=(
            Gate5B4C3ShadowGenerationProviderCredentialBinding(
                providerLabel="fireworks",
                credentialRef="platform-proxy-fireworks",
                credentialSource="env_presence",
                requiredEnvVars=("FIREWORKS_API_KEY",),
                presentEnvVars=("FIREWORKS_API_KEY",),
                adkNative=False,
            ),
        ),
        providerCredentialBindingRequired=True,
    )


def _gate1a_google_config_with_adk_llm_calls(
    max_adk_llm_calls: int,
) -> Gate5B4C3ShadowGenerationConfig:
    return _gate1a_google_config().model_copy(
        update={
            "approved_budgets": Gate5B4C3ShadowGenerationBudgets(
                maxAdkLlmCalls=max_adk_llm_calls,
            )
        }
    )


from tests.support.gate5b4c3_fakes import *  # noqa: F401, F403


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    _FakeRunner.fail = False
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _function_call_only_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FunctionCallOnlyRunner.created_kwargs = {}
    _FunctionCallOnlyRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FunctionCallOnlyRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _function_call_then_final_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FunctionCallThenFinalRunner.created_kwargs = {}
    _FunctionCallThenFinalRunner.run_kwargs = {}
    _FunctionCallThenFinalRunner.calls = []
    _FunctionCallThenFinalRunner.event_factory = _FunctionCallOnlyEvent
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FunctionCallThenFinalRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _duplicate_text_and_function_call_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.created_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.run_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.calls = []
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_DuplicateTextAndFunctionCallRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _event_cap_text_and_function_call_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _EventCapTextAndFunctionCallRunner.created_kwargs = {}
    _EventCapTextAndFunctionCallRunner.run_kwargs = {}
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_EventCapTextAndFunctionCallRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _auto_tool_loop_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _AutoToolLoopAgent.created_kwargs = []
    _AutoToolLoopRunner.created_kwargs = {}
    _AutoToolLoopRunner.run_kwargs = {}
    _AutoToolLoopRunner.calls = []
    _AutoToolLoopRunner.after_function_call_observer = None
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_AutoToolLoopAgent,
        Runner=_AutoToolLoopRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _promise_only_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _PromiseOnlyRunner.created_kwargs = {}
    _PromiseOnlyRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_PromiseOnlyRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _mapping_content_parts_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _MappingContentPartsRunner.created_kwargs = {}
    _MappingContentPartsRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_MappingContentPartsRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _candidate_content_parts_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _CandidateContentPartsRunner.created_kwargs = {}
    _CandidateContentPartsRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_CandidateContentPartsRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _model_dump_candidate_content_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _ModelDumpCandidateContentRunner.created_kwargs = {}
    _ModelDumpCandidateContentRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_ModelDumpCandidateContentRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _partial_aggregate_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _PartialAggregateRunner.created_kwargs = {}
    _PartialAggregateRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_PartialAggregateRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _output_continuation_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _OutputContinuationRunner.created_kwargs = {}
    _OutputContinuationRunner.run_kwargs = {}
    _OutputContinuationRunner.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_OutputContinuationRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _long_selected_text_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _LongSelectedTextRunner.created_kwargs = {}
    _LongSelectedTextRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_LongSelectedTextRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _loader_that_must_not_run() -> Gate5B4C3LiveAdkPrimitives:
    raise AssertionError("ADK primitives must not load unless generation is accepted")


def _file_write_adk_tool(tmp_path: Path) -> object:
    pytest.importorskip("google.adk.tools")
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHostConfig,
        build_gate5b_full_toolhost_bundle,
    )

    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": BOT_DIGEST,
                "selectedOwnerDigest": OWNER_DIGEST,
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": ("FileWrite",),
                "maxToolCallsPerTurn": 1,
            }
        ),
        scope={
            "selectedBotDigest": BOT_DIGEST,
            "selectedOwnerDigest": OWNER_DIGEST,
            "environment": "production",
        },
        workspace_root=tmp_path,
    )
    return bundle.tools[0]


class _UsageEvent:
    def __init__(
        self,
        text: str,
        prompt: int,
        candidates: int,
        cached: int = 0,
    ) -> None:
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")

        class _Usage:
            prompt_token_count = prompt
            candidates_token_count = candidates
            cached_content_token_count = cached

        self.usage_metadata = _Usage()


class _UsageRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield _UsageEvent("local diagnostic event only", prompt=1234, candidates=56)


def _usage_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _UsageRunner.created_kwargs = {}
    _UsageRunner.run_kwargs = {}
    _UsageRunner.fail = False
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_UsageRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


# ── Incomplete-output heuristic: guard against false positives on long answers ──


# ── 08-PR5 hosted session reuse (default-OFF) — isolation/TTL/seed-on-miss ──


SESSION_DIGEST_ALT = "sha256:" + "6" * 64
BOT_DIGEST_ALT = "sha256:" + "9" * 64
_HISTORY_MARKER = "Recent sanitized conversation:"
_CURRENT_TURN_TEXT = "Please summarize the approved redacted note."


class _MustNotTouchRegistry:
    """Poisoned registry: any interaction fails the bypass regression tests."""

    _MESSAGE = (
        "session registry must not be consulted on this turn "
        "(flag OFF, or no stable session key)"
    )

    def get_or_create(self, *args: object, **kwargs: object) -> tuple[object, bool]:
        raise AssertionError(self._MESSAGE)

    def try_acquire(self, *args: object, **kwargs: object) -> tuple[object, bool]:
        raise AssertionError(self._MESSAGE)

    def release(self, *args: object, **kwargs: object) -> bool:
        raise AssertionError(self._MESSAGE)

    def evict(self, *args: object, **kwargs: object) -> bool:
        raise AssertionError(self._MESSAGE)


class _SessionReuseManualClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _session_reuse_request(
    *,
    bot_digest: str = BOT_DIGEST,
    session_digest: str | None = SESSION_DIGEST,
) -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            selection={
                **_payload()["selection"],  # type: ignore[arg-type]
                "botIdDigest": bot_digest,
                "sessionKeyDigest": session_digest,
            },
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedRecentHistory": (
                    {
                        "role": "user",
                        "sanitizedText": "What did you find last turn?",
                        "sanitizedTextDigest": "sha256:" + "7" * 64,
                    },
                    {
                        "role": "assistant",
                        "sanitizedText": "I found a redacted fixture anomaly.",
                        "sanitizedTextDigest": "sha256:" + "8" * 64,
                    },
                ),
            },
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
            },
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "selected_full_toolhost",
                "sourceAuthority": "bounded_sanitized_recent_history",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
            budgets={"maxSanitizedHistoryMessages": 2},
        )
    )


def _session_reuse_config(
    *,
    bot_digest: str = BOT_DIGEST,
) -> Gate5B4C3ShadowGenerationConfig:
    return _gate1a_google_config().model_copy(update={"selected_bot_digest": bot_digest})


class _OverlapBlockingRunner(_FakeRunner):
    """Runner that parks mid-consumption so a second turn can overlap it."""

    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    entered: threading.Event = threading.Event()
    unblock: threading.Event = threading.Event()

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        type(self).entered.set()
        await asyncio.to_thread(type(self).unblock.wait, 10.0)
        yield {"text": "overlapping turn finished"}


def _overlap_blocking_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _OverlapBlockingRunner.created_kwargs = {}
    _OverlapBlockingRunner.run_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_OverlapBlockingRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


class _EmptyThenFinalizerRunner(_FakeRunner):
    """Main run yields NOTHING (no text, no tool calls); finalizer yields text.

    Models the "thinking-only / empty" turn: the model emitted no visible final
    answer and called no tool, so the no-tool finalizer must still run (there are
    no tool-only events to key off) and produce the final answer.
    """

    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.agent = kwargs["agent"]

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        type(self).calls.append(
            {"toolsAttached": bool(getattr(self.agent, "tools", ()))}
        )
        if getattr(self.agent, "tools", ()):
            return  # empty main run — no events at all
            yield  # pragma: no cover - marks this coroutine as a generator
        yield _FakeEvent("final answer after empty-turn finalizer")


def _empty_then_finalizer_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _AutoToolLoopAgent.created_kwargs = []
    _EmptyThenFinalizerRunner.created_kwargs = {}
    _EmptyThenFinalizerRunner.run_kwargs = {}
    _EmptyThenFinalizerRunner.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_AutoToolLoopAgent,
        Runner=_EmptyThenFinalizerRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


# ── continuity observability (PR-1): session_reused / event_count / seeded ──


def _capture_turn_start_events() -> list[dict[str, object]]:
    """Register a transcript sink capturing only ``turn_start`` records."""
    from magi_agent.observability.transcript import set_active_transcript_sink

    captured: list[dict[str, object]] = []

    def _sink(event: dict[str, object], _session_id: str, _turn_id: str) -> None:
        if event.get("type") == "turn_start":
            captured.append(event)

    set_active_transcript_sink(_sink)
    return captured


# ── seed-on-empty-session safety net (PR-2): emptiness probe drives seeding ──


class _EventAppendingRunner(_FakeRunner):
    """Runner over a real ADK session service that optionally appends an event.

    Mirrors what the production ADK Runner does: it get-or-creates the session
    and appends events, so a reused service accumulates real turn history that
    the emptiness probe can read on the next turn.
    """

    append_event_on_run: bool = True

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        service = type(self).created_kwargs["session_service"]
        app_name = type(self).created_kwargs["app_name"]
        user_id = kwargs["user_id"]
        session_id = kwargs["session_id"]
        session = await service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if session is None:
            session = await service.create_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
        if type(self).append_event_on_run:
            from google.adk.events import Event
            from google.genai import types as _genai_types

            await service.append_event(
                session,
                Event(
                    author="user",
                    content=_genai_types.Content(
                        parts=[_genai_types.Part.from_text(text="prior turn")],
                        role="user",
                    ),
                ),
            )
        yield _FakeEvent("final answer for the turn")


def _real_session_primitives(*, append: bool) -> Gate5B4C3LiveAdkPrimitives:
    from google.adk.sessions import InMemorySessionService

    _EventAppendingRunner.created_kwargs = {}
    _EventAppendingRunner.run_kwargs = {}
    _EventAppendingRunner.append_event_on_run = append
    _FakeAgent.created_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_EventAppendingRunner,
        InMemorySessionService=InMemorySessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )



# ---------------------------------------------------------------------------
# Keeper-helper unit tests (P5-M1b): the model-routing builder and the boundary
# result authority invariant survived the legacy engine retirement and are still
# used by the governed serving path, so they keep dedicated coverage here.
# ---------------------------------------------------------------------------


def test_gate1a_correlated_model_or_label_builds_litellm_for_fireworks_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-native provider (fireworks) routes through the LiteLlm builder."""
    built: list[tuple[str, str]] = []

    def fake_litellm_model(provider_label: str, model_label: str) -> object:
        built.append((provider_label, model_label))
        return SimpleNamespace(
            model=f"fireworks_ai/{model_label}",
            openmagi_gate5b_litellm_model=True,
        )

    monkeypatch.setattr(live_boundary_module, "_gate5b_litellm_model", fake_litellm_model)

    model = _gate1a_correlated_model_or_label(
        "fireworks", "kimi-k2p6", context=None, proxy_url=None
    )

    assert built == [("fireworks", "kimi-k2p6")]
    assert getattr(model, "openmagi_gate5b_litellm_model") is True
    assert getattr(model, "model") == "fireworks_ai/kimi-k2p6"


def test_gate1a_correlated_model_or_label_returns_bare_label_for_uncorrelated_google() -> None:
    """A google route with no egress correlation context returns the bare label
    (the runner falls back to its default routing)."""
    model = _gate1a_correlated_model_or_label(
        "google", "gemini-3.5-flash", context=None, proxy_url=None
    )
    assert model == "gemini-3.5-flash"


def test_gate5b_litellm_model_maps_known_provider_prefixes() -> None:
    """The litellm builder maps every known provider label to a prefix (or raises
    a dependency error, never a KeyError). Fireworks -> fireworks_ai/... ."""
    try:
        model = _gate5b_litellm_model("fireworks", "kimi-k2p6")
    except RuntimeError as exc:  # litellm/ADK dep missing in this env
        assert "litellm_dependency_missing" in str(exc)
    else:
        assert getattr(model, "model", "").startswith("fireworks_ai/")


def test_boundary_result_copy_and_construct_cannot_create_authority_or_user_output() -> None:
    """The boundary-result model_validator hard-forces typescript authority /
    diagnostic-only / no user-visible output even when a caller tries to inject
    a python authority via model_copy or model_construct."""
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationDiagnostic,
    )

    diagnostic = Gate5B4C3ShadowGenerationDiagnostic(
        accepted=True,
        status="accepted",
        reason="accepted",
        shadowGenerationId="test-sg-id",
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
        routingSource="per_turn_injected",
    ).model_dump(by_alias=True, mode="python", warnings=False)

    base = Gate5B4C3LiveRunnerBoundaryResult(
        diagnostic=diagnostic,
        status="completed",
        reason="runner_completed",
        selectedProvider="anthropic",
        selectedModel="claude-3-5-sonnet-latest",
        routingSource="per_turn_injected",
    )
    copied = base.model_copy(
        update={
            "responseAuthority": "python",
            "diagnosticOnly": False,
            "localOnly": False,
            "userVisibleOutput": "leak",
            "authority": {"userVisibleOutputAllowed": True},
        }
    )
    constructed = Gate5B4C3LiveRunnerBoundaryResult.model_construct(
        diagnostic=base.diagnostic,
        status="completed",
        reason="runner_completed",
        selectedProvider="anthropic",
        selectedModel="claude-3-5-sonnet-latest",
        routingSource="per_turn_injected",
        responseAuthority="python",
        diagnosticOnly=False,
        localOnly=False,
        userVisibleOutput="leak",
        authority={"userVisibleOutputAllowed": True, "toolDispatchAllowed": True},
    )

    for candidate in (copied, constructed):
        assert candidate.response_authority == "typescript"
        assert candidate.diagnostic_only is True
        assert candidate.local_only is True
        assert candidate.user_visible_output is None
        assert candidate.authority.user_visible_output_allowed is False
        assert candidate.authority.tool_dispatch_allowed is False
