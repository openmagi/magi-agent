from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from magi_agent.config.models import (
    BuildInfo,
    PythonContextContinuityConfig,
    PythonMemoryAdapterConfig,
    PythonRuntimeAuthorityConfig,
    PythonToolHostAttachmentConfig,
    RuntimeConfig,
)
from magi_agent.evidence.observed_egress import (
    LiveEgressTelemetryEvidenceProvider,
    LocalObservedEgressEvidenceProvider,
    ObservedEgressEvidence,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
)


def make_runtime() -> OpenMagiRuntime:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )
    return OpenMagiRuntime(config=config)


def test_health_returns_ts_compatible_lean_payload() -> None:
    client = TestClient(create_app(make_runtime()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "botId": "bot-test",
        "runtime": "magi-agent",
        "version": "0.1.0-adk-scaffold",
        "buildSha": "sha-test",
    }


def test_healthz_adds_runtime_engine_without_changing_runtime_identity() -> None:
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["botId"] == "bot-test"
    assert body["runtime"] == "magi-agent"
    assert body["runtimeEngine"] == "adk-python"
    assert body["adk"]["available"] is True
    assert body["adk"]["invoked"] is False


def test_healthz_exposes_explicit_false_authority_fields() -> None:
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["toolHostActive"] is False
    assert body["memoryProviderActive"] is False
    assert body["transcriptWritesAllowed"] is False
    assert body["sseWritesAllowed"] is False
    assert body["channelWritesAllowed"] is False
    assert body["dbWritesAllowed"] is False
    assert body["workspaceMutationAllowed"] is False
    assert body["childExecutionAllowed"] is False
    assert body["missionRuntimeAllowed"] is False
    assert body["evidenceBlockModeAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert body["contextContinuity"]["continuityEnabled"] is False
    assert body["contextContinuity"]["continuityCanaryReady"] is False
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is True
    assert (
        body["gate8Readiness"]["reasonCode"]
        == "pre_gate8_continuity_canary_missing"
    )


def test_healthz_keeps_user_visible_authority_false_without_active_chat_gate() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        ),
    )
    runtime = OpenMagiRuntime(config=config)
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False


def test_healthz_reports_user_visible_authority_only_with_active_chat_gate() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        ),
    )
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest="sha256:82e4e56db648bc081311887c362e565c68f74411ce44855aba61af697e57bd86",
        selectedOwnerUserIdDigest="sha256:d59c3eb10fe2b0cacea2b080885863e3286d9a6d352269b822fd5ebef3d22e15",
        environment="production",
        environmentAllowlist=("production",),
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["userVisibleOutputAllowed"] is True
    assert body["canaryRoutingAllowed"] is True
    assert body["status"] == "python_ready"
    assert body["fallbackStatus"] == "none"
    assert body["responseAuthority"] == "python"
    assert body["authority"] == {
        "userVisibleOutputAllowed": True,
        "canaryRoutingAllowed": True,
        "memoryWriteAllowed": False,
        "toolDispatchAllowed": False,
        "transcriptWritesAllowed": False,
        "sseWritesAllowed": False,
        "channelWritesAllowed": False,
        "dbWritesAllowed": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "evidenceBlockModeAllowed": False,
    }
    assert body["safety"] == {
        "toolsActive": False,
        "memoryProviderActive": False,
        "browserActive": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "telegramDeliveryAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "evidenceBlockModeAllowed": False,
        "productionTranscriptWritesAllowed": False,
        "productionSseWritesAllowed": False,
        "productionDbWritesAllowed": False,
    }


def test_healthz_projects_selected_full_toolhost_readiness_for_active_chat_gate() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        ),
    )
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest="sha256:82e4e56db648bc081311887c362e565c68f74411ce44855aba61af697e57bd86",
        selectedOwnerUserIdDigest="sha256:d59c3eb10fe2b0cacea2b080885863e3286d9a6d352269b822fd5ebef3d22e15",
        environment="production",
        environmentAllowlist=("production",),
    )
    runtime.gate5b_full_toolhost_config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": "sha256:82e4e56db648bc081311887c362e565c68f74411ce44855aba61af697e57bd86",
            "selectedOwnerDigest": "sha256:d59c3eb10fe2b0cacea2b080885863e3286d9a6d352269b822fd5ebef3d22e15",
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )

    response = TestClient(create_app(runtime)).get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["responseAuthority"] == "python"
    assert body["authority"]["toolDispatchAllowed"] is True
    assert body["authority"]["selectedWorkspaceMutationAllowed"] is True
    assert body["authority"]["productionWorkspaceMutationAllowed"] is False
    assert body["authority"]["workspaceMutationAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert body["safety"]["toolsActive"] is True
    assert body["safety"]["toolHostMode"] == "selected_full_toolhost"
    assert body["safety"]["selectedWorkspaceMutationAllowed"] is True
    assert body["safety"]["productionWorkspaceMutationAllowed"] is False
    assert body["safety"]["workspaceMutationAllowed"] is False
    assert {"FileWrite", "FileEdit", "PatchApply", "Bash"}.issubset(
        set(body["safety"]["allowedToolNames"])
    )
    assert body["tooling"]["mode"] == "selected_full_toolhost"
    assert body["tooling"]["productionAttached"] is False
    assert body["tooling"]["forbiddenToolsExposed"] == []


def test_healthz_keeps_authority_false_when_chat_gate_identity_mismatches() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        ),
    )
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest="sha256:" + "0" * 64,
        selectedOwnerUserIdDigest="sha256:d59c3eb10fe2b0cacea2b080885863e3286d9a6d352269b822fd5ebef3d22e15",
        environment="production",
        environmentAllowlist=("production",),
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False


def test_healthz_keeps_shadow_attachment_configs_inactive() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        memory=PythonMemoryAdapterConfig(
            enabled=True,
            mode="readonly_local",
            adapter="hipocampus_qmd_readonly",
        ),
        toolhost=PythonToolHostAttachmentConfig(
            enabled=True,
            mode="shadow_readonly",
        ),
    )
    client = TestClient(create_app(OpenMagiRuntime(config=config)))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["toolHostActive"] is False
    assert body["memoryProviderActive"] is False
    assert body["adk"]["invoked"] is False


def test_healthz_reports_context_continuity_local_diagnostic_not_gate8_ready() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        contextContinuity=PythonContextContinuityConfig(
            enabled=True,
            mode="local_diagnostic",
            importedEventCount=4,
            rejectedEntryCount=1,
            compactionApplied=True,
            projectionDigestPresent=True,
            sourceTranscriptHeadDigestPresent=True,
            reasonCodes=("committed_history_imported",),
        ),
    )
    client = TestClient(create_app(OpenMagiRuntime(config=config)))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    continuity = body["contextContinuity"]
    assert continuity["continuityEnabled"] is True
    assert continuity["continuityCanaryReady"] is False
    assert continuity["importedEventCount"] == 4
    assert continuity["rejectedEntryCount"] == 1
    assert continuity["compactionApplied"] is True
    assert continuity["projectionDigestPresent"] is True
    assert continuity["sourceTranscriptHeadDigestPresent"] is True
    assert continuity["reasonCodes"] == ["committed_history_imported"]
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is True
    assert body["userVisibleOutputAllowed"] is False


def test_healthz_keeps_selected_canary_metadata_blocked_without_verified_evidence() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        contextContinuity=PythonContextContinuityConfig(
            enabled=True,
            mode="selected_canary",
            canaryStatus="pass",
            importedEventCount=3,
            projectionDigestPresent=True,
            modelVisibleDigestPresent=True,
            sourceTranscriptHeadDigestPresent=True,
            fallbackStatus="none",
        ),
    )
    client = TestClient(create_app(OpenMagiRuntime(config=config)))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["contextContinuity"]["continuityCanaryReady"] is False
    assert body["contextContinuity"]["canaryEvidenceVerified"] is False
    assert body["contextContinuity"]["modelVisibleDigestPresent"] is True
    assert body["contextContinuity"]["fallbackStatus"] == "none"
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is True
    assert (
        body["gate8Readiness"]["reasonCode"]
        == "pre_gate8_continuity_evidence_unverified"
    )
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False


def test_healthz_keeps_manual_context_continuity_canary_pass_blocked_without_authority() -> None:
    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        contextContinuity=PythonContextContinuityConfig(
            enabled=True,
            mode="selected_canary",
            canaryStatus="pass",
            importedEventCount=3,
            projectionDigestPresent=True,
            modelVisibleDigestPresent=True,
            sourceTranscriptHeadDigestPresent=True,
            canaryEvidenceVerified=True,
            fallbackStatus="none",
        ),
    )
    client = TestClient(create_app(OpenMagiRuntime(config=config)))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["contextContinuity"]["continuityCanaryReady"] is False
    assert body["contextContinuity"]["canaryEvidenceVerified"] is False
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is True
    assert body["gate8Readiness"]["reasonCode"] == "pre_gate8_continuity_evidence_unverified"
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False


def test_healthz_reports_gate1a_observed_egress_evidence_default_off() -> None:
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["observedEgressEvidenceAvailable"] is False
    assert body["gate1aEgressEvidenceReady"] is False
    assert body["egressEvidenceSource"] == "none"
    assert body["egressEvidenceReadinessReason"] == "no_live_correlation_source_configured"


def test_healthz_local_fixture_evidence_is_available_but_not_activation_ready() -> None:
    runtime = make_runtime()
    runtime.gate1a_observed_egress_evidence_provider = LocalObservedEgressEvidenceProvider(
        ObservedEgressEvidence.model_validate(
            {
                "requestDigest": "sha256:" + "a" * 64,
                "providerRequestCount": 1,
                "egressTunnelCount": 1,
                "egressHostClasses": ["gemini_proxy"],
                "observedWindowStart": "2026-05-24T10:00:00.000Z",
                "observedWindowEnd": "2026-05-24T10:00:01.000Z",
                "evidenceSource": "local_fixture",
                "redactionStatus": "public_safe",
                "decisionReason": "observed_gemini_proxy_tunnel",
            }
        )
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["observedEgressEvidenceAvailable"] is True
    assert body["gate1aEgressEvidenceReady"] is False
    assert body["egressEvidenceSource"] == "local_fixture"
    assert body["egressEvidenceReadinessReason"] == "local_fixture_not_activation_ready"


def test_healthz_live_egress_telemetry_reports_ready_only_when_source_is_available(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    runtime = make_runtime()
    runtime.gate1a_observed_egress_evidence_provider = LiveEgressTelemetryEvidenceProvider(
        telemetry_path,
        proxy_url="http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080",
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["observedEgressEvidenceAvailable"] is True
    assert body["gate1aEgressEvidenceReady"] is True
    assert body["egressEvidenceSource"] == "gate5b_egress_proxy"
    assert body["egressEvidenceReadinessReason"] == "live_correlation_source_ready"


def test_healthz_returns_503_when_runtime_status_is_degraded() -> None:
    runtime = OpenMagiRuntime(
        config=make_runtime().config,
        adk_boundary=AdkPrimitiveBoundary(
            available=False,
            missing=("google.adk.agents.Agent",),
        ),
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_healthz_includes_composio_default_disabled_metadata(monkeypatch) -> None:
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("MAGI_COMPOSIO_ENABLED", raising=False)
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    composio = response.json()["composio"]
    assert composio["configured"] is False
    assert composio["active"] is False
    assert composio["disabledReason"] == "disabled_by_config"


def test_healthz_composio_metadata_does_not_leak_api_key(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setenv("USER_ID", "user-test")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setattr(
        "magi_agent.composio.health.composio_package_available",
        lambda: True,
    )
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    rendered = str(response.json())
    assert "cp_test_secret" not in rendered
    assert response.json()["composio"]["active"] is True


def test_healthz_reports_composio_package_missing_without_crashing(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setattr(
        "magi_agent.composio.health.composio_package_available",
        lambda: False,
    )
    client = TestClient(create_app(make_runtime()))

    response = client.get("/healthz")

    assert response.status_code == 200
    composio = response.json()["composio"]
    assert composio["active"] is False
    assert composio["disabledReason"] == "missing_python_package"
