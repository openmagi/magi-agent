"""PR6 — learning governance dashboard API.

TestClient against the learning router mounted on a throwaway FastAPI app with a
temp ``SqliteLearningStore``.  No real network / LLM.

Covers:
* list filters (scope/kind/status) + pagination
* detail returns provenance + eval observation + conflict info
* approve a proposed rule WITH an eval observation → active, approver recorded
* approve a rule WITHOUT an eval observation → 4xx (eval-observation-required),
  stays proposed (policy surfaced cleanly, not a 500)
* approve a non-proposed item → 4xx
* edit → new version (supersedes chain), original preserved
* delete → archived (soft-delete, still retrievable)
* conflict: approve/edit against a contradicting active rule → blocked (409),
  force overrides
* authz: anonymous mutate → 401
* reflection/run triggers one pass
* default-OFF: router not mounted unless enabled (no /v1/learning route)
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.learning.api import LearningGovernanceService
from magi_agent.learning.models import LearningItem, LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore
from magi_agent.transport.learning_dashboard import build_learning_dashboard_router


GATEWAY_TOKEN = "gateway-token"
APPROVER = "kevin@example.com"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _client(store: SqliteLearningStore) -> TestClient:
    app = FastAPI()
    service = LearningGovernanceService(store)
    app.include_router(
        build_learning_dashboard_router(service, gateway_token=GATEWAY_TOKEN)
    )
    return TestClient(app)


def _auth(*, approver: bool = False) -> dict[str, str]:
    headers = {"x-gateway-token": GATEWAY_TOKEN}
    if approver:
        headers["x-approver"] = APPROVER
    return headers


def _rule(
    *,
    item_id: str,
    when: str = "user asks for help",
    then: str = "be concise",
    tag: str = "style",
    task_kind: str = "general",
) -> LearningItem:
    return LearningItem(
        id=item_id,
        kind="rule",
        scope=LearningScope(taskKind=task_kind, tags=(tag,)),
        content={"when": when, "then": then},
        rationale="prefer concise help",
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
    )


def _example(*, item_id: str, tag: str = "style", task_kind: str = "general") -> LearningItem:
    return LearningItem(
        id=item_id,
        kind="example",
        scope=LearningScope(taskKind=task_kind, tags=(tag,)),
        content={"situation": "user asks", "behavior": "answer briefly"},
        rationale="brevity",
        provenance=Provenance(
            sessionIds=("sess-2",),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
    )


def _propose_with_eval(store: SqliteLearningStore, item: LearningItem) -> str:
    """Propose *item* and record a passing eval observation. Returns the ref."""
    store.propose(item)
    return store.record_eval_observation(
        item_id=item.id,
        before={"score": 1.0},
        after={"score": 1.0},
        sample_n=4,
        passed=True,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_filters_by_kind_status_scope(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="learning:rule-a", task_kind="research"))
    store.propose(_example(item_id="learning:example-b", task_kind="research"))
    store.propose(_rule(item_id="learning:rule-c", task_kind="coding"))
    client = _client(store)

    resp = client.get("/v1/learning/learnings", headers=_auth(), params={"kind": "rule"})
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"learning:rule-a", "learning:rule-c"}

    resp = client.get(
        "/v1/learning/learnings", headers=_auth(), params={"taskKind": "research"}
    )
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"learning:rule-a", "learning:example-b"}

    resp = client.get(
        "/v1/learning/learnings", headers=_auth(), params={"status": "proposed", "kind": "example"}
    )
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"learning:example-b"}
    store.close()


def test_list_pagination_cursor(tmp_path) -> None:
    store = _store(tmp_path)
    for n in range(3):
        store.propose(_rule(item_id=f"learning:rule-{n}"))
    client = _client(store)

    resp = client.get("/v1/learning/learnings", headers=_auth(), params={"limit": 2})
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["nextCursor"] is not None

    resp2 = client.get(
        "/v1/learning/learnings",
        headers=_auth(),
        params={"limit": 2, "cursor": body["nextCursor"]},
    )
    body2 = resp2.json()
    assert len(body2["items"]) == 1
    assert body2["nextCursor"] is None
    store.close()


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_detail_returns_provenance_eval_and_conflict(tmp_path) -> None:
    store = _store(tmp_path)
    ref = _propose_with_eval(store, _rule(item_id="learning:rule-detail"))
    client = _client(store)

    resp = client.get("/v1/learning/learnings/learning:rule-detail", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance"]["sessionIds"] == ["sess-1"]
    assert body["evalObservationRef"] == ref
    assert body["conflict"]["hasConflict"] is False
    assert "content" in body and body["content"]["when"] == "user asks for help"
    store.close()


def test_detail_missing_item_404(tmp_path) -> None:
    store = _store(tmp_path)
    client = _client(store)
    resp = client.get("/v1/learning/learnings/nope", headers=_auth())
    assert resp.status_code == 404
    store.close()


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


def test_approve_rule_with_eval_observation_activates(tmp_path) -> None:
    store = _store(tmp_path)
    _propose_with_eval(store, _rule(item_id="learning:rule-ok"))
    client = _client(store)

    resp = client.post(
        "/v1/learning/learnings/learning:rule-ok/approve",
        headers=_auth(approver=True),
        json={"force": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    item = store.get("learning:rule-ok")
    assert item is not None and item.status == "active"
    assert item.approval_ref is not None
    # approver recorded in the approvals table
    conn = store._get_conn()
    row = conn.execute(
        "SELECT approver FROM learning_approvals WHERE item_id = ?",
        ("learning:rule-ok",),
    ).fetchone()
    assert row["approver"] == APPROVER
    store.close()


def test_approve_rule_without_eval_observation_4xx_stays_proposed(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="learning:rule-noeval"))
    client = _client(store)

    resp = client.post(
        "/v1/learning/learnings/learning:rule-noeval/approve",
        headers=_auth(approver=True),
    )
    assert 400 <= resp.status_code < 500
    assert "eval-observation-required" in resp.json()["message"]
    # stays proposed
    assert store.get("learning:rule-noeval").status == "proposed"
    store.close()


def test_approve_non_proposed_item_4xx(tmp_path) -> None:
    store = _store(tmp_path)
    _propose_with_eval(store, _rule(item_id="learning:rule-twice"))
    client = _client(store)
    # first approval succeeds
    client.post(
        "/v1/learning/learnings/learning:rule-twice/approve",
        headers=_auth(approver=True),
    )
    # second approval — item is now active, not proposed
    resp = client.post(
        "/v1/learning/learnings/learning:rule-twice/approve",
        headers=_auth(approver=True),
    )
    assert 400 <= resp.status_code < 500
    store.close()


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def test_edit_creates_new_version_supersedes(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="learning:rule-edit"))
    client = _client(store)

    resp = client.patch(
        "/v1/learning/learnings/learning:rule-edit",
        headers=_auth(approver=True),
        json={"patch": {"rationale": "updated rationale"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert body["supersedes"] == "learning:rule-edit"
    assert body["id"] == "learning:rule-edit:v2"
    # original preserved
    assert store.get("learning:rule-edit") is not None
    store.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_archives_soft(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="learning:rule-del"))
    client = _client(store)

    resp = client.delete(
        "/v1/learning/learnings/learning:rule-del", headers=_auth(approver=True)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    # still retrievable as archived (not hard-deleted)
    item = store.get("learning:rule-del")
    assert item is not None and item.status == "archived"
    store.close()


# ---------------------------------------------------------------------------
# Conflict
# ---------------------------------------------------------------------------


def test_conflict_blocks_approve_unless_forced(tmp_path) -> None:
    store = _store(tmp_path)
    # active rule in scope: when="trigger" then="action A"
    active = _rule(item_id="learning:rule-active", when="trigger", then="action A")
    store.propose(active)
    store.approve(
        "learning:rule-active",
        approver="seed",
        eval_observation_ref=store.record_eval_observation(
            item_id="learning:rule-active",
            before={}, after={}, sample_n=4, passed=True,
        ),
    )
    # contradicting proposed rule: same scope + same when, different then
    contra = _rule(item_id="learning:rule-contra", when="trigger", then="action B")
    _propose_with_eval(store, contra)
    client = _client(store)

    # detail surfaces the conflict
    detail = client.get(
        "/v1/learning/learnings/learning:rule-contra", headers=_auth()
    ).json()
    assert detail["conflict"]["hasConflict"] is True
    assert "learning:rule-active" in detail["conflict"]["conflictingIds"]

    # approve blocked (409) without force
    resp = client.post(
        "/v1/learning/learnings/learning:rule-contra/approve",
        headers=_auth(approver=True),
        json={"force": False},
    )
    assert resp.status_code == 409
    assert store.get("learning:rule-contra").status == "proposed"

    # force overrides
    resp = client.post(
        "/v1/learning/learnings/learning:rule-contra/approve",
        headers=_auth(approver=True),
        json={"force": True},
    )
    assert resp.status_code == 200
    assert store.get("learning:rule-contra").status == "active"
    store.close()


# ---------------------------------------------------------------------------
# Authz
# ---------------------------------------------------------------------------


def test_anonymous_mutation_rejected(tmp_path) -> None:
    store = _store(tmp_path)
    _propose_with_eval(store, _rule(item_id="learning:rule-anon"))
    client = _client(store)

    # no gateway token at all
    resp = client.post("/v1/learning/learnings/learning:rule-anon/approve")
    assert resp.status_code == 401

    # gateway token but no approver header
    resp = client.post(
        "/v1/learning/learnings/learning:rule-anon/approve", headers=_auth()
    )
    assert resp.status_code == 401
    # unchanged
    assert store.get("learning:rule-anon").status == "proposed"
    store.close()


def test_read_requires_gateway_token(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="learning:rule-read"))
    client = _client(store)
    resp = client.get("/v1/learning/learnings")
    assert resp.status_code == 401
    store.close()


# ---------------------------------------------------------------------------
# Reflection
# ---------------------------------------------------------------------------


def test_reflection_run_triggers_pass(tmp_path) -> None:
    store = _store(tmp_path)
    client = _client(store)
    resp = client.post(
        "/v1/learning/reflection/run", headers=_auth(approver=True)
    )
    assert resp.status_code == 200
    body = resp.json()
    # env gate OFF by default → disabled no-op pass, but it ran cleanly
    assert body["status"] in {"disabled", "ok"}
    assert "candidatesProduced" in body
    store.close()


# ---------------------------------------------------------------------------
# Default-OFF mounting
# ---------------------------------------------------------------------------


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token=GATEWAY_TOKEN,
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
    )
    return OpenMagiRuntime(config=config)


def test_router_not_mounted_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MAGI_LEARNING_DASHBOARD_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)
    app = create_app(_runtime())
    paths = {route.path for route in app.routes}
    assert not any(p.startswith("/v1/learning") for p in paths)


def test_router_mounted_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_LEARNING_DASHBOARD_ENABLED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(_runtime())
    paths = {route.path for route in app.routes}
    assert any(p.startswith("/v1/learning") for p in paths)


def test_reflection_run_uses_asyncio_directly(tmp_path) -> None:
    """Sanity: the service reflection trigger is awaitable / runs via asyncio."""
    store = _store(tmp_path)
    service = LearningGovernanceService(store)
    summary = asyncio.run(service.run_reflection())
    assert summary.status in {"disabled", "ok"}
    store.close()
