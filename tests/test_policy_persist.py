"""Persist orchestration: assembled plan -> producer + gate + Policy."""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.policies import get_policy
from magi_agent.customize.policy_persist import PolicyPersistError, persist_policy_plan
from magi_agent.customize.store import customize_path, load_overrides
from magi_agent.packs.dashboard_authored import read_sidecar


def _producer(**over) -> dict:
    base = {
        "id": "source-credibility",
        "label": "records credibility",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "domainAllowlist": ["sec.gov"]},
        "action": "audit",
        "emitsEvidenceType": "custom:SourceCredibility",
    }
    base.update(over)
    return base


def _gate(**require_over) -> dict:
    require = {"evidenceType": "custom:SourceCredibility", "producerRuleId": "source-credibility"}
    require.update(require_over)
    return {
        "id": "cr_source_credibility_gate",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "tool_perm",
            "payload": {
                "match": {"tool": "execute_trade"},
                "decision": "deny",
                "requireEvidence": require,
            },
        },
        "firesAt": "before_tool_use",
        "action": "block",
    }


def _plan(**over) -> dict:
    base = {
        "intent": "require a credible source before trading",
        "producer": _producer(),
        "gate": _gate(),
        "binding": {
            "producerRuleId": "source-credibility",
            "gateRuleId": "cr_source_credibility_gate",
            "evidenceType": "custom:SourceCredibility",
        },
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


def test_persist_writes_all_three_stores(tmp_path: Path) -> None:
    saved = persist_policy_plan(_plan())
    assert saved == {
        "policyId": "source-credibility",
        "producerId": "source-credibility",
        "gateId": "cr_source_credibility_gate",
    }
    # Producer -> dashboard sidecar.
    checks = read_sidecar(tmp_path / "dashboard-authored")
    assert [c.id for c in checks] == ["source-credibility"]
    assert checks[0].emits_evidence_type == "custom:SourceCredibility"
    # Gate -> custom_rules.
    rules = load_overrides(customize_path())["verification"]["custom_rules"]
    assert any(r["id"] == "cr_source_credibility_gate" for r in rules)
    # Policy record -> policies, with the binding.
    policy = get_policy("source-credibility")
    assert policy is not None
    assert policy.rule_ids == ("cr_source_credibility_gate",)
    assert policy.binding is not None
    assert policy.binding.producer_rule_id == "source-credibility"
    assert policy.review is not None and policy.review.verdict == "unreviewed"


def test_persist_is_idempotent(tmp_path: Path) -> None:
    persist_policy_plan(_plan())
    persist_policy_plan(_plan())  # re-save same ids
    checks = read_sidecar(tmp_path / "dashboard-authored")
    assert len([c for c in checks if c.id == "source-credibility"]) == 1
    rules = load_overrides(customize_path())["verification"]["custom_rules"]
    assert len([r for r in rules if r["id"] == "cr_source_credibility_gate"]) == 1


def test_persist_rejects_unsound_plan_without_writing(tmp_path: Path) -> None:
    # A dangling gate (binds to a producer id the plan does not define).
    bad = _plan(gate=_gate(producerRuleId="cr_ghost"))
    with pytest.raises(PolicyPersistError):
        persist_policy_plan(bad)
    # Nothing was written (no partial state).
    assert read_sidecar(tmp_path / "dashboard-authored") == []
    assert get_policy("source-credibility") is None


def test_persist_rejects_non_object() -> None:
    with pytest.raises(PolicyPersistError):
        persist_policy_plan("nope")


def test_persist_requires_producer_gate_binding() -> None:
    with pytest.raises(PolicyPersistError):
        persist_policy_plan({"intent": "x", "gate": _gate()})  # no producer/binding


# --- POST /v1/app/policies/from-plan endpoint ---

from fastapi.testclient import TestClient  # noqa: E402

from magi_agent.app import create_app  # noqa: E402
from magi_agent.config.models import BuildInfo, RuntimeConfig  # noqa: E402
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime  # noqa: E402

_TOKEN = "test-gateway-token"


def _client() -> TestClient:
    rt = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot", user_id="local-user", gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local", chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0", model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )
    c = TestClient(create_app(rt))
    c.headers.update({"x-gateway-token": _TOKEN})
    return c


def test_from_plan_endpoint_persists(tmp_path: Path) -> None:
    resp = _client().post("/v1/app/policies/from-plan", json={"plan": _plan()})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["policyId"] == "source-credibility"
    assert get_policy("source-credibility") is not None


def test_from_plan_endpoint_accepts_bare_plan(tmp_path: Path) -> None:
    # Body may be the plan directly (no "plan" wrapper).
    resp = _client().post("/v1/app/policies/from-plan", json=_plan())
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_from_plan_endpoint_rejects_unsound(tmp_path: Path) -> None:
    resp = _client().post("/v1/app/policies/from-plan", json={"plan": _plan(gate=_gate(producerRuleId="cr_ghost"))})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_plan"


def test_from_plan_endpoint_requires_auth(tmp_path: Path) -> None:
    rt = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="b", user_id="u", gateway_token=_TOKEN,
            api_proxy_url="http://a", chat_proxy_url="http://c",
            redis_url="redis://r:6379/0", model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="s"),
        )
    )
    assert TestClient(create_app(rt)).post("/v1/app/policies/from-plan", json=_plan()).status_code == 401
