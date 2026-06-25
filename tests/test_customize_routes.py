"""Tests for GET /v1/app/customize and PATCH /v1/app/customize/tools/{name} endpoints."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"


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


# Alias for tests that need to accept a tmp_path argument (unused, kept for compat)
def _build_runtime(tmp_path=None, *, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path=None) -> TestClient:
    """Unauthenticated test client (no gateway token header)."""
    return TestClient(create_app(_build_runtime(tmp_path)))


def test_patch_tool_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token header
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"enabled": False})
    assert resp.status_code == 401


def test_patch_tool_persists_and_applies(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    # pick a real tool name from the runtime registry
    tool_name = runtime.tool_registry.list_all()[0].name
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(f"/v1/app/customize/tools/{tool_name}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["overrides"]["tools"][tool_name] is False
    # persisted to disk
    import json
    assert json.loads(cfile.read_text())["tools"][tool_name] is False
    # applied live
    assert runtime.tool_registry.resolve_registration(tool_name).enabled is False


def test_patch_tool_bad_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"nope": 1})
    assert resp.status_code == 400


def test_customize_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    # No token header — must get 401
    res = client.get("/v1/app/customize")
    assert res.status_code == 401


def test_patch_tool_unknown_name_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/tools/__definitely_not_a_tool__", json={"enabled": False})
    assert resp.status_code == 404
    # nothing persisted
    import os
    cfile = tmp_path / "customize.json"
    assert not cfile.exists() or "__definitely_not_a_tool__" not in cfile.read_text()


def test_patch_verification_persists_and_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(
        "/v1/app/customize/verification/harness_presets/coding-verification",
        json={"enabled": False, "mode": "deterministic"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # harness_presets persist as explicit tri-state in preset_overrides
    assert body["overrides"]["verification"]["preset_overrides"]["coding-verification"] is False
    import json
    persisted = json.loads(cfile.read_text())["verification"]["preset_overrides"]
    assert persisted["coding-verification"] is False
    # applied live (flag on)
    assert runtime.customize_verification_policy.explicit_preset("coding-verification") is False


def test_patch_verification_unknown_kind_400(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/verification/bogus/x", json={"enabled": True})
    assert resp.status_code == 400


def test_patch_verification_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token header
    resp = client.patch(
        "/v1/app/customize/verification/harness_presets/answer_quality",
        json={"enabled": True},
    )
    assert resp.status_code == 401


def test_patch_verification_bad_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch(
        "/v1/app/customize/verification/harness_presets/answer_quality", json={"nope": 1}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# F-UX10 (2026-06-24) — Recipes write surface: per-recipe toggle on/off
# ---------------------------------------------------------------------------


def test_patch_recipe_enable_appends_to_allowlist(tmp_path, monkeypatch):
    """Enabling a real recipe id appends it to ``verification.recipes[]``."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch(
        "/v1/app/customize/verification/recipes/coding_evidence_gate",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "coding_evidence_gate" in body["overrides"]["verification"]["recipes"]
    import json

    persisted = json.loads(cfile.read_text())
    assert "coding_evidence_gate" in persisted["verification"]["recipes"]


def test_patch_recipe_disable_removes_from_allowlist(tmp_path, monkeypatch):
    """Disabling a previously-enabled recipe id removes it from the list."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    # Seed: enable two recipes so the disable below has something to remove
    # without leaving the list empty (empty == legacy no-op, indistinguishable
    # from "never wrote").
    client.patch(
        "/v1/app/customize/verification/recipes/coding_evidence_gate",
        json={"enabled": True},
    )
    client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": True},
    )
    resp = client.patch(
        "/v1/app/customize/verification/recipes/coding_evidence_gate",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    recipes = resp.json()["overrides"]["verification"]["recipes"]
    assert "coding_evidence_gate" not in recipes
    assert "research" in recipes


def test_patch_recipe_unknown_id_returns_404(tmp_path, monkeypatch):
    """A typo cannot silently land in the allowlist — unknown id ⇒ 404."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch(
        "/v1/app/customize/verification/recipes/__definitely_not_a_recipe__",
        json={"enabled": True},
    )
    assert resp.status_code == 404
    # Nothing persisted — the recipes list stays empty (or the file unwritten).
    if cfile.exists():
        import json

        persisted = json.loads(cfile.read_text())
        assert "__definitely_not_a_recipe__" not in persisted["verification"]["recipes"]


def test_patch_recipe_bad_body_returns_400(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch(
        "/v1/app/customize/verification/recipes/coding_evidence_gate",
        json={"nope": 1},
    )
    assert resp.status_code == 400


def test_patch_recipe_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token header
    resp = client.patch(
        "/v1/app/customize/verification/recipes/coding_evidence_gate",
        json={"enabled": True},
    )
    assert resp.status_code == 401


def test_put_rules_persists(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.put("/v1/app/customize/rules", json={"text": "Be terse."})
    assert resp.status_code == 200
    assert resp.json()["overrides"]["user_rules"] == "Be terse."
    import json
    assert json.loads(cfile.read_text())["user_rules"] == "Be terse."


def test_put_rules_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token
    resp = client.put("/v1/app/customize/rules", json={"text": "x"})
    assert resp.status_code == 401


def _valid_custom_rule():
    return {
        "scope": "coding",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test-run"}},
        "firesAt": "pre_final",
        "action": "block",
    }


def test_put_custom_rule_valid_persists_and_assigns_id(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.put("/v1/app/customize/custom-rules", json=_valid_custom_rule())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"].startswith("cr_")
    rules = body["overrides"]["verification"]["custom_rules"]
    assert len(rules) == 1 and rules[0]["id"] == body["id"]


def test_put_custom_rule_invalid_returns_400(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    bad = _valid_custom_rule()
    bad["action"] = "ask_approval"  # illegal for deterministic_ref
    resp = client.put("/v1/app/customize/custom-rules", json=bad)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_custom_rule"


def test_put_custom_rule_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token
    resp = client.put("/v1/app/customize/custom-rules", json=_valid_custom_rule())
    assert resp.status_code == 401


def test_delete_custom_rule_route(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    rid = client.put("/v1/app/customize/custom-rules", json=_valid_custom_rule()).json()["id"]
    resp = client.delete(f"/v1/app/customize/custom-rules/{rid}")
    assert resp.status_code == 200
    assert resp.json()["overrides"]["verification"]["custom_rules"] == []


def test_put_rules_bad_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.put("/v1/app/customize/rules", json={"nope": 1})
    assert resp.status_code == 400


def test_customize_returns_catalog_and_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    res = client.get("/v1/app/customize", headers={"x-gateway-token": _TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"catalog", "overrides"}
    assert set(body["catalog"].keys()) == {"verification", "tools", "controlPlane"}
    assert set(body["catalog"]["verification"].keys()) == {
        "recipes",
        "harnessPresets",
        "hooks",
        # PR-F-UX5 evidence vs verifier split; ``customRuleMenu`` kept as the
        # back-compat union of evidenceMenu + judgmentMenu.
        "customRuleMenu",
        "evidenceMenu",
        "judgmentMenu",
    }
    # Control-plane behavior catalog includes the facts-survey toggle.
    assert any(e["id"] == "facts-replan" for e in body["catalog"]["controlPlane"])
    # No customize.json → tools overrides default to empty dict
    assert body["overrides"]["tools"] == {}


def test_patch_control_plane_persists_and_projects_to_env(tmp_path, monkeypatch) -> None:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # Simulate the lab profile seed having turned the flag ON.
    monkeypatch.setenv("MAGI_FACTS_REPLAN_ENABLED", "1")
    client = TestClient(create_app(_build_runtime(tmp_path)))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(
        "/v1/app/customize/control-plane/facts-replan", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["overrides"]["control_plane"]["facts-replan"] is False
    # Persisted to disk...
    import json

    assert json.loads(cfile.read_text())["control_plane"]["facts-replan"] is False
    # ...and projected onto the live env so the next turn honors it immediately.
    assert os.environ["MAGI_FACTS_REPLAN_ENABLED"] == "0"


def test_patch_control_plane_unknown_behavior_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_build_runtime(tmp_path)))
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch(
        "/v1/app/customize/control-plane/MAGI_EGRESS_GATE_ENABLED",
        json={"enabled": False},
    )
    assert resp.status_code == 404


def test_patch_control_plane_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token
    resp = client.patch(
        "/v1/app/customize/control-plane/facts-replan", json={"enabled": False}
    )
    assert resp.status_code == 401
