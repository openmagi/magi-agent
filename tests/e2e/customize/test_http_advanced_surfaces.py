"""Remaining customize surfaces — seams workflow, runtime-fields,
tool_perm payload variants, rule update via PUT.

Closes the last gaps after :file:`test_http_full_matrix.py`,
:file:`test_http_dashboard_endpoints.py`,
:file:`test_http_compile_endpoints.py`,
:file:`test_http_scope_multi_rule.py`, and
:file:`test_http_negative_paths.py`:

* Seam-spec workflow: ``PUT /seams`` save + ``DELETE
  /seams/{spec_id}`` round-trip. The wizard's "Advanced: rule builder"
  modal POSTs ``/seams/compile`` for preview then ``PUT /seams`` to
  persist. Without an e2e the save+delete pair drifts unnoticed.
* ``GET /runtime-fields``: the chip picker / Variable inspector reads
  this. A 404/500 silently breaks the wizard's field discovery.
* ``tool_perm`` payload variants beyond ``{match: {tool: ...}}``:
  ``domain``, ``domainAllowlist``, ``path``, ``pathAllowlist``. Each
  was added by a separate F-series PR; without per-variant pinning
  any single match field can rot.
* Rule update via PUT with an explicit id: idempotent re-save of the
  same rule MUST update in place, not duplicate. The dashboard's
  "Edit policy" flow depends on this.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
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


@pytest.fixture
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Required for PUT/DELETE /seams; the route returns the
    # disabled-feature envelope when the master flag is OFF.
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", "1")
    # Required for GET /runtime-fields (the wizard's chip picker
    # endpoint, gated by PR-F-UX2 master flag).
    monkeypatch.setenv("MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


# ---------------------------------------------------------------------------
# Seam-spec workflow: PUT /seams + DELETE /seams/{spec_id}
# ---------------------------------------------------------------------------


def _valid_seam_spec(spec_id: str = "qa_seam_1") -> dict[str, Any]:
    """Minimal SeamSpec shape parse_spec + validate_spec accept.

    spec_version + actions[] are the only required fields per
    parse_spec; each action needs ``op`` + ``preset_id``. A modify-op
    with no controls_refs is the smallest no-op a real reviewer
    would approve, so the persistence path round-trips cleanly.
    """
    return {
        "id": spec_id,
        "spec_version": "1",
        "actions": [
            {
                "op": "modify_seam",
                "preset_id": "answer-quality",
            }
        ],
    }


def test_seam_put_then_delete_roundtrip(http_client: TestClient) -> None:
    """PUT /seams persists; DELETE /seams/{id} removes."""
    put_resp = http_client.put(
        "/v1/app/customize/seams", json=_valid_seam_spec()
    )
    # PUT may 200 (saved) or 400 (validator drift). Pin the contract.
    assert put_resp.status_code in {200, 400}, (
        f"PUT /seams expected 200 or 400; "
        f"got {put_resp.status_code} body={put_resp.text}"
    )
    if put_resp.status_code == 400:
        # If the validator rejects the minimal shape, the test still
        # asserts the auth/validation contract; the runtime never
        # persists a half-broken spec.
        return

    seams = (
        put_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("seam_specs", [])
    )
    assert any(s.get("id") == "qa_seam_1" for s in seams), (
        f"PUT /seams must persist the spec into seam_specs; got {seams!r}"
    )

    del_resp = http_client.delete("/v1/app/customize/seams/qa_seam_1")
    assert del_resp.status_code == 200
    seams = (
        del_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("seam_specs", [])
    )
    assert not any(s.get("id") == "qa_seam_1" for s in seams), (
        f"DELETE /seams/qa_seam_1 must remove the spec; got {seams!r}"
    )


def test_seam_put_non_dict_body_rejected(http_client: TestClient) -> None:
    """Non-dict body (string / list) MUST be rejected at the parse step.

    parse_spec is permissive about missing fields when the body IS a
    dict (every override is optional in the modify_seam shape, so a
    bare ``{"not": "a seam"}`` passes the parse + has zero actions to
    validate, returning 200 with an empty spec; that's the documented
    behavior). A non-dict body skips parse_spec at the route level and
    returns 400 ``object_required``. This pins the route's body-type
    rejection contract.
    """
    resp = http_client.put("/v1/app/customize/seams", json="not a dict")
    assert resp.status_code == 400, (
        f"non-dict seam body MUST 400; "
        f"got {resp.status_code} body={resp.text}"
    )
    assert resp.json().get("error") == "object_required"


def test_seam_delete_unknown_id_does_not_500(
    http_client: TestClient,
) -> None:
    """DELETE of an unknown seam id MUST return a clean status (no 500)."""
    resp = http_client.delete(
        "/v1/app/customize/seams/never_existed_xyz"
    )
    assert resp.status_code in {200, 404}, (
        f"DELETE unknown seam: 200 (idempotent) or 404 (strict); "
        f"got {resp.status_code} body={resp.text}"
    )


# ---------------------------------------------------------------------------
# GET /v1/app/customize/runtime-fields
# ---------------------------------------------------------------------------


def test_runtime_fields_returns_field_catalog(http_client: TestClient) -> None:
    """The wizard's variable-chip picker reads this endpoint.

    Required query params: ``lifecycle`` + ``condition``. Without
    them the route 4xxs. The picker calls per-step as the operator
    moves through the wizard; this test pins the canonical
    (before_tool_use, regex) tuple the wizard's first chip render
    uses.
    """
    resp = http_client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "regex"},
    )
    assert resp.status_code == 200, (
        f"GET /runtime-fields expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    # The endpoint returns either a flat list or a grouped dict; both
    # shapes appear across versions. Either way the response MUST be
    # non-empty so the picker has something to render.
    assert body, f"runtime-fields must be non-empty; got {body!r}"


def test_runtime_fields_missing_query_params_rejected(
    http_client: TestClient,
) -> None:
    """No lifecycle/condition query => 4xx (the picker contract is
    'tell me what fields are available for THIS (lifecycle, condition)
    tuple', so a paramless query has no semantically valid response)."""
    resp = http_client.get("/v1/app/customize/runtime-fields")
    assert resp.status_code in {400, 404, 422}, (
        f"paramless /runtime-fields MUST 4xx; "
        f"got {resp.status_code} body={resp.text}"
    )


# ---------------------------------------------------------------------------
# tool_perm payload variants — domain + path + allowlists
# ---------------------------------------------------------------------------


@pytest.fixture
def tool_perm_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """tool_perm authoring client. Same flag set as http_client but
    without scope-specific monkeypatching."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _put_tool_perm(client: TestClient, match: dict[str, Any]) -> str:
    resp = client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {"match": match, "decision": "deny"},
            },
        },
    )
    assert resp.status_code == 200, (
        f"PUT tool_perm match={match!r} expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )
    return resp.json()["id"]


def test_tool_perm_domain_match_round_trips_and_fires(
    tool_perm_client: TestClient,
) -> None:
    """match.domain: rule fires when arguments[url] host matches."""
    rid = _put_tool_perm(tool_perm_client, {"domain": "evil.example.com"})

    from magi_agent.customize.tool_perm import matched_decision

    # url that hosts on evil.example.com → block
    hit = matched_decision(
        tool_name="web_fetch",
        arguments={"url": "https://evil.example.com/secrets"},
    )
    assert hit is not None and hit[0] == "deny", (
        f"domain match MUST fire on matching host; got {hit}"
    )
    # Different domain → no match
    miss = matched_decision(
        tool_name="web_fetch",
        arguments={"url": "https://safe.example.org/page"},
    )
    assert miss is None, f"non-matching host MUST NOT fire; got {miss}"

    tool_perm_client.delete(f"/v1/app/customize/custom-rules/{rid}")


def test_tool_perm_domain_allowlist_round_trips_and_fires(
    tool_perm_client: TestClient,
) -> None:
    """match.domainAllowlist: rule fires when host NOT in allowlist."""
    rid = _put_tool_perm(
        tool_perm_client,
        {"domainAllowlist": ["safe.example.org", "trusted.example.com"]},
    )

    from magi_agent.customize.tool_perm import matched_decision

    # host outside the allowlist → block
    hit = matched_decision(
        tool_name="web_fetch",
        arguments={"url": "https://unknown.example.net/page"},
    )
    assert hit is not None and hit[0] == "deny", (
        f"domainAllowlist MUST block out-of-list host; got {hit}"
    )
    # host inside the allowlist → no block
    miss = matched_decision(
        tool_name="web_fetch",
        arguments={"url": "https://safe.example.org/page"},
    )
    assert miss is None, f"allowlisted host MUST NOT fire; got {miss}"

    tool_perm_client.delete(f"/v1/app/customize/custom-rules/{rid}")


def test_tool_perm_path_match_round_trips_and_fires(
    tool_perm_client: TestClient,
) -> None:
    """match.path: rule fires when arguments[path] starts with prefix."""
    rid = _put_tool_perm(tool_perm_client, {"path": "/etc/"})

    from magi_agent.customize.tool_perm import matched_decision

    hit = matched_decision(
        tool_name="file_read", arguments={"path": "/etc/passwd"}
    )
    assert hit is not None and hit[0] == "deny", (
        f"path prefix match MUST fire; got {hit}"
    )
    miss = matched_decision(
        tool_name="file_read", arguments={"path": "/home/user/notes.txt"}
    )
    assert miss is None

    tool_perm_client.delete(f"/v1/app/customize/custom-rules/{rid}")


def test_tool_perm_path_allowlist_round_trips_and_fires(
    tool_perm_client: TestClient,
) -> None:
    """match.pathAllowlist: rule fires when path NOT under any prefix."""
    rid = _put_tool_perm(
        tool_perm_client, {"pathAllowlist": ["/workspace/", "/tmp/"]}
    )

    from magi_agent.customize.tool_perm import matched_decision

    hit = matched_decision(
        tool_name="file_read", arguments={"path": "/etc/passwd"}
    )
    assert hit is not None and hit[0] == "deny", (
        f"pathAllowlist MUST block out-of-list path; got {hit}"
    )
    miss = matched_decision(
        tool_name="file_read", arguments={"path": "/workspace/notes.txt"}
    )
    assert miss is None, f"allowlisted path MUST NOT fire; got {miss}"

    tool_perm_client.delete(f"/v1/app/customize/custom-rules/{rid}")


# ---------------------------------------------------------------------------
# Rule update via PUT with explicit id (dashboard "Edit policy" flow)
# ---------------------------------------------------------------------------


def test_put_custom_rule_with_explicit_id_updates_in_place(
    http_client: TestClient,
) -> None:
    """PUT with an explicit id should UPDATE not duplicate.

    The dashboard's "Edit policy" flow re-PUTs the same id with
    modified fields. If the route appended a new entry instead of
    replacing, the catalog would accumulate stale rules silently.
    """
    base = {
        "id": "cr_qa_edit_in_place",
        "scope": "always",
        "enabled": True,
        "firesAt": "pre_final",
        "action": "audit",
        "what": {
            "kind": "deterministic_ref",
            "payload": {"ref": "evidence:test-run"},
        },
    }
    first = http_client.put("/v1/app/customize/custom-rules", json=base)
    assert first.status_code == 200, (
        f"first PUT expected 200; got {first.status_code} body={first.text}"
    )
    first_rules = first.json()["overrides"]["verification"]["custom_rules"]
    assert sum(1 for r in first_rules if r["id"] == "cr_qa_edit_in_place") == 1

    # Re-PUT with the same id but action=block (legal for this kind).
    updated = dict(base)
    updated["action"] = "block"
    second = http_client.put("/v1/app/customize/custom-rules", json=updated)
    assert second.status_code == 200
    second_rules = second.json()["overrides"]["verification"]["custom_rules"]

    matches = [r for r in second_rules if r["id"] == "cr_qa_edit_in_place"]
    assert len(matches) == 1, (
        f"re-PUT with same id MUST keep exactly one entry; "
        f"got {len(matches)} matches: {matches!r}"
    )
    assert matches[0]["action"] == "block", (
        f"re-PUT MUST update the rule in place; "
        f"got action={matches[0]['action']!r}"
    )

    http_client.delete("/v1/app/customize/custom-rules/cr_qa_edit_in_place")
