"""PR8 — multi-tenant isolation + approver-role authz + rollout telemetry.

This is the LAST learning-layer implementation PR.  It proves the SAME code
serves OSS single-tenant (``tenant_id="local"``) AND a hosted multi-tenant
deployment, and that PR1–PR7 behavior is preserved.

Covers:
* cross-tenant isolation at the STORE: an item proposed under tenant A is
  invisible (``get`` → None) and immutable (``edit``/``archive``/``approve``/
  ``auto_activate``/``record_eval_observation`` → not found) to tenant B.  A can
  still operate normally.
* cross-tenant isolation through the DASHBOARD API via the ``x-tenant`` header:
  cross-tenant id → 404; default tenant ``local``.
* approver-role authz: a non-approver identity → 403 on approve/edit/delete; an
  approver-role identity → allowed; role recorded.
* rollout staging telemetry: promotion/reflection/approval emit tenant-scoped,
  user-id-HASHED events; the raw user id is NEVER present; default-OFF → no
  emission.

No network / no PII.  Real ``SqliteLearningStore`` (temp); FastAPI TestClient.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.learning.api import LearningGovernanceService, LearningNotFoundError
from magi_agent.learning.eval_gate import StaticCheckSet, run_eval_gate
from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.models import LearningItem, LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore
from magi_agent.transport.learning_dashboard import build_learning_dashboard_router


GATEWAY_TOKEN = "gateway-token"
APPROVER = "kevin@example.com"
NON_APPROVER = "stranger@example.com"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _rule(
    *,
    item_id: str,
    tenant_id: str = "local",
    when: str = "user asks for help",
    then: str = "be concise",
) -> LearningItem:
    return LearningItem(
        id=item_id,
        tenantId=tenant_id,
        kind="rule",
        scope=LearningScope(taskKind="general", tags=("style",)),
        content={"when": when, "then": then},
        rationale="prefer concise help",
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T00:00:00.000000Z",
        ),
    )


def _record_passing_obs(store: SqliteLearningStore, item_id: str, *, tenant_id: str) -> str:
    return store.record_eval_observation(
        item_id=item_id,
        tenant_id=tenant_id,
        before={"mean": 0.5, "n": 8},
        after={"mean": 0.9, "n": 8},
        sample_n=8,
        passed=True,
    )


# ---------------------------------------------------------------------------
# 1. Cross-tenant isolation at the STORE (the heart of this PR)
# ---------------------------------------------------------------------------


def test_store_get_is_tenant_scoped(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="shared:id", tenant_id="tenant-a"))

    # Tenant A sees it; tenant B does not (no cross-tenant read).
    assert store.get("shared:id", tenant_id="tenant-a") is not None
    assert store.get("shared:id", tenant_id="tenant-b") is None
    # Default single-tenant path still works for a local item.
    store.propose(_rule(item_id="local:id"))  # tenant_id defaults to "local"
    assert store.get("local:id") is not None
    assert store.get("local:id", tenant_id="tenant-b") is None


def test_store_mutations_are_tenant_scoped(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="x", tenant_id="tenant-a"))
    eval_ref = _record_passing_obs(store, "x", tenant_id="tenant-a")

    # Tenant B cannot mutate tenant A's item by id — every by-id op must fail.
    import pytest

    with pytest.raises(KeyError):
        store.edit("x", patch={"rationale": "hijacked"}, editor="b", tenant_id="tenant-b")
    with pytest.raises(KeyError):
        store.archive("x", actor="b", tenant_id="tenant-b")
    with pytest.raises(KeyError):
        store.approve("x", approver="b", eval_observation_ref=eval_ref, tenant_id="tenant-b")
    with pytest.raises(KeyError):
        store.auto_activate("x", eval_observation_ref=eval_ref, tenant_id="tenant-b")
    with pytest.raises(KeyError):
        store.record_eval_observation(
            item_id="x",
            tenant_id="tenant-b",
            before={"mean": 0.1, "n": 4},
            after={"mean": 0.0, "n": 4},
            sample_n=4,
            passed=False,
        )

    # Tenant A is unaffected and can still operate normally.
    assert store.get("x", tenant_id="tenant-a") is not None
    approved = store.approve(
        "x", approver="a", eval_observation_ref=eval_ref, tenant_id="tenant-a"
    )
    assert approved.status == "active"


# ---------------------------------------------------------------------------
# 2. Cross-tenant isolation through the DASHBOARD API (x-tenant header)
# ---------------------------------------------------------------------------


def _is_approver(tenant_id: str, approver: str) -> bool:
    # Test role resolver: only APPROVER holds the approver role (any tenant).
    return approver == APPROVER


def _client(store: SqliteLearningStore) -> TestClient:
    app = FastAPI()
    router = build_learning_dashboard_router(
        store=store,
        gateway_token=GATEWAY_TOKEN,
        is_approver=_is_approver,
    )
    app.include_router(router)
    return TestClient(app)


def _headers(*, tenant: str | None = None, approver: str | None = None) -> dict[str, str]:
    h = {"x-gateway-token": GATEWAY_TOKEN}
    if tenant is not None:
        h["x-tenant"] = tenant
    if approver is not None:
        h["x-approver"] = approver
    return h


def test_dashboard_scopes_to_x_tenant_header(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="a-item", tenant_id="tenant-a"))
    client = _client(store)

    # Tenant A can read its item.
    r = client.get("/v1/learning/learnings/a-item", headers=_headers(tenant="tenant-a"))
    assert r.status_code == 200
    # Tenant B gets a 404 for tenant A's id (cross-tenant invisible).
    r = client.get("/v1/learning/learnings/a-item", headers=_headers(tenant="tenant-b"))
    assert r.status_code == 404
    # Default tenant is "local".
    store.propose(_rule(item_id="local-item"))
    r = client.get("/v1/learning/learnings/local-item", headers=_headers())
    assert r.status_code == 200


def test_dashboard_list_is_tenant_scoped(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="a-1", tenant_id="tenant-a"))
    store.propose(_rule(item_id="b-1", tenant_id="tenant-b"))
    client = _client(store)

    r = client.get("/v1/learning/learnings", headers=_headers(tenant="tenant-a"))
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"a-1"}


def test_dashboard_default_resolver_pins_tenant_to_local(tmp_path) -> None:
    """Multi-tenant mount WITHOUT an injected resolver pins the tenant to "local".

    When the router is built in store/multi-tenant mode but no real approver-role
    resolver is supplied, a caller-chosen ``x-tenant`` MUST be ignored and
    treated as ``local`` — otherwise the default resolver authorizes any approver
    for any tenant, giving name-only isolation.  This proves a caller cannot
    reach ``tenant-a``'s data by sending ``x-tenant: tenant-a``.
    """
    store = _store(tmp_path)
    # An item under tenant-a, and one under the pinned "local" tenant.
    store.propose(_rule(item_id="a-only", tenant_id="tenant-a"))
    store.propose(_rule(item_id="local-item", tenant_id="local"))

    # NO is_approver injected → default resolver → tenant pinned to "local".
    app = FastAPI()
    router = build_learning_dashboard_router(
        store=store, gateway_token=GATEWAY_TOKEN
    )
    app.include_router(router)
    client = TestClient(app)

    # x-tenant: tenant-a is refused/ignored → request is treated as "local",
    # so tenant-a's item is NOT reachable (404).
    r = client.get(
        "/v1/learning/learnings/a-only", headers=_headers(tenant="tenant-a")
    )
    assert r.status_code == 404
    # The pinned "local" tenant's own item IS reachable even with an x-tenant
    # header set (the header is ignored).
    r = client.get(
        "/v1/learning/learnings/local-item", headers=_headers(tenant="tenant-a")
    )
    assert r.status_code == 200
    # A list call cannot see tenant-a's data either.
    r = client.get("/v1/learning/learnings", headers=_headers(tenant="tenant-a"))
    ids = {i["id"] for i in r.json()["items"]}
    assert "a-only" not in ids
    assert ids == {"local-item"}


# ---------------------------------------------------------------------------
# 3. Approver role authz (beyond header presence)
# ---------------------------------------------------------------------------


def test_non_approver_identity_blocked_403(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="r1", tenant_id="tenant-a"))
    _record_passing_obs(store, "r1", tenant_id="tenant-a")
    client = _client(store)

    # Present but not an approver-role identity → 403 (not 401).
    r = client.post(
        "/v1/learning/learnings/r1/approve",
        headers=_headers(tenant="tenant-a", approver=NON_APPROVER),
    )
    assert r.status_code == 403
    r = client.patch(
        "/v1/learning/learnings/r1",
        headers=_headers(tenant="tenant-a", approver=NON_APPROVER),
        json={"patch": {"rationale": "x"}},
    )
    assert r.status_code == 403
    r = client.delete(
        "/v1/learning/learnings/r1",
        headers=_headers(tenant="tenant-a", approver=NON_APPROVER),
    )
    assert r.status_code == 403


def test_approver_role_allowed_and_recorded(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="r2", tenant_id="tenant-a"))
    _record_passing_obs(store, "r2", tenant_id="tenant-a")
    client = _client(store)

    r = client.post(
        "/v1/learning/learnings/r2/approve",
        headers=_headers(tenant="tenant-a", approver=APPROVER),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    # Approver + role recorded in the approval audit row.
    conn = store._get_conn()
    row = conn.execute(
        "SELECT approver FROM learning_approvals WHERE item_id = ?", ("r2",)
    ).fetchone()
    assert row is not None
    assert APPROVER in row["approver"]
    assert "role=approver" in row["approver"]


def test_anonymous_mutation_still_401(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="r3", tenant_id="tenant-a"))
    client = _client(store)
    # No x-approver header at all → 401 (anonymous), distinct from 403.
    r = client.post(
        "/v1/learning/learnings/r3/approve",
        headers=_headers(tenant="tenant-a"),
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 4. Rollout staging telemetry
# ---------------------------------------------------------------------------


def test_telemetry_default_off_no_emission(monkeypatch) -> None:
    # PR9a: telemetry is ON by default (safe tier); the byte-quiet no-emission
    # state is reached via the master switch ``MAGI_LEARNING_ENABLED=false``.
    monkeypatch.delenv("MAGI_LEARNING_TELEMETRY_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_LEARNING_ENABLED", "false")
    from magi_agent.learning import telemetry as tel

    sink: list = []
    ev = tel.emit_learning_approval_event(
        tenant_id="tenant-a",
        item_id="approval:abc",
        approver_role="approver",
        user_id="user-123",
        sink=sink.append,
    )
    assert ev is None
    assert sink == []


def test_telemetry_emits_tenant_scoped_hashed_event(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_LEARNING_TELEMETRY_ENABLED", "1")
    from magi_agent.learning import telemetry as tel

    sink: list = []
    ev = tel.emit_learning_approval_event(
        tenant_id="tenant-a",
        item_id="approval:abc",
        approver_role="approver",
        user_id="user-123",
        sink=sink.append,
    )
    assert ev is not None
    assert len(sink) == 1
    dumped = ev.model_dump(by_alias=True, mode="json")
    blob = repr(dumped)
    # Raw user id NEVER present; only the sha256 digest.
    assert "user-123" not in blob
    assert any("sha256:" in str(v) for v in dumped["metadata"].values())
    # Tenant scope present.
    assert any("tenant-a" in str(v) for v in dumped["metadata"].values())


def test_telemetry_promotion_event_from_audit(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_LEARNING_TELEMETRY_ENABLED", "1")
    from magi_agent.learning import telemetry as tel
    from magi_agent.learning.live import LearningLiveAuditRecord
    from magi_agent.gates.learning_live_readiness import _sha256_text_digest

    audit = LearningLiveAuditRecord(
        executionMode="shadow",
        gateEnabled=True,
        readinessReady=True,
        promotedAdapters=("transcript_source", "labeler"),
        promotedAt="2026-06-03T00:00:00.000000Z",
        reasonCodes=("selected_shadow_ready",),
        botId="bot-1",
        tenantId="tenant-a",
        userIdDigest=_sha256_text_digest("user-123"),
    )
    sink: list = []
    ev = tel.emit_learning_promotion_event(audit, sink=sink.append)
    assert ev is not None
    blob = repr(ev.model_dump(by_alias=True, mode="json"))
    assert "user-123" not in blob
    assert "shadow" in blob


def test_telemetry_reflection_event(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_LEARNING_TELEMETRY_ENABLED", "1")
    from magi_agent.learning import telemetry as tel

    sink: list = []
    ev = tel.emit_learning_reflection_event(
        tenant_id="tenant-a",
        candidates_produced=3,
        items_proposed=2,
        items_activated=1,
        sink=sink.append,
    )
    assert ev is not None
    assert len(sink) == 1


# ---------------------------------------------------------------------------
# 5. OSS parity — eval gate threads tenant, single-tenant flow unchanged
# ---------------------------------------------------------------------------


def _candidate(*, ref: str) -> LearningCandidate:
    return LearningCandidate(
        kind="example",
        scope=LearningScope(taskKind="general"),
        content={"situation": "s", "behavior": "b"},
        rationale="r",
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T00:00:00.000000Z",
        ),
        sourceSignalRef=ref,
    )


def test_eval_gate_single_tenant_default_unchanged(tmp_path) -> None:
    store = _store(tmp_path)
    checkset = StaticCheckSet(before=(0.5, 0.5, 0.5, 0.5), after=(0.9, 0.9, 0.9, 0.9))
    decisions = run_eval_gate((_candidate(ref="sig-1"),), store=store, checkset=checkset)
    assert len(decisions) == 1
    assert decisions[0].activated is True
    # Item activated under the default "local" tenant.
    assert store.get(decisions[0].item_id) is not None
    assert store.get(decisions[0].item_id).status == "active"


def test_eval_gate_threads_explicit_tenant(tmp_path) -> None:
    store = _store(tmp_path)
    checkset = StaticCheckSet(before=(0.5, 0.5, 0.5, 0.5), after=(0.9, 0.9, 0.9, 0.9))
    decisions = run_eval_gate(
        (_candidate(ref="sig-2"),),
        store=store,
        checkset=checkset,
        tenant_id="tenant-a",
    )
    item_id = decisions[0].item_id
    # Activated under tenant-a; invisible to local / tenant-b.
    assert store.get(item_id, tenant_id="tenant-a").status == "active"
    assert store.get(item_id, tenant_id="local") is None
    assert store.get(item_id, tenant_id="tenant-b") is None


# ---------------------------------------------------------------------------
# 6. C1 — cross-tenant propose() clobber is impossible
# ---------------------------------------------------------------------------


def _content_candidate(*, ref: str, behavior: str) -> LearningCandidate:
    return LearningCandidate(
        kind="example",
        scope=LearningScope(taskKind="general"),
        content={"situation": "s", "behavior": behavior},
        rationale="r",
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T00:00:00.000000Z",
        ),
        sourceSignalRef=ref,
    )


def test_propose_no_cross_tenant_clobber(tmp_path) -> None:
    """Cross-tenant content with the same source_signal_ref cannot clobber.

    tenant-a proposes (via the eval gate) content with a ``source_signal_ref``;
    tenant-b proposes DIFFERENT content with the SAME ``source_signal_ref``.
    Because the candidate id is tenant-unique (sha1 over ``tenant_id`` +
    ``source_signal_ref``) and propose() is tenant-scoped, tenant-a's row stays
    intact (owned by tenant-a, original content) and tenant-b gets a DISTINCT
    row — cross-tenant clobber is impossible.
    """
    store = _store(tmp_path)
    checkset = StaticCheckSet(before=(0.5, 0.5, 0.5, 0.5), after=(0.9, 0.9, 0.9, 0.9))

    a = run_eval_gate(
        (_content_candidate(ref="signal-shared", behavior="tenant-a-original"),),
        store=store,
        checkset=checkset,
        tenant_id="tenant-a",
    )
    b = run_eval_gate(
        (_content_candidate(ref="signal-shared", behavior="tenant-b-hijack"),),
        store=store,
        checkset=checkset,
        tenant_id="tenant-b",
    )

    a_id, b_id = a[0].item_id, b[0].item_id
    # Distinct rows despite identical source_signal_ref.
    assert a_id != b_id

    # tenant-a's row is intact: owned by tenant-a, original content untouched.
    a_item = store.get(a_id, tenant_id="tenant-a")
    assert a_item is not None
    assert a_item.tenant_id == "tenant-a"
    assert a_item.content["behavior"] == "tenant-a-original"

    # tenant-b owns its OWN distinct row with its own content.
    b_item = store.get(b_id, tenant_id="tenant-b")
    assert b_item is not None
    assert b_item.tenant_id == "tenant-b"
    assert b_item.content["behavior"] == "tenant-b-hijack"

    # No cross-tenant visibility either way.
    assert store.get(a_id, tenant_id="tenant-b") is None
    assert store.get(b_id, tenant_id="tenant-a") is None


def test_propose_cannot_clobber_other_tenant(tmp_path) -> None:
    """A same-id propose from a different tenant cannot overwrite a tenant's row.

    With a composite primary key ``(id, tenant_id)`` a cross-tenant caller who
    proposes a COLLIDING id INSERTS a separate row instead of clobbering — the
    victim tenant's status/content/scope stay byte-for-byte intact and the
    attacker gets its OWN independent row under its own tenant.
    """
    store = _store(tmp_path)
    # tenant-a has an ACTIVE item id="X".
    store.propose(
        _rule(item_id="X", tenant_id="tenant-a", when="orig-when", then="orig-then")
    )
    eval_ref = _record_passing_obs(store, "X", tenant_id="tenant-a")
    a_active = store.approve(
        "X", approver="a", eval_observation_ref=eval_ref, tenant_id="tenant-a"
    )
    assert a_active.status == "active"

    a_before = store.get("X", tenant_id="tenant-a")
    assert a_before is not None

    # tenant-b proposes id="X" with DIFFERENT content/scope.
    store.propose(
        _rule(
            item_id="X",
            tenant_id="tenant-b",
            when="hijack-when",
            then="hijack-then",
        )
    )

    # tenant-a's row is byte-for-byte intact: status/content/scope unchanged.
    a_after = store.get("X", tenant_id="tenant-a")
    assert a_after is not None
    assert a_after.status == "active"
    assert a_after.content == a_before.content
    assert a_after.content["when"] == "orig-when"
    assert a_after.content["then"] == "orig-then"
    assert a_after.scope == a_before.scope
    assert a_after.tenant_id == "tenant-a"

    # tenant-b has its OWN independent id="X" row under tenant-b.
    b_item = store.get("X", tenant_id="tenant-b")
    assert b_item is not None
    assert b_item.tenant_id == "tenant-b"
    assert b_item.status == "proposed"
    assert b_item.content["when"] == "hijack-when"
    assert b_item.content["then"] == "hijack-then"


def test_propose_same_tenant_is_idempotent_upsert(tmp_path) -> None:
    """Re-proposing a still-proposed item under the SAME tenant upserts in place."""
    store = _store(tmp_path)
    store.propose(_rule(item_id="Y", tenant_id="tenant-a", then="v1"))
    store.propose(_rule(item_id="Y", tenant_id="tenant-a", then="v2"))

    # Exactly one row for (Y, tenant-a); content reflects the latest propose.
    page = store.list(tenant_id="tenant-a")
    ids = [i.id for i in page.items]
    assert ids.count("Y") == 1
    item = store.get("Y", tenant_id="tenant-a")
    assert item is not None
    assert item.content["then"] == "v2"


def test_eval_gate_candidate_ids_are_tenant_unique(tmp_path) -> None:
    """Same source_signal_ref + different tenants → DISTINCT store rows.

    Defense in depth: the candidate id is derived from ``tenant_id`` +
    ``source_signal_ref``, so cross-tenant content can never share an id and the
    upsert path can never be reached across tenants.
    """
    store = _store(tmp_path)
    checkset = StaticCheckSet(before=(0.5, 0.5, 0.5, 0.5), after=(0.9, 0.9, 0.9, 0.9))

    a = run_eval_gate(
        (_candidate(ref="same-signal"),), store=store, checkset=checkset, tenant_id="tenant-a"
    )
    b = run_eval_gate(
        (_candidate(ref="same-signal"),), store=store, checkset=checkset, tenant_id="tenant-b"
    )

    # Distinct ids despite identical source_signal_ref.
    assert a[0].item_id != b[0].item_id
    # Each tenant owns its own row; neither bleeds across.
    assert store.get(a[0].item_id, tenant_id="tenant-a") is not None
    assert store.get(a[0].item_id, tenant_id="tenant-b") is None
    assert store.get(b[0].item_id, tenant_id="tenant-b") is not None
    assert store.get(b[0].item_id, tenant_id="tenant-a") is None


# ---------------------------------------------------------------------------
# 7. I1 — reflection-run is tenant-scoped (no longer always "local")
# ---------------------------------------------------------------------------


def test_reflection_run_writes_under_request_tenant(tmp_path, monkeypatch) -> None:
    """A non-local tenant's reflection run writes under ITS tenant, not "local"."""
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob
    from magi_agent.harness.learning_executor import LearningReflectionConfig
    from magi_agent.learning.candidates import LocalFakeTranscriptSource, SessionTrace

    monkeypatch.setenv("MAGI_LEARNING_REFLECTION_ENABLED", "1")

    store = _store(tmp_path)
    trace = SessionTrace(
        session_id="sess-xyz",
        turns=({"role": "user", "text": "hi"}, {"role": "agent", "text": "done"}),
        final_output="done",
        ts="2026-06-03T10:00:00Z",
    )
    job = LearningReflectionCronJob(
        source=LocalFakeTranscriptSource(traces=(trace,)),
        store=store,
        config=LearningReflectionConfig(enabled=True),
    )
    service = LearningGovernanceService(store, tenant_id="tenant-a", reflection_job=job)

    summary = asyncio.run(service.run_reflection())
    assert summary.status == "ok"
    # At least one item was written (proposed and/or activated).
    written = summary.items_proposed + summary.items_activated
    assert written >= 1

    # Items are visible under tenant-a, and NOT under "local".
    tenant_a_items = store.list(tenant_id="tenant-a").items
    assert len(tenant_a_items) == written
    assert all(i.tenant_id == "tenant-a" for i in tenant_a_items)
    assert store.list(tenant_id="local").items == ()


# ---------------------------------------------------------------------------
# 8. I2 — is_approver resolver raising → clean 503 (not 500)
# ---------------------------------------------------------------------------


def test_resolver_exception_returns_503(tmp_path) -> None:
    store = _store(tmp_path)
    store.propose(_rule(item_id="r-503", tenant_id="tenant-a"))
    _record_passing_obs(store, "r-503", tenant_id="tenant-a")

    def _raising_resolver(tenant_id: str, approver: str) -> bool:
        raise RuntimeError("role store unreachable")

    app = FastAPI()
    router = build_learning_dashboard_router(
        store=store, gateway_token=GATEWAY_TOKEN, is_approver=_raising_resolver
    )
    app.include_router(router)
    client = TestClient(app)

    r = client.post(
        "/v1/learning/learnings/r-503/approve",
        headers=_headers(tenant="tenant-a", approver=APPROVER),
    )
    assert r.status_code == 503
    assert r.json() == {"error": "role_check_unavailable"}


# ---------------------------------------------------------------------------
# 9. MINOR — telemetry never crashes the caller
# ---------------------------------------------------------------------------


def test_telemetry_construction_failure_returns_none(monkeypatch) -> None:
    """An emit that fails model construction logs WARNING and returns None."""
    monkeypatch.setenv("MAGI_LEARNING_TELEMETRY_ENABLED", "1")
    from magi_agent.learning import telemetry as tel

    # Force DeterministicRuntimeEvent construction to raise, simulating an
    # odd/protected token reaching the strict event model.
    def _boom(*args, **kwargs):
        raise ValueError("protected token rejected")

    monkeypatch.setattr(tel, "DeterministicRuntimeEvent", _boom)

    sink: list = []
    ev = tel.emit_learning_approval_event(
        tenant_id="tenant-a",
        item_id="approval:odd",
        approver_role="approver",
        user_id="bot::weird\x00token",
        sink=sink.append,
    )
    assert ev is None
    assert sink == []
