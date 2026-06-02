from __future__ import annotations

import json
from pathlib import Path


def test_ladder_registry_defines_gate_0_to_9_with_precise_activation_stages() -> None:
    from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

    registry = build_canary_gate_registry()

    assert tuple(gate.gate_id for gate in registry.gates) == tuple(range(10))
    assert registry.gates[0].slug == "gate0_text"
    assert registry.gates[1].slug == "gate1_readonly_tools"
    assert registry.gates[2].slug == "gate2_coding_workspace"
    assert registry.gates[3].slug == "gate3_web_research_browser"
    assert registry.gates[4].slug == "gate4_delivery_channel"
    assert registry.gates[5].slug == "gate5_scheduler_cron_mission"
    assert registry.gates[6].slug == "gate6_memory"
    assert registry.gates[7].slug == "gate7_child_workspace_adoption"
    assert registry.gates[8].slug == "gate8_full_selected_python_authority"
    assert registry.gates[9].slug == "gate9_broader_canary_replacement"
    assert all(gate.default_off is True for gate in registry.gates)
    assert registry.gates[1].required_stage == "local_live_enabled_test_only"
    assert registry.gates[9].required_stage == "readiness_report_only"


def test_ladder_default_off_run_restores_closed_gate_without_api_call() -> None:
    from magi_agent.gates.api_canary_ladder import (
        CanaryHarnessConfig,
        SyntheticCanaryHarness,
        build_canary_gate_registry,
    )

    calls: list[object] = []
    harness = SyntheticCanaryHarness(
        registry=build_canary_gate_registry(),
        config=CanaryHarnessConfig(),
        api_client=lambda request: calls.append(request) or {"status": "python_ready"},
    )

    report = harness.run_gate(1)

    assert calls == []
    assert report.status == "skipped"
    assert report.reason == "gate_disabled"
    assert report.route_decision == "typescript_fallback"
    assert report.default_off_restored is True
    assert report.production_writes_enabled is False
    assert report.user_visible_output_enabled is False


def test_ladder_gate0_and_gate1_mocked_api_loop_validate_receipts_and_restore_defaults(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.api_canary_ladder import (
        CanaryHarnessConfig,
        SyntheticCanaryHarness,
        build_canary_gate_registry,
    )

    requests: list[object] = []

    def api_client(request: object) -> dict[str, object]:
        requests.append(request)
        return {
            "status": "python_ready",
            "fallbackStatus": "none",
            "responseAuthority": "python",
            "eventFrames": [
                {"type": "response_clear"},
                {"type": "delta", "text": "ok"},
                {"type": "done", "marker": "[DONE]"},
            ],
            "routeDecision": "python_selected",
            "receipts": [
                {
                    "requestDigest": "sha256:" + "a" * 64,
                    "status": "ok",
                    "boundedOutputDigest": "sha256:" + "b" * 64,
                }
            ],
            "counterStatus": "served_to_client",
            "egressEvidence": {"networkFetched": False},
        }

    harness = SyntheticCanaryHarness(
        registry=build_canary_gate_registry(),
        config=CanaryHarnessConfig(
            enabled=True,
            localApiLoopEnabled=True,
            scopedCanaryTokenRef="canary-token:test-only",
            selectedBotDigest="sha256:" + "1" * 64,
            selectedOwnerDigest="sha256:" + "2" * 64,
            environment="local",
            reportDirectory=tmp_path,
        ),
        api_client=api_client,
    )

    gate0 = harness.run_gate(0)
    gate1 = harness.run_gate(1)

    assert len(requests) == 2
    assert gate0.status == "passed"
    assert gate1.status == "passed"
    assert gate0.validated_sse_frames is True
    assert gate1.validated_receipts is True
    assert gate1.validated_counters is True
    assert gate1.validated_egress_evidence is True
    assert gate1.default_off_restored is True
    assert (tmp_path / "gate1_readonly_tools.json").exists()

    serialized = json.dumps(gate1.model_dump(by_alias=True), sort_keys=True)
    assert "canary-token:test-only" not in serialized
    assert "cookie" not in serialized.lower()
    assert "authorization" not in serialized.lower()


def test_ladder_can_build_scoped_chat_proxy_synthetic_request_without_user_credentials() -> None:
    from magi_agent.gates.api_canary_ladder import (
        build_scoped_canary_test_request,
    )

    body = json.dumps(
        {"messages": [{"role": "user", "content": "synthetic prompt only"}]},
        separators=(",", ":"),
    )

    request = build_scoped_canary_test_request(
        botId="61df1790-281a-4420-acef-46e9038b4252",
        ownerUserId="did:privy:test-owner",
        environment="production",
        gate="gate1_readonly_tools",
        body=body,
        issuer="openmagi-tests",
        audience="chat-proxy",
        secret="unit-test-canary-secret-value",
        nonce="nonce-1",
        nowMs=1_779_380_000_000,
    )

    assert request.method == "POST"
    assert request.path == "/v1/chat/61df1790-281a-4420-acef-46e9038b4252/completions"
    assert request.body == body
    assert request.body_digest.startswith("sha256:")
    assert request.token_digest.startswith("sha256:")
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-gate-canary-test-token"].count(".") == 2
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers

    serialized = json.dumps(request.model_dump(by_alias=True), sort_keys=True)
    assert "unit-test-canary-secret-value" not in serialized
    assert "browser" not in serialized.lower()
    assert "keychain" not in serialized.lower()
    assert "privy token" not in serialized.lower()


def test_gate2_to_gate9_readiness_packages_are_precise_not_vague() -> None:
    from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

    registry = build_canary_gate_registry()

    for gate in registry.gates[2:]:
        package = gate.readiness_package
        assert package is not None
        assert package.implementation_blockers
        assert package.required_test_sinks
        assert package.activation_env
        assert package.counter_requirements
        assert package.rollback
        assert package.stop_conditions
        dumped = json.dumps(package.model_dump(by_alias=True), sort_keys=True).lower()
        for vague in ("tbd", "todo", "later", "future work"):
            assert vague not in dumped

    gate9 = registry.gates[9].readiness_package
    assert gate9 is not None
    assert "separate rollout approval" in " ".join(gate9.stop_conditions)


def test_gate8_requires_session_continuity_canary_before_full_authority() -> None:
    from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

    gate8 = build_canary_gate_registry().by_id(8)
    package = gate8.readiness_package

    assert package is not None
    blockers = " ".join(package.implementation_blockers)
    sinks = " ".join(package.required_test_sinks)
    counters = " ".join(package.counter_requirements)
    stops = " ".join(package.stop_conditions)

    assert "SessionContinuityBoundary" in blockers
    assert "ADK SessionService" in blockers
    assert "아까 말한 그거" in blockers
    assert "raw full transcript" in stops
    assert "hidden reasoning" in stops
    assert "raw tool logs" in stops
    assert "child transcripts" in stops
    assert "credentials" in stops
    assert "private paths" in stops
    assert "unapproved memory" in stops
    assert "pre-Gate8 multi-turn session continuity canary" in sinks
    for field in (
        "continuityCanaryStatus",
        "importedEventCount",
        "rejectedEntryCount",
        "compactionApplied",
        "projectionDigest",
        "modelVisibleDigest",
        "sourceTranscriptHeadDigest",
        "reasonCodes",
    ):
        assert field in counters
    assert "continuityCanaryStatus=pass" in stops


def test_ladder_import_boundary_avoids_deploy_routing_browser_and_network_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "gates"
        / "api_canary_ladder.py"
    )
    source = module_path.read_text(encoding="utf-8")

    for forbidden in (
        "magi_agent.transport.chat",
        "magi_agent.transport.sse",
        "magi_agent.browser",
        "magi_agent.web_acquisition",
        "magi_agent.memory",
        "magi_agent.channels",
        "requests",
        "httpx",
        "kubectl",
        "runtime-selector",
        "supabase",
    ):
        assert forbidden not in source
