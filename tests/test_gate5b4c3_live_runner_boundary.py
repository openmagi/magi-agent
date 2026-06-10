from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.shadow import gate5b4c3_live_runner_boundary as live_boundary_module
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
    Gate5B4C3LiveRunnerBoundary,
    Gate5B4C3LiveRunnerBoundaryResult,
    _event_usage_metadata,
    _invoke_manual_tool,
    _looks_like_incomplete_full_toolhost_output,
    _selected_full_toolhost_run_config,
    _usage_dict,
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


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakeGenerateContentConfig:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeRunner:
    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    fail: bool = False

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        if type(self).fail:
            raise RuntimeError("provider failed with Authorization: Bearer unsafe-token")
        yield {"text": "local diagnostic event only"}


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")


class _FunctionCallOnlyPart:
    function_call = {"name": "Calculation", "args": {"expression": "1 + 1"}}


class _FunctionCallOnlyEvent:
    class _Content:
        parts = [_FunctionCallOnlyPart()]

    content = _Content()


class _CandidateFunctionCallOnlyEvent:
    candidates = [
        {
            "content": {
                "parts": [
                    {
                        "functionCall": {
                            "name": "Calculation",
                            "args": {"expression": "2 + 3"},
                        }
                    }
                ]
            }
        }
    ]

    @property
    def text(self) -> str:
        return ""


class _MethodFunctionCall:
    name = "Calculation"
    args = {"expression": "3 + 4"}
    id = "call_method"


class _MethodFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    def get_function_calls(self) -> list[object]:
        return [_MethodFunctionCall()]


class _FunctionCallOnlyRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield _FunctionCallOnlyEvent()


class _FunctionCallThenFinalRunner(_FakeRunner):
    calls: list[dict[str, object]] = []
    event_factory: object = _FunctionCallOnlyEvent

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        type(self).calls.append(kwargs)
        if len(type(self).calls) == 1:
            factory = type(self).event_factory
            yield factory() if callable(factory) else factory
            return
        message = kwargs["new_message"]
        assert isinstance(message, _FakeContent)
        assert "Tool execution results" in message.parts[0].text
        yield _FakeEvent("final answer after manual tool execution")


class _TextAndFunctionCallEvent:
    """A single model turn that emits preamble text AND a pending tool call.

    This is the shape that produced "promise without delivery": the model says
    it will do the work and emits the function call in the same turn.
    """

    def __init__(self) -> None:
        self.content = _FakeContent(
            parts=[
                _FakePart("재무제표 분석을 진행하겠습니다."),
                _FunctionCallOnlyPart(),
            ],
            role="model",
        )


class _DuplicateTextAndFunctionCallRunner(_FakeRunner):
    calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        type(self).calls.append(kwargs)
        if len(type(self).calls) == 1:
            yield _TextAndFunctionCallEvent()
            yield _TextAndFunctionCallEvent()
            return
        message = kwargs["new_message"]
        assert isinstance(message, _FakeContent)
        assert "Tool execution results" in message.parts[0].text
        yield _FakeEvent("final answer after one manual tool execution")


class _EventCapTextAndFunctionCallRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        for _ in range(63):
            yield _FakeEvent("")
        yield _TextAndFunctionCallEvent()


class _FunctionResponseOnlyPart:
    function_response = {"name": "Calculation", "response": {"status": "ok"}}


class _FunctionResponseOnlyEvent:
    class _Content:
        parts = [_FunctionResponseOnlyPart()]

    content = _Content()


class _AutoToolLoopAgent:
    created_kwargs: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.tools = tuple(kwargs.get("tools", ()))
        type(self).created_kwargs.append(kwargs)


class _AutoToolLoopRunner(_FakeRunner):
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.agent = kwargs["agent"]
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        type(self).calls.append(
            {
                "toolsAttached": bool(getattr(self.agent, "tools", ())),
                "newMessage": kwargs.get("new_message"),
                "runConfigPresent": kwargs.get("run_config") is not None,
            }
        )
        if getattr(self.agent, "tools", ()):
            yield _FunctionCallOnlyEvent()
            yield _FunctionResponseOnlyEvent()
            return
        yield _FakeEvent("final answer after no-tool finalizer")


class _PromiseOnlyRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield _FakeEvent(
            "선정된 종목들에 대해 /multibagger-full-report 분석을 병렬로 실행하겠습니다. "
            "잠시만 기다려 주세요."
        )


class _ManualCalculationTool:
    name = "Calculation"
    calls: list[dict[str, object]] = []

    @classmethod
    async def run_async(
        cls,
        *,
        args: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        del tool_context
        cls.calls.append(args)
        return {
            "status": "ok",
            "reason": "tool_completed",
            "outputPreview": {"value": 2},
        }


class _MappingContentPartsRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield {"content": {"parts": ({"text": "live ADK text from mapping parts"},)}}


class _CandidateContentPartsRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield {
            "candidates": (
                {
                    "content": {
                        "parts": (
                            {"text": "live ADK text from candidate parts"},
                        )
                    }
                },
            )
        }


class _ModelDumpCandidateContentRunner(_FakeRunner):
    class _Event:
        def model_dump(self, **_kwargs: object) -> dict[str, object]:
            return {
                "candidates": (
                    {
                        "content": {
                            "parts": (
                                {"text": "live ADK text from model dump"},
                            )
                        }
                    },
                )
            }

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield self._Event()


class _PartialAggregateEvent:
    def __init__(self, text: str, *, partial: bool) -> None:
        self.partial = partial
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")


class _PartialAggregateRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield _PartialAggregateEvent("EX", partial=True)
        yield _PartialAggregateEvent("ACTLY_ONCE_SENTINEL_9Q4Z", partial=True)
        yield _PartialAggregateEvent("EXACTLY_ONCE_SENTINEL_9Q4Z", partial=False)


class _ModelDumpFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "functionCalls": [
                {
                    "name": "Calculation",
                    "args": {"expression": "5 + 6"},
                    "id": "dump_call",
                }
            ]
        }


class _ModelDumpFunctionCallOnlyPart:
    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "function_call": {
                "name": "Calculation",
                "args": {"expression": "7 + 8"},
                "id": "part_dump_call",
            }
        }


class _PartModelDumpFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    @property
    def content(self) -> object:
        return type(
            "_Content",
            (),
            {"parts": [_ModelDumpFunctionCallOnlyPart()]},
        )()


class _ProviderSetupFailRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        raise RuntimeError(
            "No API key configured at /Users/kevin/private with "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield {"text": "must not happen"}


class _GenericProxyFailRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        raise RuntimeError("ProxyError: upstream tunnel reset after CONNECT")
        yield {"text": "must not happen"}


class _FunctionToolSchemaTypeErrorRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        raise TypeError(
            "FunctionTool schema signature mismatch at /Users/kevin/private "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield {"text": "must not happen"}


class _RunnerConstructionFail:
    def __init__(self, **_kwargs: object) -> None:
        raise RuntimeError(
            "Runner construction failed at /Users/kevin/private with token=secret"
        )


class _ToolHostAttachmentFailAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs
        raise RuntimeError(
            "ToolHost attachment failed with Cookie: session=secret and /private/path"
        )


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


def _loader_that_must_not_run() -> Gate5B4C3LiveAdkPrimitives:
    raise AssertionError("ADK primitives must not load unless generation is accepted")


def test_live_boundary_default_disabled_does_not_load_adk_and_keeps_typescript_authority() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
        _request(),
        config=Gate5B4C3ShadowGenerationConfig(),
    )

    assert result.status == "skipped"
    assert result.reason == "not_accepted"
    assert result.diagnostic.reason == "disabled"
    assert result.response_authority == "typescript"
    assert result.diagnostic_only is True
    assert result.local_only is True
    assert result.adk_invoked is False
    assert result.runner_attempted is False
    assert result.model_call_via_adk_runner_attempted is False
    assert result.user_visible_output is None
    assert result.authority.user_visible_output_allowed is False
    assert result.authority.tool_dispatch_allowed is False
    assert result.authority.memory_write_allowed is False
    assert result.authority.child_execution_allowed is False
    assert result.authority.mission_runtime_allowed is False


def test_live_boundary_invokes_runner_with_allowlisted_kwargs_and_disabled_tools() -> None:
    # PR11: this request routes a Claude model, which now resolves through
    # magi's cache-aware ADK subclass (CacheAwareClaude). Building it imports
    # ADK's Anthropic integration, which requires the optional `anthropic`
    # package — skip cleanly when it is not installed.
    pytest.importorskip("anthropic")
    result = Gate5B4C3LiveRunnerBoundary(_fake_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.response_authority == "typescript"
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.event_count == 1
    assert result.agent_kwargs_keys == (
        "description",
        "generate_content_config",
        "instruction",
        "model",
        "name",
        "tools",
    )
    assert result.runner_kwargs_keys == (
        "agent",
        "app_name",
        "auto_create_session",
        "session_service",
    )
    assert result.run_async_kwargs_keys == ("new_message", "session_id", "user_id")
    assert set(_FakeAgent.created_kwargs) == set(result.agent_kwargs_keys)
    # PR11: a Claude/anthropic model id now resolves to magi's cache-aware ADK
    # subclass (CacheAwareClaude) so the outgoing Anthropic request can carry
    # rolling-tail cache markers when MAGI_MESSAGE_CACHE_ENABLED is set. The
    # underlying model name is preserved on the resolved instance.
    resolved_model = _FakeAgent.created_kwargs["model"]
    assert getattr(resolved_model, "magi_message_cache_aware", False) is True
    assert getattr(resolved_model, "model", None) == "claude-3-5-sonnet-latest"
    assert _FakeAgent.created_kwargs["tools"] == []
    assert _FakeGenerateContentConfig.created_kwargs == {"maxOutputTokens": 512}
    assert set(_FakeRunner.created_kwargs) == set(result.runner_kwargs_keys)
    assert set(_FakeRunner.run_kwargs) == set(result.run_async_kwargs_keys)
    assert "state_delta" not in _FakeRunner.run_kwargs
    assert "run_config" not in _FakeRunner.run_kwargs
    message = _FakeRunner.run_kwargs["new_message"]
    assert isinstance(message, _FakeContent)
    assert message.parts[0].text == "Please summarize the approved redacted note."
    assert result.user_visible_output is None
    assert result.authority.db_writes_allowed is False
    assert result.authority.workspace_mutation_allowed is False


def test_live_boundary_selected_full_toolhost_runner_receives_prior_sanitized_turns() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(
        _selected_full_toolhost_history_request(),
        config=_gate1a_google_config(),
    )

    assert result.status == "completed"
    message = _FakeRunner.run_kwargs["new_message"]
    assert isinstance(message, _FakeContent)
    text = message.parts[0].text
    assert "Recent sanitized conversation:" in text
    assert "user: What did you find last turn?" in text
    assert "assistant: I found a redacted fixture anomaly." in text
    assert "Current user message:" in text
    assert "Please summarize the approved redacted note." in text


def test_live_boundary_selected_full_toolhost_uses_request_adk_llm_call_budget() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
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
            budgets={"maxAdkLlmCalls": 32},
        )
    )

    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(
        request,
        config=_gate1a_google_config_with_adk_llm_calls(32),
    )

    assert result.status == "completed"
    run_config = _FakeRunner.run_kwargs["run_config"]
    assert getattr(run_config, "max_llm_calls") == 32


def test_live_boundary_selected_full_toolhost_run_config_requests_sse_streaming() -> None:
    pytest.importorskip("google.adk.agents.run_config")
    from google.adk.agents.run_config import StreamingMode

    run_config = _selected_full_toolhost_run_config(True, max_llm_calls=32)

    assert run_config is not None
    assert getattr(run_config, "max_llm_calls") == 32
    assert getattr(run_config, "streaming_mode") == StreamingMode.SSE


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


def test_manual_fallback_invokes_real_adk_function_tool_with_direct_args(
    tmp_path: Path,
) -> None:
    tool = _file_write_adk_tool(tmp_path)

    result = asyncio.run(
        _invoke_manual_tool(
            tool,
            {"path": "manual-fallback.txt", "content": "manual fallback wrote"},
        )
    )

    assert (tmp_path / "manual-fallback.txt").read_text(encoding="utf-8") == (
        "manual fallback wrote"
    )
    assert isinstance(result, dict)
    assert result.get("status") == "ok"


def test_manual_fallback_unwraps_provider_arguments_object_for_real_adk_tool(
    tmp_path: Path,
) -> None:
    tool = _file_write_adk_tool(tmp_path)

    result = asyncio.run(
        _invoke_manual_tool(
            tool,
            {
                "arguments": {
                    "path": "provider-wrapper.txt",
                    "content": "provider wrapper wrote",
                }
            },
        )
    )

    assert (tmp_path / "provider-wrapper.txt").read_text(encoding="utf-8") == (
        "provider wrapper wrote"
    )
    assert isinstance(result, dict)
    assert result.get("status") == "ok"


def test_live_boundary_rejects_completed_runner_without_text_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_function_call_only_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "error"
    assert result.reason == "runner_output_missing"
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.event_count == 1
    assert result.output_text_internal is None
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "runner_output_projection"
    assert result.runner_error_diagnostic.reason_code == "runner_output_missing"
    assert result.runner_error_diagnostic.exception_category == (
        "runner_output_projection_failure"
    )
    assert result.user_visible_output is None
    assert result.authority.user_visible_output_allowed is False


def test_live_boundary_runs_manual_full_toolhost_continuation_for_function_call_only_event() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _function_call_then_final_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.event_count == 2
    assert result.output_text_internal == "final answer after manual tool execution"
    assert _ManualCalculationTool.calls == [{"expression": "1 + 1"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2
    assert result.runner_error_diagnostic is None


def test_live_boundary_executes_pending_tool_calls_emitted_with_preamble_text() -> None:
    # Root-cause guard: when the model emits preamble text AND a tool call in the
    # same turn, the runtime must still execute the tool and let the model finish
    # — not short-circuit on the text and serve the unfulfilled promise.
    primitives = _function_call_then_final_primitives()
    _FunctionCallThenFinalRunner.event_factory = _TextAndFunctionCallEvent

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    # The pending tool call was executed, not discarded.
    assert _ManualCalculationTool.calls == [{"expression": "1 + 1"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2
    assert "final answer after manual tool execution" in (
        result.output_text_internal or ""
    )


def test_live_boundary_deduplicates_pending_tool_calls_across_events() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _duplicate_text_and_function_call_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert _ManualCalculationTool.calls == [{"expression": "1 + 1"}]
    assert len(_DuplicateTextAndFunctionCallRunner.calls) == 2
    assert "final answer after one manual tool execution" in (
        result.output_text_internal or ""
    )


def test_live_boundary_does_not_execute_manual_tool_at_event_cap() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _event_cap_text_and_function_call_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "error"
    assert result.reason == "runner_incomplete"
    assert result.event_count == 64
    assert _ManualCalculationTool.calls == []


def test_live_boundary_runs_manual_full_toolhost_continuation_for_candidate_function_call_event() -> None:
    primitives = _function_call_then_final_primitives()
    _FunctionCallThenFinalRunner.event_factory = _CandidateFunctionCallOnlyEvent

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.output_text_internal == "final answer after manual tool execution"
    assert _ManualCalculationTool.calls == [{"expression": "2 + 3"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2


def test_live_boundary_runs_manual_full_toolhost_continuation_for_method_function_calls() -> None:
    primitives = _function_call_then_final_primitives()
    _FunctionCallThenFinalRunner.event_factory = _MethodFunctionCallOnlyEvent

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.output_text_internal == "final answer after manual tool execution"
    assert _ManualCalculationTool.calls == [{"expression": "3 + 4"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2


def test_live_boundary_runs_manual_full_toolhost_continuation_for_model_dump_function_calls() -> None:
    primitives = _function_call_then_final_primitives()
    _FunctionCallThenFinalRunner.event_factory = _ModelDumpFunctionCallOnlyEvent

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.output_text_internal == "final answer after manual tool execution"
    assert _ManualCalculationTool.calls == [{"expression": "5 + 6"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2


def test_live_boundary_runs_manual_full_toolhost_continuation_for_part_model_dump_function_calls() -> None:
    primitives = _function_call_then_final_primitives()
    _FunctionCallThenFinalRunner.event_factory = _PartModelDumpFunctionCallOnlyEvent

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.output_text_internal == "final answer after manual tool execution"
    assert _ManualCalculationTool.calls == [{"expression": "7 + 8"}]
    assert len(_FunctionCallThenFinalRunner.calls) == 2


def test_live_boundary_runs_no_tool_finalizer_after_adk_tool_only_events() -> None:
    primitives = _auto_tool_loop_primitives()

    result = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.output_text_internal == "final answer after no-tool finalizer"
    assert result.event_count == 3
    assert [call["toolsAttached"] for call in _AutoToolLoopRunner.calls] == [
        True,
        False,
    ]
    assert [call["runConfigPresent"] for call in _AutoToolLoopRunner.calls] == [
        True,
        True,
    ]
    assert [bool(kwargs["tools"]) for kwargs in _AutoToolLoopAgent.created_kwargs] == [
        True,
        False,
    ]


def test_live_boundary_rejects_promise_only_full_toolhost_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _promise_only_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(_selected_full_toolhost_request(), config=_enabled_config())

    assert result.status == "error"
    assert result.reason == "runner_incomplete"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "runner_output_projection"
    assert result.runner_error_diagnostic.reason_code == "runner_incomplete"
    assert result.output_text_internal is not None
    assert result.user_visible_output is None


def test_live_boundary_extracts_mapping_content_parts_text_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_mapping_content_parts_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.event_count == 1
    assert result.output_text_internal == "live ADK text from mapping parts"
    assert result.runner_error_diagnostic is None


def test_live_boundary_extracts_candidate_content_parts_text_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_candidate_content_parts_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.event_count == 1
    assert result.output_text_internal == "live ADK text from candidate parts"
    assert result.runner_error_diagnostic is None


def test_live_boundary_extracts_model_dump_candidate_text_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(
        _model_dump_candidate_content_primitives
    ).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.event_count == 1
    assert result.output_text_internal == "live ADK text from model dump"
    assert result.runner_error_diagnostic is None


def test_live_boundary_does_not_reemit_final_aggregate_after_partial_deltas() -> None:
    public_events: list[dict[str, object]] = []
    readonly_tool = object()

    result = Gate5B4C3LiveRunnerBoundary(
        _partial_aggregate_primitives,
        adk_tools=(readonly_tool,),
        public_event_sink=lambda event: public_events.append(dict(event)),
    ).invoke(
        _gate1a_google_request(),
        config=_gate1a_google_config(),
    )

    assert result.status == "completed"
    assert result.output_text_internal == "EXACTLY_ONCE_SENTINEL_9Q4Z"
    text_deltas = [
        event["delta"] for event in public_events if event.get("type") == "text_delta"
    ]
    assert text_deltas == ["EX", "ACTLY_ONCE_SENTINEL_9Q4Z"]


def test_live_boundary_fails_closed_on_tool_policy_mismatch_before_adk_load() -> None:
    readonly_without_tools = Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
        _readonly_request(),
        config=_enabled_config(),
    )
    disabled_with_tools = Gate5B4C3LiveRunnerBoundary(
        _loader_that_must_not_run,
        adk_tools=(object(),),
    ).invoke(_request(), config=_enabled_config())

    for result in (readonly_without_tools, disabled_with_tools):
        assert result.status == "dropped"
        assert result.reason == "input_adapter_drop"
        assert result.error_preview == "tool_policy_mismatch"
        assert result.adk_invoked is False
        assert result.runner_attempted is False
        assert result.model_call_via_adk_runner_attempted is False


def test_live_boundary_attaches_gate1a_readonly_tools_only_when_policy_matches() -> None:
    readonly_tool = object()

    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(readonly_tool,),
    ).invoke(_readonly_request(), config=_enabled_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert _FakeAgent.created_kwargs["tools"] == [readonly_tool]
    instruction = str(_FakeAgent.created_kwargs["instruction"])
    assert "read-only tools" in instruction
    assert "no-tools" not in instruction.lower()
    assert "Do not request tools" not in instruction


def test_live_boundary_attaches_gate1a_proxy_connect_headers_only_with_context() -> None:
    from magi_agent.evidence.gate1a_egress_correlation import (
        Gate1AEgressCorrelationContext,
    )

    readonly_tool = object()
    request = _gate1a_google_request()
    context = Gate1AEgressCorrelationContext(
        request_digest=request.request_id_digest,
        correlation_digest=request.request_id_digest,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
    )

    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(readonly_tool,),
        gate1a_egress_correlation_context=context,
        gate1a_egress_proxy_url=(
            "http://gate5b-gemini-egress-proxy.openmagi-system.svc.cluster.local:8080"
        ),
    ).invoke(request, config=_gate1a_google_config())

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    model = _FakeAgent.created_kwargs["model"]
    assert model != "gemini-3.5-flash"
    assert getattr(model, "model") == "gemini-3.5-flash"
    assert getattr(model, "openmagi_gate1a_proxy_connect_headers_enabled") is True
    assert set(_FakeRunner.run_kwargs) == {"new_message", "session_id", "user_id"}
    assert "x-gate1a-request-digest" not in json.dumps(_FakeRunner.run_kwargs, default=str)


def test_live_boundary_does_not_attach_gate1a_proxy_connect_headers_without_context() -> None:
    readonly_tool = object()
    request = _gate1a_google_request()

    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(readonly_tool,),
    ).invoke(
        request,
        config=_gate1a_google_config(),
    )

    assert result.status == "completed"
    assert _FakeAgent.created_kwargs["model"] == "gemini-3.5-flash"
    assert set(_FakeRunner.run_kwargs) == {"new_message", "session_id", "user_id"}


def test_live_boundary_builds_litellm_model_for_fireworks_route(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[str, str]] = []

    def fake_litellm_model(provider_label: str, model_label: str) -> object:
        built.append((provider_label, model_label))
        return SimpleNamespace(
            model=f"fireworks_ai/{model_label}",
            openmagi_gate5b_litellm_model=True,
        )

    monkeypatch.setattr(
        live_boundary_module,
        "_gate5b_litellm_model",
        fake_litellm_model,
    )

    result = Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(_ManualCalculationTool,),
    ).invoke(
        _fireworks_full_toolhost_request(),
        config=_fireworks_config(),
    )

    assert result.status == "completed"
    assert built == [("fireworks", "kimi-k2p6")]
    model = _FakeAgent.created_kwargs["model"]
    assert getattr(model, "openmagi_gate5b_litellm_model") is True
    assert getattr(model, "model") == "fireworks_ai/kimi-k2p6"


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


def test_event_usage_metadata_reads_object_mapping_and_nested_shapes() -> None:
    class _Meta:
        prompt_token_count = 10
        candidates_token_count = 4
        cached_content_token_count = 2

    class _Evt:
        usage_metadata = _Meta()

    assert _event_usage_metadata(_Evt()) == (10, 4, 2)
    assert _event_usage_metadata(
        {"usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}}
    ) == (7, 3, 0)
    assert _event_usage_metadata(
        {"llm_response": {"usage_metadata": {"prompt_token_count": 5}}}
    ) == (5, 0, 0)
    assert _event_usage_metadata({"text": "no usage here"}) is None


def test_usage_dict_drops_all_zero_totals() -> None:
    assert _usage_dict((0, 0, 0)) is None
    assert _usage_dict((9, 1, 0)) == {
        "inputTokens": 9,
        "outputTokens": 1,
        "cacheReadTokens": 0,
    }


def test_live_boundary_captures_usage_internal_on_completed_turn() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_usage_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.usage_internal == {
        "inputTokens": 1234,
        "outputTokens": 56,
        "cacheReadTokens": 0,
    }
    assert "usageInternal" not in result.model_dump(by_alias=True)
    assert "usage_internal" not in result.model_dump()


def test_live_boundary_usage_internal_none_without_provider_usage() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_fake_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert result.usage_internal is None


def test_live_boundary_uses_adapter_resolved_per_turn_output_cap() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "maxOutputTokens": 128,
            }
        )
    )

    result = Gate5B4C3LiveRunnerBoundary(_fake_primitives).invoke(
        request,
        config=_enabled_config(),
    )

    assert result.status == "completed"
    assert _FakeGenerateContentConfig.created_kwargs == {"maxOutputTokens": 128}


def test_live_boundary_uses_input_adapter_and_does_not_load_adk_on_budget_drop() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": "x" * 80,
            },
            budgets={"maxEstimatedInputTokens": 10},
        )
    )

    result = Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
        request,
        config=_enabled_config(),
    )

    assert result.status == "dropped"
    assert result.reason == "input_adapter_drop"
    assert result.adk_invoked is False
    assert result.runner_attempted is False
    assert result.model_call_via_adk_runner_attempted is False
    assert result.user_visible_output is None


def test_live_boundary_runner_error_fails_open_and_redacts_error_preview() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        _FakeRunner.fail = True
        return primitives

    result = Gate5B4C3LiveRunnerBoundary(failing_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.response_authority == "typescript"
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.error_class == "RuntimeError"
    assert result.error_preview is not None
    assert "unsafe-token" not in result.error_preview
    assert "Authorization:" not in result.error_preview
    assert "[REDACTED]" in result.error_preview
    assert result.user_visible_output is None


def test_live_boundary_provider_setup_failure_has_sanitized_stage_diagnostic() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        return Gate5B4C3LiveAdkPrimitives(
            Agent=primitives.Agent,
            Runner=_ProviderSetupFailRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = Gate5B4C3LiveRunnerBoundary(failing_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "provider_client_setup"
    assert result.runner_error_diagnostic.reason_code == "provider_client_setup_failed"
    assert result.runner_error_diagnostic.exception_class == "RuntimeError"
    assert result.runner_error_diagnostic.exception_category == (
        "provider_client_setup_failure"
    )
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is False
    serialized = json.dumps(result.model_dump(by_alias=True, mode="json"))
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
    ):
        assert forbidden not in serialized


def test_live_boundary_generic_proxy_failure_stays_model_attempted() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        return Gate5B4C3LiveAdkPrimitives(
            Agent=primitives.Agent,
            Runner=_GenericProxyFailRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = Gate5B4C3LiveRunnerBoundary(failing_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "runner_execution"
    assert result.runner_error_diagnostic.reason_code == "runner_execution_failed"
    assert result.runner_error_diagnostic.exception_category == "unexpected_exception"
    assert result.model_call_via_adk_runner_attempted is True


def test_live_boundary_function_tool_typeerror_reports_pre_provider_substage() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        return Gate5B4C3LiveAdkPrimitives(
            Agent=primitives.Agent,
            Runner=_FunctionToolSchemaTypeErrorRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    readonly_tool = type("ReadableTool", (), {"name": "Clock"})()
    result = Gate5B4C3LiveRunnerBoundary(
        failing_primitives,
        adk_tools=(readonly_tool,),
    ).invoke(_readonly_request(), config=_enabled_config())

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "adk_tool_schema"
    assert result.runner_error_diagnostic.reason_code == "adk_function_tool_schema_mismatch"
    assert result.runner_error_diagnostic.exception_class == "TypeError"
    assert result.runner_error_diagnostic.exception_category == (
        "adk_function_tool_schema_mismatch"
    )
    assert result.runner_error_diagnostic.error_preview is not None
    assert "[REDACTED]" in result.runner_error_diagnostic.error_preview
    assert result.runner_error_diagnostic.traceback_markers
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is False
    assert result.runner_error_diagnostic.model_call_attempted is False
    assert result.runner_error_diagnostic.active_tool_names == ("Clock",)
    serialized = json.dumps(result.model_dump(by_alias=True, mode="json"))
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
    ):
        assert forbidden not in serialized


def test_live_boundary_runner_construction_failure_has_no_model_attempt() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        return Gate5B4C3LiveAdkPrimitives(
            Agent=primitives.Agent,
            Runner=_RunnerConstructionFail,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = Gate5B4C3LiveRunnerBoundary(failing_primitives).invoke(
        _request(),
        config=_enabled_config(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "adk_runner_construction"
    assert result.runner_error_diagnostic.reason_code == "adk_runner_construction_failed"
    assert result.runner_error_diagnostic.exception_category == (
        "adk_runner_construction_failure"
    )
    assert result.runner_attempted is False
    assert result.model_call_via_adk_runner_attempted is False
    serialized = json.dumps(result.model_dump(by_alias=True, mode="json"))
    assert "token=secret" not in serialized
    assert "/Users/kevin" not in serialized


def test_live_boundary_toolhost_attachment_failure_has_public_safe_diagnostic() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        return Gate5B4C3LiveAdkPrimitives(
            Agent=_ToolHostAttachmentFailAgent,
            Runner=primitives.Runner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = Gate5B4C3LiveRunnerBoundary(
        failing_primitives,
        adk_tools=(object(),),
    ).invoke(_readonly_request(), config=_enabled_config())

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is not None
    assert result.runner_error_diagnostic.stage == "toolhost_attachment"
    assert result.runner_error_diagnostic.reason_code == "toolhost_attachment_failed"
    assert result.runner_error_diagnostic.exception_category == "toolhost_attachment_failure"
    assert result.runner_error_diagnostic.tools_policy == "shadow_readonly"
    assert result.runner_error_diagnostic.tools_enabled is True
    assert result.runner_error_diagnostic.tool_host_dispatch_allowed is True
    assert result.runner_attempted is False
    assert result.model_call_via_adk_runner_attempted is False
    serialized = json.dumps(result.model_dump(by_alias=True, mode="json"))
    assert "Cookie:" not in serialized
    assert "session=secret" not in serialized
    assert "/private/path" not in serialized


def test_live_boundary_result_copy_and_construct_cannot_create_authority_or_user_output() -> None:
    result = Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
        _request(),
        config=Gate5B4C3ShadowGenerationConfig(),
    )
    copied = result.model_copy(
        update={
            "responseAuthority": "python",
            "diagnosticOnly": False,
            "localOnly": False,
            "userVisibleOutput": "leak",
            "authority": {"userVisibleOutputAllowed": True},
        }
    )
    constructed = Gate5B4C3LiveRunnerBoundaryResult.model_construct(
        diagnostic=result.diagnostic,
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


def test_live_boundary_import_is_lazy_and_does_not_activate_route_or_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module(
    "magi_agent.shadow.gate5b4c3_live_runner_boundary"
)
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.events",
    "openai",
    "anthropic",
)
forbidden_prefixes = (
    "magi_agent.transport.shadow_generations",
    "magi_agent.transport.shadow_invocations",
    "magi_agent.transport.chat",
    "magi_agent.routing",
    "magi_agent.workspace",
    "magi_agent.deploy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.telegram",
    "magi_agent.database",
    "magi_agent.api",
    "magi_agent.dashboard",
    "magi_agent.model_routing",
    "magi_agent.missions",
    "magi_agent.scheduler",
    "magi_agent.children",
    "magi_agent.memory",
    "magi_agent.agentmemory",
    "magi_agent.hipocampus",
    "magi_agent.qmd",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"Gate 5B-4c-3d live boundary loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_live_boundary_source_keeps_adk_imports_inside_boundary_loader_only() -> None:
    root = Path(__file__).parents[1]
    module_path = (
        root
        / "magi_agent"
        / "shadow"
        / "gate5b4c3_live_runner_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")
    before_loader = source.split("def load_gate5b4c3_live_adk_primitives", 1)[0]

    assert "google.adk" not in before_loader
    assert "from google.adk" in source
    assert "import openai" not in source
    assert "import anthropic" not in source
    assert "from magi_agent.tools" not in source
    assert "from magi_agent.memory" not in source
    assert "from magi_agent.workspace" not in source
    assert "from magi_agent.children" not in source
    assert "from magi_agent.missions" not in source
    assert "from fastapi" not in source
    assert "APIRouter" not in source
    assert "add_api_route" not in source
    assert "@app." not in source
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source


# ── Incomplete-output heuristic: guard against false positives on long answers ──


def test_short_promise_only_output_is_incomplete() -> None:
    # A one-line "I'll run the report" stub with no delivered substance is the
    # genuine incomplete case the heuristic must keep catching.
    stub = "선정된 종목들에 대해 multibagger 분석을 실행하겠습니다."
    assert _looks_like_incomplete_full_toolhost_output(stub) is True


def test_wait_phrasing_output_is_incomplete() -> None:
    assert (
        _looks_like_incomplete_full_toolhost_output(
            "분석을 진행하겠습니다. 잠시만 기다려 주세요."
        )
        is True
    )


def test_long_substantive_korean_analysis_is_not_incomplete() -> None:
    # A delivered financial analysis legitimately uses polite future-tense
    # ("진행하겠습니다") and work references ("분석") without literally writing a
    # completion token. It must NOT be flagged incomplete just for that phrasing.
    delivered = (
        "내외디스틸러리 재무제표 분석 내용을 정리해 드립니다. "
        + "법인은 2025년 4월 14일부터 12월 31일까지 매출이 발생했으며 "
        + "초기 시설투자로 인해 결손 상태입니다. "
        * 30
        + "추가로 필요한 검토는 다음과 같이 진행하겠습니다."
    )
    assert len(" ".join(delivered.split())) > 600
    assert _looks_like_incomplete_full_toolhost_output(delivered) is False


def test_completion_token_output_is_not_incomplete() -> None:
    assert (
        _looks_like_incomplete_full_toolhost_output(
            "분석을 실행하겠습니다. 결과는 다음과 같습니다."
        )
        is False
    )


# ── 08-PR5 hosted session reuse (default-OFF) — isolation/TTL/seed-on-miss ──


SESSION_DIGEST_ALT = "sha256:" + "6" * 64
BOT_DIGEST_ALT = "sha256:" + "9" * 64
_HISTORY_MARKER = "Recent sanitized conversation:"
_CURRENT_TURN_TEXT = "Please summarize the approved redacted note."


class _MustNotTouchRegistry:
    """Poisoned registry: any interaction fails the flag-OFF regression test."""

    def get_or_create(self, *args: object, **kwargs: object) -> tuple[object, bool]:
        raise AssertionError(
            "session registry must not be consulted when MAGI_HOSTED_SESSION_REUSE is OFF"
        )

    def evict(self, *args: object, **kwargs: object) -> bool:
        raise AssertionError(
            "session registry must not be consulted when MAGI_HOSTED_SESSION_REUSE is OFF"
        )


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
    session_digest: str = SESSION_DIGEST,
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


def _session_reuse_boundary(registry: object) -> Gate5B4C3LiveRunnerBoundary:
    return Gate5B4C3LiveRunnerBoundary(
        _fake_primitives,
        adk_tools=(_ManualCalculationTool,),
        session_service_registry=registry,  # type: ignore[arg-type]
    )


def _invoke_session_reuse_turn(
    boundary: Gate5B4C3LiveRunnerBoundary,
    *,
    bot_digest: str = BOT_DIGEST,
    session_digest: str = SESSION_DIGEST,
) -> tuple[Gate5B4C3LiveRunnerBoundaryResult, object, str]:
    result = boundary.invoke(
        _session_reuse_request(bot_digest=bot_digest, session_digest=session_digest),
        config=_session_reuse_config(bot_digest=bot_digest),
    )
    service = _FakeRunner.created_kwargs["session_service"]
    message = _FakeRunner.run_kwargs["new_message"]
    assert isinstance(message, _FakeContent)
    return result, service, message.parts[0].text


def test_session_reuse_flag_off_default_builds_fresh_service_and_never_touches_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_HOSTED_SESSION_REUSE", raising=False)
    boundary = _session_reuse_boundary(_MustNotTouchRegistry())

    first_result, first_service, first_text = _invoke_session_reuse_turn(boundary)
    second_result, second_service, second_text = _invoke_session_reuse_turn(boundary)

    assert first_result.status == "completed"
    assert second_result.status == "completed"
    assert isinstance(first_service, _FakeSessionService)
    assert isinstance(second_service, _FakeSessionService)
    # Flag OFF keeps today's behavior byte-identical: a fresh session service
    # per turn and the sanitized history re-sent into every turn's message.
    assert second_service is not first_service
    for text in (first_text, second_text):
        assert _HISTORY_MARKER in text
        assert "user: What did you find last turn?" in text
        assert _CURRENT_TURN_TEXT in text


def test_session_reuse_two_turns_share_session_service_and_seed_history_only_on_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    boundary = _session_reuse_boundary(registry)

    first_result, first_service, first_text = _invoke_session_reuse_turn(boundary)
    first_session_id = _FakeRunner.run_kwargs["session_id"]
    second_result, second_service, second_text = _invoke_session_reuse_turn(boundary)
    second_session_id = _FakeRunner.run_kwargs["session_id"]

    assert first_result.status == "completed"
    assert second_result.status == "completed"
    assert second_session_id == first_session_id
    # Registry hit: the second turn reuses the exact same session service.
    assert second_service is first_service
    # Miss seeds the re-sent sanitized history exactly as today...
    assert _HISTORY_MARKER in first_text
    assert "user: What did you find last turn?" in first_text
    # ...but a hit must NOT re-ingest it (the session already holds the
    # context); only the current sanitized turn text is sent.
    assert _HISTORY_MARKER not in second_text
    assert "What did you find last turn?" not in second_text
    assert second_text == _CURRENT_TURN_TEXT


def test_session_reuse_isolation_distinct_bot_digests_never_share_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    boundary = _session_reuse_boundary(registry)

    _result_a, service_a, _text_a = _invoke_session_reuse_turn(
        boundary,
        bot_digest=BOT_DIGEST,
    )
    # Same session-key digest, different bot: must be a miss with a fresh
    # service — bot A session state can never leak into bot B's turn.
    result_b, service_b, text_b = _invoke_session_reuse_turn(
        boundary,
        bot_digest=BOT_DIGEST_ALT,
    )

    assert result_b.status == "completed"
    assert service_b is not service_a
    assert _HISTORY_MARKER in text_b


def test_session_reuse_isolation_distinct_session_ids_never_share_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    boundary = _session_reuse_boundary(registry)

    _result_1, service_1, _text_1 = _invoke_session_reuse_turn(
        boundary,
        session_digest=SESSION_DIGEST,
    )
    result_2, service_2, text_2 = _invoke_session_reuse_turn(
        boundary,
        session_digest=SESSION_DIGEST_ALT,
    )

    assert result_2.status == "completed"
    assert service_2 is not service_1
    assert _HISTORY_MARKER in text_2


def test_session_reuse_ttl_expiry_rebuilds_session_and_reseeds_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    clock = _SessionReuseManualClock()
    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0, clock=clock)
    boundary = _session_reuse_boundary(registry)

    _result_1, stale_service, _text_1 = _invoke_session_reuse_turn(boundary)
    clock.advance(61.0)
    result_2, fresh_service, text_2 = _invoke_session_reuse_turn(boundary)

    assert result_2.status == "completed"
    # Expired entry is evicted: a fresh service is built and the history is
    # seeded again instead of resurrecting the stale session.
    assert fresh_service is not stale_service
    assert _HISTORY_MARKER in text_2
