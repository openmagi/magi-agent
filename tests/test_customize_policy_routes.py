"""Tests for the policy CRUD endpoints under /v1/app/policies (phase 1b)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"

_POLICY = {
    "displayName": "Verify source before high-risk tool",
    "intent": "require a credible source before a high-risk tool",
    "ruleIds": ["cr_gate"],
}


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _authed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_policies_requires_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/policies").status_code == 401


def test_list_only_builtins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.get("/v1/app/policies")
    assert resp.status_code == 200
    # An empty store still surfaces the first-party builtin(s).
    body = resp.json()
    assert [p["id"] for p in body["policies"]] == ["source_citation", "verify_before_replying"]
    assert body["policies"][0]["origin"] == "builtin"


def test_upsert_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/policies/verify-source", json=_POLICY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy"]["id"] == "verify-source"
    assert body["policy"]["ruleIds"] == ["cr_gate"]
    # The list is builtins + stored, sorted by id.
    assert [p["id"] for p in body["policies"]] == [
        "source_citation",
        "verify-source",
        "verify_before_replying",
    ]
    # Follow-up GET agrees.
    listing = client.get("/v1/app/policies").json()
    assert [p["id"] for p in listing["policies"]] == [
        "source_citation",
        "verify-source",
        "verify_before_replying",
    ]


def test_path_id_is_authoritative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    # A body-supplied id must not override the path id.
    resp = client.put("/v1/app/policies/from-path", json={**_POLICY, "id": "from-body"})
    assert resp.status_code == 200
    assert resp.json()["policy"]["id"] == "from-path"


def test_upsert_with_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    policy = {
        **_POLICY,
        "ruleIds": ["cr_gate"],
        "binding": {
            "producerRuleId": "credible-source",
            "gateRuleId": "cr_gate",
            "evidenceType": "custom:SourceCredibility",
        },
    }
    resp = client.put("/v1/app/policies/verify-source", json=policy)
    assert resp.status_code == 200
    assert resp.json()["policy"]["binding"]["evidenceType"] == "custom:SourceCredibility"


def test_upsert_invalid_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/policies/bad", json={"displayName": ""})  # empty name
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_policy"


def test_upsert_non_object_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/policies/x", json=["not", "an", "object"])
    assert resp.status_code == 400
    assert resp.json()["error"] == "object_required"


def test_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.put("/v1/app/policies/verify-source", json=_POLICY)
    resp = client.request("DELETE", "/v1/app/policies/verify-source")
    assert resp.status_code == 200
    # Deleting the user policy leaves the first-party builtins intact.
    assert [p["id"] for p in resp.json()["policies"]] == ["source_citation", "verify_before_replying"]
    # Idempotent second delete.
    assert client.request("DELETE", "/v1/app/policies/verify-source").status_code == 200
    # Deleting a builtin id is a no-op: the first-party policies stay present.
    resp2 = client.request("DELETE", "/v1/app/policies/source_citation")
    assert resp2.status_code == 200
    assert [p["id"] for p in resp2.json()["policies"]] == ["source_citation", "verify_before_replying"]


def test_migrate_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed two grouped custom rules, then migrate them 1:1 into a policy.
    from magi_agent.customize.store import set_custom_rules_group

    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    rule = {
        "id": "cr_g1",
        "scope": "always",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test"}},
        "firesAt": "pre_final",
        "action": "block",
    }
    set_custom_rules_group([rule], "my-group", path=cfile)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.post("/v1/app/policies/migrate")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    # The migrated policy plus the always-present first-party builtins.
    assert sorted(p["id"] for p in resp.json()["policies"]) == [
        "my-group",
        "source_citation",
        "verify_before_replying",
    ]


# --- U4: PATCH /v1/app/policies/{id} enabled cascade ----------------------


def _seed_custom_rule(cfile: Path, rule_id: str, *, enabled: bool = True) -> None:
    from magi_agent.customize.store import set_custom_rule

    set_custom_rule(
        {
            "id": rule_id,
            "scope": "always",
            "enabled": enabled,
            "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test"}},
            "firesAt": "pre_final",
            "action": "block",
        },
        path=cfile,
    )


def test_patch_policy_cascades_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = tmp_path / "customize.json"
    client = _authed(tmp_path, monkeypatch)
    _seed_custom_rule(cfile, "cr_a")
    _seed_custom_rule(cfile, "cr_b")
    client.put(
        "/v1/app/policies/grp",
        json={"displayName": "Group", "ruleIds": ["cr_a", "cr_b"]},
    )
    resp = client.patch("/v1/app/policies/grp", json={"enabled": False})
    assert resp.status_code == 200
    from magi_agent.customize.store import load_overrides

    rules = load_overrides(cfile)["verification"]["custom_rules"]
    by_id = {r["id"]: r for r in rules}
    assert by_id["cr_a"]["enabled"] is False
    assert by_id["cr_b"]["enabled"] is False


def test_patch_policy_unknown_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.patch("/v1/app/policies/does-not-exist", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_policy_builtin_floor_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    # A first-party policy is not toggled through this route (it keeps its own
    # builtin-policies / control-plane routes). Reject with a 4xx.
    resp = client.patch("/v1/app/policies/source_citation", json={"enabled": False})
    assert resp.status_code in (400, 409)


def test_patch_policy_requires_enabled_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = tmp_path / "customize.json"
    client = _authed(tmp_path, monkeypatch)
    _seed_custom_rule(cfile, "cr_a")
    client.put("/v1/app/policies/grp", json={"displayName": "G", "ruleIds": ["cr_a"]})
    resp = client.patch("/v1/app/policies/grp", json={"enabled": "yes"})
    assert resp.status_code == 400


def test_patch_policy_requires_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.patch("/v1/app/policies/x", json={"enabled": False}).status_code == 401


def test_patch_policy_cascade_leaves_non_members_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding (PR-1): pin the cascade's SCOPE, not only its effect -
    a rule outside the policy must never be flipped."""
    cfile = tmp_path / "customize.json"
    client = _authed(tmp_path, monkeypatch)
    _seed_custom_rule(cfile, "cr_a")
    _seed_custom_rule(cfile, "cr_z")  # NOT a member
    client.put(
        "/v1/app/policies/grp",
        json={"displayName": "Group", "ruleIds": ["cr_a"]},
    )
    resp = client.patch("/v1/app/policies/grp", json={"enabled": False})
    assert resp.status_code == 200
    from magi_agent.customize.store import load_overrides

    by_id = {
        r["id"]: r for r in load_overrides(cfile)["verification"]["custom_rules"]
    }
    assert by_id["cr_a"]["enabled"] is False
    assert by_id["cr_z"]["enabled"] is True  # untouched


def test_patch_policy_disableable_builtin_also_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify_before_replying is userDisableable, but its opt-out lives on the
    builtin-policies PATCH route (#1403), NOT the member-cascade route: its
    members are runtime-native, so a cascade here would be a silent no-op
    pretending to work."""
    client = _authed(tmp_path, monkeypatch)
    resp = client.patch(
        "/v1/app/policies/verify_before_replying", json={"enabled": False}
    )
    assert resp.status_code == 409


def test_get_customize_backfills_orphan_rules_into_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding (PR-1): route-level pin for the write-on-read backfill -
    opening Customize promotes orphan rules into policies, idempotently."""
    cfile = tmp_path / "customize.json"
    client = _authed(tmp_path, monkeypatch)
    _seed_custom_rule(cfile, "cr_orphan")  # seeded directly, bypasses U1
    resp = client.get("/v1/app/customize")
    assert resp.status_code == 200
    policies = resp.json()["catalog"]["policies"]
    owning = [p for p in policies if "cr_orphan" in p["ruleIds"]]
    assert len(owning) == 1, policies
    # Idempotent: a second GET creates nothing new.
    resp2 = client.get("/v1/app/customize")
    policies2 = resp2.json()["catalog"]["policies"]
    assert len(policies2) == len(policies)


def test_get_customize_survives_corrupt_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = tmp_path / "customize.json"
    client = _authed(tmp_path, monkeypatch)
    cfile.write_text("{ not valid json !!", encoding="utf-8")
    resp = client.get("/v1/app/customize")
    assert resp.status_code == 200
