"""PR-F3 — PUT /v1/app/customize/custom-rules field_constraint lift tests.

The NL and guided-wizard authoring surfaces both deliver field_constraint
rules in shapes that ``validate_custom_rule`` (which only knows the four
legacy kinds) rejects outright. The transport layer must lift them to a
synthesised ``shacl_constraint`` payload BEFORE validation so the same
backend gate covers both authoring forms.

Spec: docs/plans/2026-06-23-customize-depth-enrichment-design.md §PR-F3
("field_constraint validated as a shacl_constraint (same backend gate);
no new runtime path needed").

These tests round-trip both shapes through the PUT route and then through
the matching GET so we cover persistence + reload.
"""

from __future__ import annotations

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


def _client(tmp_path) -> TestClient:
    """Return an authenticated client; persists overrides under ``tmp_path``."""
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


# ---------------------------------------------------------------------------
# Wizard surface: kind == "shacl_constraint", payload.shapeTtl == "",
# payload.authoredAs.kind == "field_constraint".
# ---------------------------------------------------------------------------


def _wizard_payload() -> dict:
    return {
        "scope": "coding",
        "enabled": True,
        "firesAt": "pre_final",
        "action": "block",
        "what": {
            "kind": "shacl_constraint",
            "payload": {
                "shapeTtl": "",
                "authoredAs": {
                    "kind": "field_constraint",
                    "evidenceType": "TestRun",
                    "field": "exitCode",
                    "operator": "eq",
                    "value": 0,
                },
            },
        },
    }


def test_put_custom_rule_lifts_wizard_field_constraint(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)

    resp = client.put("/v1/app/customize/custom-rules", json=_wizard_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"].startswith("cr_")

    rules = body["overrides"]["verification"]["custom_rules"]
    assert len(rules) == 1
    persisted = rules[0]
    assert persisted["what"]["kind"] == "shacl_constraint"

    payload = persisted["what"]["payload"]
    # The empty shapeTtl was filled in via field_constraint_compiler.
    shape_ttl = payload["shapeTtl"]
    assert isinstance(shape_ttl, str) and shape_ttl.strip(), (
        "lift must synthesise a non-empty SHACL TTL from the IR"
    )
    assert "sh:NodeShape" in shape_ttl
    # The authored IR is retained for round-trip back into the wizard.
    assert payload["authoredAs"]["kind"] == "field_constraint"
    assert payload["authoredAs"]["evidenceType"] == "TestRun"
    assert payload["authoredAs"]["field"] == "exitCode"

    # Round-trip via GET — same shape comes back.
    fetched = client.get("/v1/app/customize").json()
    fetched_rules = fetched["overrides"]["verification"]["custom_rules"]
    assert len(fetched_rules) == 1
    assert fetched_rules[0]["what"]["payload"]["authoredAs"]["field"] == "exitCode"
    assert "sh:NodeShape" in fetched_rules[0]["what"]["payload"]["shapeTtl"]


# ---------------------------------------------------------------------------
# NL surface: kind == "field_constraint" — payload IS the IR.
# ---------------------------------------------------------------------------


def _nl_payload() -> dict:
    return {
        "scope": "coding",
        "enabled": True,
        "firesAt": "pre_final",
        "action": "block",
        "what": {
            "kind": "field_constraint",
            "payload": {
                "evidenceType": "TestRun",
                "field": "exitCode",
                "operator": "eq",
                "value": 0,
            },
        },
    }


def test_put_custom_rule_lifts_nl_field_constraint(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = _client(tmp_path)

    resp = client.put("/v1/app/customize/custom-rules", json=_nl_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rules = body["overrides"]["verification"]["custom_rules"]
    assert len(rules) == 1
    persisted = rules[0]
    # Lifted to shacl_constraint for backend-gate parity.
    assert persisted["what"]["kind"] == "shacl_constraint"
    payload = persisted["what"]["payload"]
    assert isinstance(payload["shapeTtl"], str) and "sh:NodeShape" in payload["shapeTtl"]
    # authoredAs IR present for round-trip.
    authored = payload["authoredAs"]
    assert authored["kind"] == "field_constraint"
    assert authored["evidenceType"] == "TestRun"
    assert authored["field"] == "exitCode"
    assert authored["operator"] == "eq"
    assert authored["value"] == 0

    # Round-trip via GET.
    fetched = client.get("/v1/app/customize").json()
    fetched_rules = fetched["overrides"]["verification"]["custom_rules"]
    assert len(fetched_rules) == 1
    assert fetched_rules[0]["what"]["payload"]["authoredAs"]["operator"] == "eq"


# ---------------------------------------------------------------------------
# Invalid IR (unknown field) surfaces as 400, not 500.
# ---------------------------------------------------------------------------


def test_put_custom_rule_rejects_field_constraint_with_unknown_field(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)

    bad = _nl_payload()
    bad["what"]["payload"]["field"] = "totallyImaginaryField"
    resp = client.put("/v1/app/customize/custom-rules", json=bad)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_custom_rule"
    # Compiler reason bubbles up so the operator can see what was rejected.
    details = " ".join(body.get("details", []))
    assert "totallyImaginaryField" in details
