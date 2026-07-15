from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import (
    parse_gate5b4c3_shadow_generation_route_env,
    parse_runtime_env,
)
from magi_agent.evidence.observed_egress import (
    build_gate1a_observed_egress_evidence_provider_from_env,
)
from magi_agent.research.research_first_canary import (
    build_research_first_selected_response,
    research_first_selected_canary_active,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat import (
    build_gate5b_user_visible_chat_route_config_from_env,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-research-first",
        "USER_ID": "owner-research-first",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
        "CORE_AGENT_PYTHON_CHAT_ROUTE": "on",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "user_visible_canary",
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "1",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "1",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST": _digest(
            "bot-research-first"
        ),
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-research-first"
        ),
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST": "production",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "selected_canary",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS": "1",
    }
    env.update(overrides)
    return env


def _research_payload(content: str = "Inspect the read-only source path.") -> dict[str, object]:
    return {
        "messages": [{"role": "user", "content": content}],
        "runtimeContext": {
            "explicitRecipeSelection": {
                "mode": "this_turn",
                "requiredRecipeRefs": [{"recipeId": "openmagi.research"}],
                "allowAdditionalAutoRecipes": True,
            }
        },
    }


def _runtime(env: dict[str, str]) -> OpenMagiRuntime:
    config = parse_runtime_env(env)
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b_user_visible_chat_route_config = (
        build_gate5b_user_visible_chat_route_config_from_env(env, config)
    )
    runtime.gate5b4c3_shadow_generation_route_config = (
        parse_gate5b4c3_shadow_generation_route_env(env)
    )
    runtime.gate1a_observed_egress_evidence_provider = (
        build_gate1a_observed_egress_evidence_provider_from_env(env)
    )
    return runtime


def test_research_first_selected_canary_is_default_off() -> None:
    assert research_first_selected_canary_active(_research_payload(), env={}) is False
    assert (
        research_first_selected_canary_active(
            _research_payload(),
            env={"CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED": "1"},
        )
        is True
    )
    assert (
        research_first_selected_canary_active(
            _research_payload(),
            env={
                "CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED": "1",
                "CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_KILL_SWITCH": "1",
            },
        )
        is False
    )
    assert (
        research_first_selected_canary_active(
            {"messages": [{"role": "user", "content": "hello"}]},
            env={"CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED": "1"},
        )
        is False
    )


def test_research_first_selected_response_requires_source_evidence_and_repairs_unsupported_claim() -> None:
    result = build_research_first_selected_response(
        _research_payload("Include one unsupported claim so the verifier can repair it."),
        bot_id="bot-research-first",
        user_id="owner-research-first",
        environment="production",
        now_ms=1_800_000_000_000,
        request_digest=_digest("request"),
    )

    assert result.final_gate_result.ok is True
    assert result.final_gate_result.status == "passed"
    assert result.metadata["unsupportedClaimHandling"]["status"] == "repaired"
    assert result.metadata["unsupportedClaimHandling"]["omittedClaimCount"] == 1
    assert "unsupported_claim_missing_citation_ref" in result.metadata[
        "unsupportedClaimHandling"
    ]["blockedReasonCodes"]
    assert "unsupported" not in result.content.lower()
    assert result.content.count("src_1") >= 2

    sources = result.metadata["sourceLedger"]["sources"]
    assert sources == [
        {
            "sourceId": "src_1",
            "sourceRef": "ref:src_1",
            "contentHash": result.final_gate_result.source_ledger.snapshot()[0].content_hash,
            "retrievedAt": 1_800_000_000_000,
            "inspected": True,
            "kind": "external_doc",
        }
    ]

    event_types = [event["type"] for event in result.public_events]
    assert "source_inspected" in event_types
    assert event_types.count("rule_check") >= 3
    encoded_events = json.dumps(result.public_events, sort_keys=True)
    assert "claim-citation-gate" in encoded_events
    assert "verifier:research-source-evidence" in encoded_events
    assert "final_projection:research-first" in encoded_events
    assert "http://" not in encoded_events
    assert "Authorization" not in encoded_events
    assert "/Users/" not in encoded_events


def test_research_first_selected_endpoint_ignores_local_dashboard_route_env(
    monkeypatch,
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    counter_path = tmp_path / "gate8-research-first-counters.json"
    request_digest = _digest("research-first-request-with-local-route-env")
    env = _base_env(
        CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED="1",
        CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_KILL_SWITCH="0",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED="1",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH=str(counter_path),
        CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE="egress_proxy_telemetry",
        CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH=str(telemetry_path),
        CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE="proxy_connect_headers",
        CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL=(
            "http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080"
        ),
    )
    monkeypatch.setenv("MAGI_AGENT_LOCAL_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_KILL_SWITCH", "0")

    response = TestClient(create_app(_runtime(env))).post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer gateway-token",
            "X-Gate5B-Canary-Request-Digest": request_digest,
        },
        json=_research_payload(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["researchFirst"]["requestDigest"] == request_digest


