"""C3 — SkillCurator tests (TDD, in-memory store, inactivity-triggered, archive-only).

All tests use an in-memory SqliteLearningStore (:memory:) so no filesystem I/O
and no shared state between test cases.

Test categories
---------------
1. inactivity_trigger   — should_run_curator() contract
2. archive_only         — items transitioned to archived, never deleted
3. pinned_exempt        — pinned items survive the pass
4. active_approved      — human-approved active items NOT archived
5. snapshot             — snapshot captured before mutating pass + restorable
6. gate_off             — MAGI_SKILL_CURATOR_ENABLED=0 → no-op
7. shadow               — MAGI_SKILL_CURATOR_SHADOW=1 → compute but don't mutate
8. evidence             — evidence record fields (counts, snapshot_ref, no raw content)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import tempfile

import pytest

# ---------------------------------------------------------------------------
# Lazy import helpers — keep test file importable before the module exists
# ---------------------------------------------------------------------------
from magi_agent.harness.skill_curator import (
    CuratorConfig,
    CuratorResult,
    SkillCurator,
    should_run_curator,
)
from magi_agent.learning.models import LearningItem, LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SCOPE = LearningScope(taskKind="self-review")
_SCOPE_CODING = LearningScope(taskKind="coding")


def _now() -> datetime:
    return datetime.now(UTC)


def _prov(*, derived_by: str = "reflection") -> Provenance:
    return Provenance(
        sessionIds=(f"session-{uuid.uuid4().hex[:8]}",),
        derivedBy=derived_by,  # type: ignore[arg-type]
        createdAt=datetime.now(UTC).isoformat(),
    )


def _make_example(
    *,
    id_suffix: str = "",
    status: str = "proposed",
    derived_by: str = "reflection",
    tenant_id: str = "local",
    pinned: bool = False,
    scope: LearningScope | None = None,
) -> LearningItem:
    uid = f"item-{uuid.uuid4().hex[:8]}{id_suffix}"
    return LearningItem.model_validate(
        {
            "id": uid,
            "tenantId": tenant_id,
            "kind": "example",
            "status": status,
            "scope": (scope or _SCOPE).model_dump(by_alias=True),
            "content": {"situation": "test", "behavior": "test-behavior"},
            "rationale": "unit-test item",
            "provenance": _prov(derived_by=derived_by).model_dump(by_alias=True),
            "pinned": pinned,
        }
    )


def _make_rule(
    *,
    id_suffix: str = "",
    status: str = "proposed",
    derived_by: str = "reflection",
    tenant_id: str = "local",
    pinned: bool = False,
) -> LearningItem:
    uid = f"rule-{uuid.uuid4().hex[:8]}{id_suffix}"
    return LearningItem.model_validate(
        {
            "id": uid,
            "tenantId": tenant_id,
            "kind": "rule",
            "status": status,
            "scope": _SCOPE.model_dump(by_alias=True),
            "content": {"when": "test-when", "then": "test-then"},
            "rationale": "unit-test rule",
            "provenance": _prov(derived_by=derived_by).model_dump(by_alias=True),
            "pinned": pinned,
        }
    )


def _in_memory_store(*, workspace_root: str = "") -> SqliteLearningStore:
    """Return a fresh isolated SQLite learning store backed by a tmpdir.

    SQLite's ":memory:" path is relative to cwd when passed through
    SqliteLearningStore (which joins it with workspace_root / cwd), so all
    ":memory:" calls would share the same on-disk file.  Using a fresh
    tempfile per call guarantees test isolation.
    """
    tmp = tempfile.mkdtemp()
    store = SqliteLearningStore(db_path="learning.db", workspace_root=tmp)
    # Force migrations to run
    store._get_conn()
    return store


def _propose_stale(store: SqliteLearningStore, *, tenant_id: str = "local") -> LearningItem:
    """Propose an item and backdated its updated_at to >30 days ago (stale)."""
    item = _make_example(tenant_id=tenant_id)
    proposed = store.propose(item)
    # Manually backdate updated_at so it appears stale
    conn = store._get_conn()
    old_ts = (datetime.now(UTC) - timedelta(days=35)).isoformat()
    conn.execute(
        "UPDATE learning_items SET updated_at = ?, created_at = ? WHERE id = ? AND tenant_id = ?",
        (old_ts, old_ts, proposed.id, tenant_id),
    )
    conn.commit()
    return proposed


# ---------------------------------------------------------------------------
# 1. Inactivity trigger tests
# ---------------------------------------------------------------------------

class TestShouldRunCurator:
    def test_runs_when_idle_and_interval_elapsed(self) -> None:
        now = _now()
        last_run = now - timedelta(hours=200)  # > 7 days
        last_activity = now - timedelta(hours=24)  # idle for 24h
        assert should_run_curator(
            now=now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=168,
        ) is True

    def test_does_not_run_before_interval(self) -> None:
        now = _now()
        last_run = now - timedelta(hours=100)  # < 7 days
        last_activity = now - timedelta(hours=48)
        assert should_run_curator(
            now=now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=168,
        ) is False

    def test_does_not_run_when_active(self) -> None:
        now = _now()
        last_run = now - timedelta(hours=200)
        last_activity = now - timedelta(seconds=30)  # very recent activity
        assert should_run_curator(
            now=now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=168,
            idle_threshold_seconds=3600,
        ) is False

    def test_runs_when_never_run_before(self) -> None:
        now = _now()
        last_activity = now - timedelta(hours=24)
        assert should_run_curator(
            now=now,
            last_run_at=None,
            last_activity_at=last_activity,
            interval_hours=168,
        ) is True

    def test_custom_interval_hours(self) -> None:
        now = _now()
        last_run = now - timedelta(hours=3)  # < 4h
        last_activity = now - timedelta(hours=2)
        assert should_run_curator(
            now=now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=4,
        ) is False

        last_run2 = now - timedelta(hours=5)  # > 4h
        assert should_run_curator(
            now=now,
            last_run_at=last_run2,
            last_activity_at=last_activity,
            interval_hours=4,
        ) is True

    def test_exactly_at_boundary_runs(self) -> None:
        now = _now()
        interval = 168
        last_run = now - timedelta(hours=interval)
        last_activity = now - timedelta(hours=24)
        # At exact boundary: should run (>= boundary)
        assert should_run_curator(
            now=now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=interval,
        ) is True


# ---------------------------------------------------------------------------
# 2. Archive-only tests (no hard delete)
# ---------------------------------------------------------------------------

class TestArchiveOnly:
    def test_stale_proposed_items_archived_not_deleted(self) -> None:
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        # Item still exists in store (not hard-deleted)
        found = store.get(stale.id, tenant_id="local")
        assert found is not None
        assert found.status == "archived"
        assert result.archived_count >= 1

    def test_archived_items_remain_retrievable(self) -> None:
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        # Should be retrievable with status=archived
        from magi_agent.learning.store import Page
        page: Page = store.list(tenant_id="local", status="archived")
        ids = [i.id for i in page.items]
        assert stale.id in ids

    def test_fresh_proposed_items_not_archived(self) -> None:
        store = _in_memory_store()
        fresh = store.propose(_make_example())  # just proposed, updated_at = now

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        found = store.get(fresh.id, tenant_id="local")
        assert found is not None
        assert found.status == "proposed"
        assert result.archived_count == 0


# ---------------------------------------------------------------------------
# 3. Pinned-exempt tests
# ---------------------------------------------------------------------------

class TestPinnedExempt:
    def test_pinned_stale_item_not_archived(self) -> None:
        store = _in_memory_store()
        # Propose pinned item and backdate it
        pinned_item = _make_example(pinned=True)
        store.propose(pinned_item)
        conn = store._get_conn()
        old_ts = (datetime.now(UTC) - timedelta(days=35)).isoformat()
        conn.execute(
            "UPDATE learning_items SET updated_at = ?, created_at = ? WHERE id = ? AND tenant_id = ?",
            (old_ts, old_ts, pinned_item.id, "local"),
        )
        conn.commit()

        curator = SkillCurator(store=store)
        curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        found = store.get(pinned_item.id, tenant_id="local")
        assert found is not None
        assert found.status == "proposed"  # exempt: not archived

    def test_unpinned_stale_archived_pinned_spared(self) -> None:
        store = _in_memory_store()
        stale_unpinned = _propose_stale(store)

        pinned_item = _make_example(pinned=True)
        store.propose(pinned_item)
        conn = store._get_conn()
        old_ts = (datetime.now(UTC) - timedelta(days=35)).isoformat()
        conn.execute(
            "UPDATE learning_items SET updated_at = ?, created_at = ? WHERE id = ? AND tenant_id = ?",
            (old_ts, old_ts, pinned_item.id, "local"),
        )
        conn.commit()

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        # Unpinned stale gets archived
        assert store.get(stale_unpinned.id, tenant_id="local").status == "archived"
        # Pinned stale is spared
        assert store.get(pinned_item.id, tenant_id="local").status == "proposed"
        assert result.archived_count == 1
        assert result.pinned_exempt_count >= 1


# ---------------------------------------------------------------------------
# 4. Active-approved items NOT archived
# ---------------------------------------------------------------------------

class TestActiveApprovedNotArchived:
    def _make_active_item(self, store: SqliteLearningStore, *, tenant_id: str = "local") -> LearningItem:
        """Propose + activate an example item via eval observation + auto_activate."""
        item = _make_example(tenant_id=tenant_id)
        store.propose(item)
        # Record a passing eval observation
        obs_ref = store.record_eval_observation(
            item_id=item.id,
            before={},
            after={"score": 0.9},
            sample_n=5,
            passed=True,
            tenant_id=tenant_id,
        )
        return store.auto_activate(item.id, eval_observation_ref=obs_ref, tenant_id=tenant_id)

    def test_active_approved_not_archived_by_curator(self) -> None:
        store = _in_memory_store()
        active = self._make_active_item(store)

        # Backdate it so it would normally be stale
        conn = store._get_conn()
        old_ts = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        conn.execute(
            "UPDATE learning_items SET updated_at = ?, created_at = ? WHERE id = ? AND tenant_id = ?",
            (old_ts, old_ts, active.id, "local"),
        )
        conn.commit()

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        found = store.get(active.id, tenant_id="local")
        assert found is not None
        assert found.status == "active"  # NOT archived
        assert result.archived_count == 0

    def test_only_proposed_stale_reflection_items_archived(self) -> None:
        """Curator targets proposed + derived_by=reflection items only."""
        store = _in_memory_store()
        active_item = self._make_active_item(store)
        stale_proposed = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        assert store.get(active_item.id, tenant_id="local").status == "active"
        assert store.get(stale_proposed.id, tenant_id="local").status == "archived"
        assert result.archived_count == 1


# ---------------------------------------------------------------------------
# 5. Snapshot/backup before mutating pass
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_captured_before_archive_pass(self) -> None:
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        assert result.snapshot_ref is not None
        assert len(result.snapshot_ref) > 0

    def test_snapshot_is_restorable(self) -> None:
        """After archive pass, snapshot should contain the item in its pre-archive state."""
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        assert result.snapshot_ref is not None
        # Retrieve snapshot from store
        snapshot_data = curator.get_snapshot(result.snapshot_ref)
        assert snapshot_data is not None
        # Should contain the item pre-archive state (status=proposed)
        ids_in_snapshot = [e["id"] for e in snapshot_data["items"]]
        assert stale.id in ids_in_snapshot
        pre_archive_statuses = {e["id"]: e["status"] for e in snapshot_data["items"]}
        assert pre_archive_statuses[stale.id] == "proposed"

    def test_no_snapshot_when_nothing_to_archive(self) -> None:
        """If no items would be archived, snapshot_ref is None (nothing to back up)."""
        store = _in_memory_store()
        # Fresh item only — won't be archived
        store.propose(_make_example())

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        assert result.snapshot_ref is None
        assert result.archived_count == 0


# ---------------------------------------------------------------------------
# 6. Gate-off — no-op
# ---------------------------------------------------------------------------

class TestGateOff:
    def test_gate_off_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SKILL_CURATOR_ENABLED", "0")
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig.from_env(),
        )

        assert result.gate_off is True
        assert result.archived_count == 0
        assert store.get(stale.id, tenant_id="local").status == "proposed"

    def test_gate_off_explicit_config(self) -> None:
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=False, shadow=False),
        )

        assert result.gate_off is True
        assert result.archived_count == 0
        assert store.get(stale.id, tenant_id="local").status == "proposed"


# ---------------------------------------------------------------------------
# 7. Shadow mode — compute but don't mutate
# ---------------------------------------------------------------------------

class TestShadowMode:
    def test_shadow_computes_would_archive_set_but_does_not_mutate(self) -> None:
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=True),
        )

        assert result.shadow is True
        assert result.would_archive_count >= 1  # computed the set
        assert result.archived_count == 0  # NOT mutated
        # Item is still proposed
        assert store.get(stale.id, tenant_id="local").status == "proposed"

    def test_shadow_does_not_write_snapshot(self) -> None:
        """Shadow mode must not write a real snapshot (no mutation = no backup needed)."""
        store = _in_memory_store()
        _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=True),
        )

        # shadow → no real mutation → no snapshot written
        assert result.snapshot_ref is None

    def test_shadow_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SKILL_CURATOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SKILL_CURATOR_SHADOW", "1")
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig.from_env(),
        )

        assert result.shadow is True
        assert store.get(stale.id, tenant_id="local").status == "proposed"


# ---------------------------------------------------------------------------
# 8. Evidence record — redaction contract
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_evidence_contains_counts_not_raw_content(self) -> None:
        store = _in_memory_store()
        _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        ev = result.evidence
        assert ev is not None
        assert ev.type.startswith("custom:")
        # Evidence must have archived_count, snapshot_ref (or None), not raw content
        fields = ev.fields
        assert "archivedCount" in fields
        assert "snapshotRef" in fields
        # No raw item content or rationale in evidence
        assert "content" not in fields
        assert "rationale" not in fields

    def test_evidence_item_refs_are_digests_not_raw_ids(self) -> None:
        """Item identifiers in evidence should be digested, not raw IDs."""
        store = _in_memory_store()
        stale = _propose_stale(store)

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        ev = result.evidence
        assert ev is not None
        # archivedItemDigests should be present and not contain raw IDs.
        # EvidenceRecord._freeze_mapping converts lists to tuples, so accept both.
        item_digests = ev.fields.get("archivedItemDigests", ())
        assert isinstance(item_digests, (list, tuple))
        # Each digest should be a hex string (sha256), not the raw item id
        for d in item_digests:
            assert stale.id not in str(d), "raw item id must not appear in evidence"
            assert len(str(d)) >= 16  # at least a meaningful digest

    def test_authority_flags_all_false(self) -> None:
        store = _in_memory_store()

        curator = SkillCurator(store=store)
        result = curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        flags = result.authority_flags
        assert flags.agent_spawned is False
        assert flags.network_call_allowed is False
        assert flags.live_tool_execution is False


# ---------------------------------------------------------------------------
# 9. Inactivity gate integration — last_run_at persisted
# ---------------------------------------------------------------------------

class TestInactivityGateIntegration:
    def test_run_updates_last_run_at(self) -> None:
        store = _in_memory_store()
        curator = SkillCurator(store=store)
        before = _now()

        curator.run(
            now=before,
            tenant_id="local",
            config=CuratorConfig(enabled=True, shadow=False),
        )

        persisted = curator.get_last_run_at(tenant_id="local")
        assert persisted is not None
        # Should be approximately the 'before' time
        diff = abs((persisted - before).total_seconds())
        assert diff < 5

    def test_gate_off_does_not_update_last_run_at(self) -> None:
        store = _in_memory_store()
        curator = SkillCurator(store=store)

        curator.run(
            now=_now(),
            tenant_id="local",
            config=CuratorConfig(enabled=False, shadow=False),
        )

        persisted = curator.get_last_run_at(tenant_id="local")
        assert persisted is None
