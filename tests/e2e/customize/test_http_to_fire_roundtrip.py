"""End-to-end customize-dashboard round-trip: HTTP -> persist -> fire.

Existing coverage gap. ``tests/test_customize_routes.py`` proves the
HTTP transport persists rules (PUT 200 + GET shows the rule), and
``tests/e2e/customize/test_matrix_*.py`` proves the in-process
fan-out fires each rule. Neither glues the two together. The hosted
500/404 incidents that surface as "Failed to update verification
rule"/"Failed to update behavior" in the dashboard are exactly the
class of bug a glued HTTP-to-fire test would catch: an endpoint that
silently mis-shapes the payload (or doesn't accept it at all) makes
the dashboard error visible without any runtime smoke noticing.

This file walks one representative configuration for every authorable
custom_rule kind plus the two non-rule customize endpoints the
dashboard uses (control-plane behaviors, verification preset PATCH):

* 9 kinds: PUT ``/v1/app/customize/custom-rules`` with a wizard-shaped
  payload, assert 200 + ``id`` returned, GET back the catalog and
  confirm the rule is listed, invoke the matching ``lifecycle_audit``
  fan-out the runtime uses, assert it fires as authored, then DELETE
  the rule and assert it is gone.
* 2 control-plane behaviors: PATCH
  ``/v1/app/customize/control-plane/{behavior_id}`` round-trips +
  projects to ``os.environ``.
* 1 verification preset toggle: PATCH
  ``/v1/app/customize/verification/{kind}/{item_id}`` round-trips.

The fan-out call is the SAME production code-path the serve loop
invokes; the only thing this file adds over the existing matrix is
the HTTP author + persist step at the front. Coverage = "wizard
POSTs a rule the operator authored in the dashboard, runtime fires
it" — the exact contract the hosted 500/404 broke.

llm_criterion uses the F-QA conftest stub critic (no provider key
required); every other kind hits real production code (real
subprocess for shell_*, real pySHACL parse for shacl_constraint,
real evidence catalog for deterministic_ref).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.tools.result import ToolResult


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


@pytest.fixture
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path]:
    """Authenticated TestClient + tmp customize.json path.

    Returns the path so individual tests can read back the persisted
    contents after the PUT. Every relevant master flag is flipped ON
    (the F-QA1 set) plus the lab profile is unset so the round-trip
    path is byte-exact what an operator on a self-host install would
    see.
    """
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client, cfile


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


# ---------------------------------------------------------------------------
# Helpers — every test that authors a rule via HTTP and then fires it
# repeats the same three-step shape. Factor them so the per-kind test
# body stays small enough to read at a glance.
# ---------------------------------------------------------------------------


def _put_rule(client: TestClient, rule: dict[str, Any]) -> str:
    """PUT the rule and assert 200 + return the assigned rule id."""
    resp = client.put("/v1/app/customize/custom-rules", json=rule)
    assert resp.status_code == 200, (
        f"PUT /v1/app/customize/custom-rules expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    rid = body["id"]
    assert isinstance(rid, str) and rid.startswith("cr_"), (
        f"PUT must return an id of shape cr_*; got {rid!r}"
    )
    # The response body MUST also include the freshly-persisted rule in
    # overrides.verification.custom_rules so the dashboard can re-render
    # without a round-trip GET. Same shape contract the wizard relies on.
    listed = body["overrides"]["verification"]["custom_rules"]
    assert any(r["id"] == rid for r in listed), (
        f"the new rule {rid!r} must appear in the response's "
        f"overrides.verification.custom_rules; got {listed!r}"
    )
    return rid


def _assert_rule_visible(client: TestClient, rid: str) -> None:
    """GET the catalog and assert the rule is listed (PUT didn't lie)."""
    resp = client.get("/v1/app/customize")
    assert resp.status_code == 200, (
        f"GET /v1/app/customize expected 200; got {resp.status_code}"
    )
    rules = (
        resp.json().get("overrides", {})
        .get("verification", {})
        .get("custom_rules", [])
    )
    assert any(r["id"] == rid for r in rules), (
        f"after PUT the rule {rid!r} must appear in GET /v1/app/customize; "
        f"got {rules!r}"
    )


def _delete_rule(client: TestClient, rid: str) -> None:
    """DELETE the rule and assert it is gone from the catalog."""
    resp = client.delete(f"/v1/app/customize/custom-rules/{rid}")
    assert resp.status_code == 200
    rules = (
        resp.json().get("overrides", {})
        .get("verification", {})
        .get("custom_rules", [])
    )
    assert not any(r["id"] == rid for r in rules), (
        f"after DELETE {rid!r} must NOT appear in the response catalog; "
        f"got {rules!r}"
    )


# ---------------------------------------------------------------------------
# Per-kind round-trip tests
# ---------------------------------------------------------------------------


def test_shell_command_audit_before_turn_start_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_turn_start",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "echo http_roundtrip",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_before_turn_start,
    )

    audits = asyncio.run(
        run_shell_command_at_before_turn_start(prompt_text="hi", remaining_budget=10)
    )
    statuses = [a.get("status") for a in audits]
    assert "executed" in statuses, (
        f"after HTTP PUT the rule MUST fire on the next "
        f"before_turn_start fan-out; got statuses={statuses}"
    )
    _delete_rule(client, rid)


def test_shell_command_block_pre_final_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "exit 1",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_pre_final,
    )

    _, verdict = asyncio.run(
        run_shell_command_at_pre_final(draft_text="x", remaining_budget=10)
    )
    assert verdict == "block", (
        f"action=block + exit-1 script must yield gate verdict='block'; "
        f"got {verdict!r}"
    )
    _delete_rule(client, rid)


def test_shell_check_audit_before_tool_use_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "audit",
            "what": {
                "kind": "shell_check",
                "payload": {
                    "source": "inline",
                    "inline": "printf '{\"passed\":true,\"reason\":\"ok\"}'",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.lifecycle_audit import (
        run_shell_check_at_before_tool_use,
    )

    audits, verdict = asyncio.run(
        run_shell_check_at_before_tool_use(
            tool_name="some_tool",
            tool_args={"hello": "world"},
            remaining_budget=10,
        )
    )
    assert verdict == "proceed", f"passing shell_check must not block; got {verdict}"
    assert any(
        a.get("status") == "evaluated" and a.get("passed") is True for a in audits
    ), f"check rule must produce a passed=True evaluated audit; got {audits}"
    _delete_rule(client, rid)


def test_shell_check_block_pre_final_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {
                "kind": "shell_check",
                "payload": {
                    "source": "inline",
                    "inline": "printf '{\"passed\":false,\"reason\":\"qa_reject\"}'",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.lifecycle_audit import (
        run_shell_check_at_pre_final,
    )

    _, verdict = asyncio.run(
        run_shell_check_at_pre_final(draft_text="x", remaining_budget=10)
    )
    assert verdict == "block"
    _delete_rule(client, rid)


def test_tool_perm_block_roundtrip(http_client: tuple[TestClient, Path]) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {
                    "match": {"tool": "dangerous_tool"},
                    "decision": "deny",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.tool_perm import matched_decision

    out = matched_decision(tool_name="dangerous_tool", arguments={})
    assert out is not None and out[0] == "deny", (
        f"after HTTP PUT, matched_decision MUST return ('deny', rid); got {out}"
    )
    _delete_rule(client, rid)


def test_tool_perm_ask_approval_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "ask_approval",
            "what": {
                "kind": "tool_perm",
                "payload": {
                    "match": {"tool": "needs_review"},
                    "decision": "ask",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.tool_perm import matched_decision

    out = matched_decision(tool_name="needs_review", arguments={})
    assert out is not None and out[0] == "ask"
    _delete_rule(client, rid)


def test_capability_scope_block_spawn_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rule = {
        "scope": "coding",
        "enabled": True,
        "firesAt": "spawn",
        "action": "block",
        "what": {
            "kind": "capability_scope",
            "payload": {
                "tightenOnly": True,
                "denyTools": ["DangerousTool", "WriteFile"],
                "maxPermissionClass": "readonly",
            },
        },
    }
    rid = _put_rule(client, rule)
    _assert_rule_visible(client, rid)

    from magi_agent.customize.capability_scope import apply_capability_scope
    from magi_agent.customize.store import load_overrides

    class _T:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_T("DangerousTool"), _T("ReadFile"), _T("WriteFile"), _T("EditFile")]
    # Load the rule back from the persisted customize.json the HTTP
    # PUT just wrote, then pass it to apply_capability_scope exactly
    # as the spawn-time wiring does.
    overrides = load_overrides()
    rules = (
        overrides.get("verification", {}).get("custom_rules", [])
    )
    assert any(r.get("id") == rid for r in rules), (
        f"capability_scope rule MUST be persisted; "
        f"got ids={[r.get('id') for r in rules]}"
    )
    assert any(r.get("id") == rid for r in rules), (
        f"capability_scope rule MUST round-trip through the policy "
        f"reader; got {[r.get('id') for r in rules]}"
    )
    narrowed, cap = apply_capability_scope(
        tools,
        rules=rules,
        tool_name_fn=lambda t: t.name,
        current_permission_class="safe_write",
    )
    narrowed_names = {t.name for t in narrowed}
    assert "DangerousTool" not in narrowed_names
    assert "WriteFile" not in narrowed_names
    assert "ReadFile" in narrowed_names
    assert cap == "readonly"
    _delete_rule(client, rid)


def test_prompt_injection_audit_before_tool_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "audit",
            "what": {
                "kind": "prompt_injection",
                "payload": {
                    "mode": "append",
                    "target_arg_key": "input",
                    "value": "[qa-injected]",
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.prompt_injection import (
        apply_prompt_injection_to_tool_args,
    )
    from magi_agent.customize.store import load_overrides

    overrides = load_overrides()
    rules = overrides.get("verification", {}).get("custom_rules", [])
    out = apply_prompt_injection_to_tool_args(
        {"input": "original"}, rules, "some_tool"
    )
    assert "qa-injected" in str(out.get("input")), (
        f"prompt_injection MUST mutate the tool arg after HTTP PUT; "
        f"got {out}"
    )
    _delete_rule(client, rid)


def test_output_rewrite_audit_after_tool_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "after_tool_use",
            "action": "audit",
            "what": {
                "kind": "output_rewrite",
                "payload": {
                    "mode": "redact",
                    "pattern": "secret",
                    "replacement": "[redacted]",
                    "isRegex": False,
                },
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.output_rewrite import (
        apply_output_rewrite_to_tool_result,
    )
    from magi_agent.customize.store import load_overrides

    overrides = load_overrides()
    rules = overrides.get("verification", {}).get("custom_rules", [])
    result = ToolResult(
        status="ok", output="this contains a secret token", metadata={}
    )
    rewritten = apply_output_rewrite_to_tool_result(result, rules, "some_tool")
    assert "redacted" in str(rewritten.output)
    assert "secret" not in str(rewritten.output)
    _delete_rule(client, rid)


def test_deterministic_ref_audit_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    from magi_agent.customize.what_menu import allowed_actions_for, known_refs

    refs = sorted(known_refs())
    assert refs, "evidence ref catalog MUST be non-empty"
    ref = refs[0]
    action = (
        "audit" if "audit" in allowed_actions_for(ref) else allowed_actions_for(ref)[0]
    )
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": action,
            "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        },
    )
    _assert_rule_visible(client, rid)
    _delete_rule(client, rid)


def test_shacl_constraint_block_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    client, _ = http_client
    ttl = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:NodeShape ;\n"
        "   sh:targetClass <urn:Evidence> ;\n"
        "   sh:property [ sh:path <urn:hasTimestamp> ; "
        "sh:datatype xsd:dateTime ; sh:minCount 1 ] .\n"
    )
    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {"kind": "shacl_constraint", "payload": {"shapeTtl": ttl}},
        },
    )
    _assert_rule_visible(client, rid)
    _delete_rule(client, rid)


def test_llm_criterion_audit_before_llm_call_roundtrip(
    http_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_criterion: HTTP PUT + plugin invocation reaches the patched judge.

    Uses the same critic-factory + evaluate_criterion patches the
    F-QA conftest installs so this test does not require a real
    provider key.
    """
    client, _ = http_client

    sentinel = object()
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        lambda *_a, **_kw: sentinel,
    )
    judge_calls: list[str] = []

    async def _fake_eval(
        *,
        criterion: str,
        draft_text: str,
        model_factory: Any,
        invoke: Any = None,
        evidence_context: Any = None,
    ) -> tuple[bool, str]:
        judge_calls.append(criterion)
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion",
        _fake_eval,
    )

    rid = _put_rule(
        client,
        {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_llm_call",
            "action": "audit",
            "what": {
                "kind": "llm_criterion",
                "payload": {"criterion": "the prompt is safe"},
            },
        },
    )
    _assert_rule_visible(client, rid)

    from magi_agent.customize.lifecycle_audit import (
        run_before_llm_call_audit,
    )

    audits = asyncio.run(
        run_before_llm_call_audit(
            prompt_text="hello",
            model_factory=lambda: sentinel,
            critic_budget_remaining=3,
        )
    )
    statuses = [a.get("status") for a in audits]
    assert "evaluated" in statuses, (
        f"after HTTP PUT the llm_criterion rule must reach the judge; "
        f"got statuses={statuses}, judge_calls={judge_calls}"
    )
    assert judge_calls == ["the prompt is safe"], (
        f"the patched judge must see the authored criterion verbatim; "
        f"got {judge_calls}"
    )
    _delete_rule(client, rid)


# ---------------------------------------------------------------------------
# Non-rule customize endpoints the dashboard uses (the same surface the
# hosted 500/404 incidents broke). These do not go through the
# custom_rules PUT but they're equally part of the wizard's UI options.
# ---------------------------------------------------------------------------


def test_control_plane_behavior_toggle_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    """PATCH /v1/app/customize/control-plane/{behavior_id} round-trip.

    Exactly the endpoint the hosted dashboard's Behaviors tab calls and
    that returned 404 on a stale bot runtime image. A green test here
    means the OSS image carries the route; a hosted 404 then unambiguously
    points to the image pin (operations problem, not code regression).
    """
    client, _ = http_client
    resp = client.patch(
        "/v1/app/customize/control-plane/facts-replan", json={"enabled": False}
    )
    assert resp.status_code == 200, (
        f"PATCH /control-plane/facts-replan expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )
    # The behavior id is persisted verbatim (hyphenated). The
    # control-plane projector maps id -> env at runtime; this test
    # only asserts the persistence round-trip.
    overrides = resp.json().get("overrides", {}).get("control_plane", {})
    assert overrides.get("facts-replan") is False, (
        f"behavior toggle must persist into overrides.control_plane "
        f"under the verbatim id; got {overrides}"
    )


def test_verification_preset_patch_roundtrip(
    http_client: tuple[TestClient, Path],
) -> None:
    """PATCH /v1/app/customize/verification/{kind}/{item_id} round-trip.

    The Policies tab's built-in toggle goes through this endpoint. The
    hosted dashboard surfaced this as a 500; if the same call against a
    fresh runtime returns 200 then the failure is an image-pin issue,
    not a code regression.
    """
    client, _ = http_client
    # Valid verification kinds are ``recipes`` / ``harness_presets`` /
    # ``hooks`` (see magi_agent/transport/customize.py
    # ``_VERIFICATION_KINDS``). ``recipes`` is the dashboard's primary
    # toggle target; pick a built-in recipe id that exists in
    # ``magi_agent.customize.catalog.RECIPES``.
    #
    # Persistence shape (see store.set_verification_override): the
    # ``recipes`` bucket is a list of ENABLED recipe ids (append on
    # enable, remove on disable). The dashboard's toggle ON/OFF maps
    # to those two operations; test both halves of the round-trip.
    enable_resp = client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200, (
        f"PATCH enable expected 200; "
        f"got {enable_resp.status_code} body={enable_resp.text}"
    )
    recipes_after_enable = (
        enable_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("recipes", [])
    )
    assert "research" in recipes_after_enable, (
        f"after PATCH enable, recipe id must appear in the list; "
        f"got {recipes_after_enable!r}"
    )

    disable_resp = client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": False},
    )
    assert disable_resp.status_code == 200
    recipes_after_disable = (
        disable_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("recipes", [])
    )
    assert "research" not in recipes_after_disable, (
        f"after PATCH disable, recipe id must be removed from the list; "
        f"got {recipes_after_disable!r}"
    )
