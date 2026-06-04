from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import RuntimeEnvError, parse_runtime_env
from magi_agent.config.models import (
    BuildInfo,
    PythonContextContinuityConfig,
    PythonGate8ReadinessConfig,
    RuntimeConfig,
)
from magi_agent.evidence.observed_egress import (
    LiveEgressTelemetryEvidenceProvider,
    build_gate1a_observed_egress_evidence_provider_from_env,
)
from magi_agent.gates.gate8_readiness import (
    Gate8PreGate8ContinuityReceipt,
)
from magi_agent.gates.pregate8_continuity_canary import (
    PreGate8ContinuityCanaryEvidence,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport import chat as chat_module
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate8",
        "USER_ID": "owner-gate8",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def _gate8_selected_env(**overrides: str) -> dict[str, str]:
    env = {
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST": _digest(
            "bot-gate8"
        ),
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate8"
        ),
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST": "production",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "selected_canary",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS": "1",
    }
    env.update(overrides)
    return env


def _evidence(**overrides: object) -> PreGate8ContinuityCanaryEvidence:
    payload = {
        "status": "pass",
        "fallbackStatus": "none",
        "importedEventCount": 4,
        "rejectedEntryCount": 1,
        "compactionApplied": True,
        "projectionDigest": _digest("projection"),
        "modelVisibleDigest": _digest("model-visible"),
        "sourceTranscriptHeadDigest": _digest("source-head"),
        "observedAdkSessionDigest": _digest("adk-session"),
        "observedModelVisibleDigest": _digest("observed-message"),
        "antecedentDigest": _digest("antecedent"),
        "currentFollowupDigest": _digest("followup"),
        "antecedentPresentInAdkSession": True,
        "currentFollowupPresentInModelVisibleMessage": True,
        "privatePayloadRejected": True,
        "reasonCodes": (
            "runner_completed",
            "antecedent_present",
            "followup_present",
            "private_payload_rejected",
            "fallback_none",
        ),
    }
    payload.update(overrides)
    return PreGate8ContinuityCanaryEvidence.model_validate(payload)


def _healthz(env: dict[str, str]) -> dict[str, object]:
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    runtime.gate1a_observed_egress_evidence_provider = (
        build_gate1a_observed_egress_evidence_provider_from_env(env)
    )
    response = TestClient(create_app(runtime)).get("/healthz")
    assert response.status_code == 200, json.dumps(response.json(), sort_keys=True)
    return response.json()


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.role = role
        self.parts = parts


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeRunner:
    run_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield type(
            "FakeEvent",
            (),
            {
                "content": type(
                    "FakeContent",
                    (),
                    {"parts": [type("FakePart", (), {"text": "gate8 live fake answer"})()]},
                )()
            },
        )()


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _gate8_runtime(config: object) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            botId=config.bot_id,
            userId=config.user_id,
            gatewayToken=config.gateway_token,
            apiProxyUrl=str(config.api_proxy_url),
            chatProxyUrl=str(config.chat_proxy_url),
            redisUrl=str(config.redis_url),
            model=config.model,
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
            contextContinuity=config.context_continuity,
            gate8Readiness=config.gate8_readiness,
            authority=config.authority,
        )
    )


def _attach_gate8_live_route(runtime: OpenMagiRuntime, tmp_path) -> None:
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_digest("bot-gate8"),
        selectedOwnerUserIdDigest=_digest("owner-gate8"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            liveRunnerBoundaryEnabled=True,
            counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
            generationConfig=Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=False,
                capStateInitialized=True,
                providerProjectSpendControlsVerified=True,
                selectedBotDigest=_digest("bot-gate8"),
                trustedOwnerUserIdDigest=_digest("owner-gate8"),
                environment="production",
                allowedProviderLabels=("google",),
                allowedModelLabels=("gemini-3.5-flash",),
                allowedModelRoutes=("google:gemini-3.5-flash",),
                allowedShadowCredentialRefs=("gate8-google-api-key-smoke-v1",),
                providerCredentialBindingRequired=False,
                approvedBudgets={
                    "maxDailyGenerationRuns": 1,
                    "maxDailyGenerationCostUsd": 0.05,
                    "maxCostUsd": 0.05,
                },
            ),
        )
    )


def test_gate8_readiness_is_disabled_by_default_and_exposes_no_authority() -> None:
    body = _healthz(_base_env())

    gate8 = body["gate8Readiness"]
    assert gate8["enabled"] is False
    assert gate8["status"] == "disabled"
    assert gate8["readinessReady"] is False
    assert gate8["selectedScopeMatched"] is False
    assert gate8["blockedByPreGate8Continuity"] is True
    assert gate8["reasonCode"] == "pre_gate8_continuity_canary_missing"
    assert gate8["routeAttached"] is False
    assert gate8["productionRouteAttached"] is False
    assert gate8["userVisibleOutputAllowed"] is False
    assert gate8["writeMutationAllowed"] is False
    assert gate8["memoryWriteAllowed"] is False
    assert gate8["channelDeliveryAllowed"] is False
    assert gate8["workspaceMutationAllowed"] is False
    assert gate8["missionSchedulerAllowed"] is False
    assert gate8["backgroundTaskAllowed"] is False
    assert gate8["selfImprovementAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert "FileRead" in body["activeTools"]
    assert "AgentMemorySearch" in body["activeTools"]


def test_gate8_readiness_requires_selected_scope_and_verified_continuity_evidence(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    body = _healthz(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
                CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
                    "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
                ),
            )
        )
    )

    gate8 = body["gate8Readiness"]
    assert gate8["enabled"] is True
    assert gate8["status"] == "ready"
    assert gate8["readinessReady"] is True
    assert gate8["selectedScopeMatched"] is True
    assert gate8["blockedByPreGate8Continuity"] is False
    assert gate8["reasonCode"] == "gate8_selected_authority_ready"
    assert gate8["responseAuthorityEligible"] is True
    assert gate8["routeAttached"] is False
    assert gate8["productionRouteAttached"] is False
    assert gate8["preGate8ContinuityEvidence"]["importedEventCount"] == 4
    assert gate8["preGate8ContinuityEvidence"]["rejectedEntryCount"] == 1
    assert gate8["preGate8ContinuityEvidence"]["compactionApplied"] is True
    assert gate8["preGate8ContinuityEvidence"]["projectionDigestPresent"] is True
    assert gate8["preGate8ContinuityEvidence"]["reasonCodes"]
    assert gate8["writeMutationAllowed"] is False
    assert gate8["memoryWriteAllowed"] is False
    assert gate8["channelDeliveryAllowed"] is False
    assert gate8["workspaceMutationAllowed"] is False
    assert gate8["missionSchedulerAllowed"] is False
    assert gate8["backgroundTaskAllowed"] is False
    assert gate8["selfImprovementAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert gate8["egressTelemetryCorrelationReady"] is True
    assert gate8["blockedByEgressTelemetryCorrelation"] is False


def test_gate8_readiness_blocks_when_egress_correlation_source_is_missing() -> None:
    body = _healthz(_base_env(**_gate8_selected_env()))

    gate8 = body["gate8Readiness"]
    assert gate8["enabled"] is True
    assert gate8["status"] == "blocked"
    assert gate8["readinessReady"] is False
    assert gate8["selectedScopeMatched"] is True
    assert gate8["blockedByPreGate8Continuity"] is False
    assert gate8["blockedByEgressTelemetryCorrelation"] is True
    assert gate8["egressTelemetryCorrelationReady"] is False
    assert gate8["reasonCode"] == "gate8_egress_correlation_not_ready"
    assert "gate8_egress_correlation_not_ready" in gate8["reasonCodes"]
    assert gate8["responseAuthorityEligible"] is False


def test_gate8_readiness_blocks_config_only_continuity_pass() -> None:
    body = _healthz(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS="0",
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_STATUS="pass",
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_IMPORTED_EVENT_COUNT="4",
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REJECTED_ENTRY_COUNT="0",
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PROJECTION_DIGEST=_digest(
                    "projection"
                ),
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODEL_VISIBLE_DIGEST=_digest(
                    "model-visible"
                ),
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SOURCE_TRANSCRIPT_HEAD_DIGEST=_digest(
                    "source-head"
                ),
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_FALLBACK_STATUS="none",
                CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REASON_CODES="fallback_none",
            )
        )
    )

    gate8 = body["gate8Readiness"]
    assert gate8["enabled"] is True
    assert gate8["status"] == "blocked"
    assert gate8["readinessReady"] is False
    assert gate8["blockedByPreGate8Continuity"] is True
    assert gate8["reasonCode"] == "pre_gate8_continuity_evidence_unverified"
    assert gate8["responseAuthorityEligible"] is False


def test_gate8_selected_authority_env_requires_verified_continuity_evidence() -> None:
    config = parse_runtime_env(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_CHAT_ROUTE="on",
                CORE_AGENT_PYTHON_OUTPUT_MODE="user_visible_canary",
                CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT="1",
                CORE_AGENT_PYTHON_CANARY_ROUTING="1",
            )
        )
    )

    assert config.authority.user_visible_output_allowed is True
    assert config.authority.canary_routing_allowed is True
    assert config.authority.workspace_mutation_allowed is False
    assert config.authority.channel_write_allowed is False
    assert config.authority.db_write_allowed is False

    with pytest.raises(RuntimeEnvError, match="Pre-Gate8 continuity"):
        parse_runtime_env(
            _base_env(
                **_gate8_selected_env(
                    CORE_AGENT_PYTHON_CHAT_ROUTE="on",
                    CORE_AGENT_PYTHON_OUTPUT_MODE="user_visible_canary",
                    CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT="1",
                    CORE_AGENT_PYTHON_CANARY_ROUTING="1",
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS="0",
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_STATUS="pass",
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_IMPORTED_EVENT_COUNT="4",
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PROJECTION_DIGEST=_digest(
                        "projection"
                    ),
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODEL_VISIBLE_DIGEST=_digest(
                        "model-visible"
                    ),
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SOURCE_TRANSCRIPT_HEAD_DIGEST=_digest(
                        "source-head"
                    ),
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_FALLBACK_STATUS="none",
                    CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REASON_CODES="fallback_none",
                )
            )
        )


def test_gate8_chat_route_projects_selected_metadata_without_high_risk_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    config = parse_runtime_env(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_OUTPUT_MODE="user_visible_canary",
                CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT="1",
                CORE_AGENT_PYTHON_CANARY_ROUTING="1",
                CORE_AGENT_PYTHON_CHAT_ROUTE="on",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
                CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
                    "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
                ),
            )
        )
    )
    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            botId=config.bot_id,
            userId=config.user_id,
            gatewayToken=config.gateway_token,
            apiProxyUrl=str(config.api_proxy_url),
            chatProxyUrl=str(config.chat_proxy_url),
            redisUrl=str(config.redis_url),
            model=config.model,
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
            contextContinuity=config.context_continuity,
            gate8Readiness=config.gate8_readiness,
            authority=config.authority,
        )
    )
    runtime.gate1a_observed_egress_evidence_provider = (
        LiveEgressTelemetryEvidenceProvider(
            telemetry_path,
            proxy_url=(
                "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
            ),
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_digest("bot-gate8"),
        selectedOwnerUserIdDigest=_digest("owner-gate8"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda request: {"content": "safe selected Gate 8 response"},
    )
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["gate"] == "gate8_selected_python_authority"
    assert body["gate8Readiness"]["readinessReady"] is True
    assert body["gate8Readiness"]["preGate8ContinuityEvidence"][
        "projectionDigestPresent"
    ] is True
    assert body["authority"]["userVisibleOutputAllowed"] is True
    assert body["authority"]["canaryRoutingAllowed"] is True
    assert body["authority"]["readOnlyToolDispatchAllowed"] is False
    assert body["authority"]["toolDispatchAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert body["safety"]["toolsActive"] is False
    assert body["safety"]["workspaceMutationAllowed"] is False
    encoded = json.dumps(body, sort_keys=True)
    assert _digest("projection") not in encoded
    assert "gateway-token" not in encoded


def test_gate8_live_selected_path_fails_closed_without_correlated_egress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    request_digest = "sha256:" + "9" * 64
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    timestamps = iter(
        (
            "2026-05-24T10:00:00.000Z",
            "2026-05-24T10:00:05.000Z",
        )
    )
    monkeypatch.setattr(chat_module, "_utc_now_iso", lambda: next(timestamps))
    config = parse_runtime_env(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_OUTPUT_MODE="user_visible_canary",
                CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT="1",
                CORE_AGENT_PYTHON_CANARY_ROUTING="1",
                CORE_AGENT_PYTHON_CHAT_ROUTE="on",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
                CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
                    "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
                ),
            )
        )
    )
    runtime = _gate8_runtime(config)
    _attach_gate8_live_route(runtime, tmp_path)
    runtime.gate1a_observed_egress_evidence_provider = (
        LiveEgressTelemetryEvidenceProvider(
            telemetry_path,
            proxy_url=(
                "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
            ),
        )
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": request_digest,
        },
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 503, json.dumps(response.json(), sort_keys=True)
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "missing_observed_egress_evidence"
    assert body["responseAuthority"] == "typescript"
    assert body["adk"]["invoked"] is True
    serialized = json.dumps(body, sort_keys=True)
    for forbidden in (
        "Please answer briefly",
        "gateway-token",
        "generativelanguage.googleapis.com",
        "/Users/",
        "Bearer",
        "api_key",
    ):
        assert forbidden not in serialized


def test_gate8_live_selected_path_reuses_egress_proxy_correlation_without_gate1a_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    request_digest = "sha256:" + "9" * 64
    model_attempt_digest = _digest(
        f"{request_digest}:google:gemini-3.5-flash:attempt:1"
    )
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text(
        json.dumps(
            {
                "schemaVersion": "gate1a.egressProxyTelemetry.v1",
                "observedAt": "2026-05-24T10:00:01.000Z",
                "requestDigest": request_digest,
                "correlationDigest": request_digest,
                "modelAttemptDigest": model_attempt_digest,
                "egressHostClass": "gemini_proxy",
                "evidenceSource": "gate5b_egress_proxy",
                "redactionStatus": "public_safe",
                "decisionReason": "connect_tunnel_established",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    timestamps = iter(
        (
            "2026-05-24T10:00:00.000Z",
            "2026-05-24T10:00:05.000Z",
        )
    )
    monkeypatch.setattr(chat_module, "_utc_now_iso", lambda: next(timestamps))
    config = parse_runtime_env(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_OUTPUT_MODE="user_visible_canary",
                CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT="1",
                CORE_AGENT_PYTHON_CANARY_ROUTING="1",
                CORE_AGENT_PYTHON_CHAT_ROUTE="on",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
                CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
                    "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
                ),
            )
        )
    )
    runtime = _gate8_runtime(config)
    _attach_gate8_live_route(runtime, tmp_path)
    runtime.gate1a_observed_egress_evidence_provider = (
        LiveEgressTelemetryEvidenceProvider(
            telemetry_path,
            proxy_url=(
                "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
            ),
        )
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": request_digest,
        },
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200, json.dumps(response.json(), sort_keys=True)
    body = response.json()
    assert body["gate"] == "gate8_selected_python_authority"
    assert body["activeTools"] == []
    assert "tooling" not in body
    assert body["modelAttemptDigest"] == model_attempt_digest
    assert body["egressEvidenceStatus"] == "observed_egress_evidence_present"
    assert body["egressCorrelationDigest"] == request_digest
    assert body["egressTunnelCount"] == 1
    assert body["egressHostClasses"] == ["gemini_proxy"]
    agent_model = _FakeAgent.created_kwargs["model"]
    assert getattr(agent_model, "model", None) == "gemini-3.5-flash"
    assert getattr(agent_model, "openmagi_gate1a_proxy_connect_headers_enabled", False) is True
    serialized = json.dumps(body["observedEgressEvidence"], sort_keys=True)
    for forbidden in (
        "Please answer briefly",
        "gateway-token",
        "generativelanguage.googleapis.com",
        "/Users/",
        "Bearer",
        "api_key",
    ):
        assert forbidden not in serialized


def test_gate8_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    non_selected = _healthz(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST=_digest(
                    "other-bot"
                )
            )
        )
    )["gate8Readiness"]
    malformed = _healthz(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST="bot-gate8"
            )
        )
    )["gate8Readiness"]

    assert non_selected["status"] == "blocked"
    assert non_selected["readinessReady"] is False
    assert "bot_not_selected" in non_selected["reasonCodes"]
    assert non_selected["responseAuthorityEligible"] is False
    assert malformed["status"] == "blocked"
    assert malformed["readinessReady"] is False
    assert "malformed_selected_scope" in malformed["reasonCodes"]
    assert malformed["responseAuthorityEligible"] is False


def test_gate8_env_is_dedicated_and_gate1a_gate5b_flags_do_not_enable_it() -> None:
    body = _healthz(
        _base_env(
            GATE1A_PYTHON_READONLY_TOOLS_CANARY_ENABLED="1",
            GATE1A_PYTHON_READONLY_TOOLS_CANARY_BOT_ALLOWLIST="bot-gate8",
            GATE5B_PYTHON_USER_VISIBLE_CANARY_ENABLED="1",
            GATE5B_PYTHON_USER_VISIBLE_CANARY_BOT_ALLOWLIST="bot-gate8",
            CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED="1",
            CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE="selected_canary",
            CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS="1",
        )
    )

    gate8 = body["gate8Readiness"]
    assert gate8["enabled"] is False
    assert gate8["status"] == "disabled"
    assert gate8["readinessReady"] is False
    assert gate8["responseAuthorityEligible"] is False


def test_gate8_rejects_attempted_high_risk_authority_env_flags(tmp_path) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    body = _healthz(
        _base_env(
            **_gate8_selected_env(
                CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
                CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
                CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
                    "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
                ),
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_PRODUCTION_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_WRITE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_MEMORY_WRITE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_CHANNEL_DELIVERY_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_WORKSPACE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_MISSION_SCHEDULER_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_BACKGROUND_TASK_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELF_IMPROVEMENT_ALLOWED="true",
            )
        )
    )

    gate8 = body["gate8Readiness"]
    assert gate8["readinessReady"] is True
    assert gate8["routeAttached"] is False
    assert gate8["productionRouteAttached"] is False
    assert gate8["writeMutationAllowed"] is False
    assert gate8["memoryWriteAllowed"] is False
    assert gate8["channelDeliveryAllowed"] is False
    assert gate8["workspaceMutationAllowed"] is False
    assert gate8["missionSchedulerAllowed"] is False
    assert gate8["backgroundTaskAllowed"] is False
    assert gate8["selfImprovementAllowed"] is False


def test_gate8_pre_gate8_receipt_accepts_only_public_safe_verified_evidence() -> None:
    evidence = _evidence()
    receipt = Gate8PreGate8ContinuityReceipt.from_evidence(
        evidence,
        observed_at_epoch_seconds=1_775_000_000,
        now_epoch_seconds=1_775_000_100,
    )

    assert receipt.status == "pass"
    assert receipt.imported_event_count == 4
    assert receipt.rejected_entry_count == 1
    assert receipt.compaction_applied is True
    assert receipt.projection_digest_present is True
    assert receipt.receipt_digest.startswith("sha256:")
    assert receipt.evidence_digest.startswith("sha256:")
    serialized = json.dumps(receipt.model_dump(by_alias=True), sort_keys=True)
    assert _digest("projection") not in serialized
    assert "raw" not in serialized.lower()


def test_gate8_pre_gate8_receipt_rejects_stale_forged_or_private_evidence() -> None:
    evidence = _evidence()
    with pytest.raises(ValueError, match="stale"):
        Gate8PreGate8ContinuityReceipt.from_evidence(
            evidence,
            observed_at_epoch_seconds=1_775_000_000,
            now_epoch_seconds=1_775_020_000,
            max_age_seconds=600,
        )
    with pytest.raises(ValueError, match="verified"):
        Gate8PreGate8ContinuityReceipt.from_context_config(
            PythonContextContinuityConfig(
                enabled=True,
                mode="selected_canary",
                canaryStatus="pass",
                importedEventCount=4,
                projectionDigestPresent=True,
                modelVisibleDigestPresent=True,
                sourceTranscriptHeadDigestPresent=True,
                fallbackStatus="none",
                reasonCodes=("fallback_none",),
            ),
            observed_at_epoch_seconds=1_775_000_000,
            now_epoch_seconds=1_775_000_100,
        )
    with pytest.raises(ValueError, match="private"):
        Gate8PreGate8ContinuityReceipt.from_evidence(
            _evidence(
                status="fail",
                forbiddenPayloadObserved=True,
                reasonCodes=("forbidden_payload_observed", "fallback_none"),
            ),
            observed_at_epoch_seconds=1_775_000_000,
            now_epoch_seconds=1_775_000_100,
        )


def test_gate8_config_cannot_be_constructed_with_write_authority() -> None:
    config = PythonGate8ReadinessConfig(
        enabled=True,
        routeAttached=True,
        productionRouteAttached=True,
        writeMutationAllowed=True,
        memoryWriteAllowed=True,
        channelDeliveryAllowed=True,
        workspaceMutationAllowed=True,
        missionSchedulerAllowed=True,
        backgroundTaskAllowed=True,
        selfImprovementAllowed=True,
    )

    assert config.route_attached is False
    assert config.production_route_attached is False
    assert config.write_mutation_allowed is False
    assert config.memory_write_allowed is False
    assert config.channel_delivery_allowed is False
    assert config.workspace_mutation_allowed is False
    assert config.mission_scheduler_allowed is False
    assert config.background_task_allowed is False
    assert config.self_improvement_allowed is False


def test_gate8_readiness_import_boundary_is_pure_contract_only() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    module = importlib.import_module("magi_agent.gates.gate8_readiness")
    after = set(sys.modules)

    forbidden = {
        "google.adk",
        "google.genai",
        "magi_agent.toolhost",
        "magi_agent.memory.providers",
        "magi_agent.transport.chat",
    }
    assert hasattr(module, "gate8_readiness_health_metadata")
    assert forbidden.isdisjoint(after - before)
