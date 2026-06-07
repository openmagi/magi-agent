"""Task 3 — richer eval observation: persist significance ``stats`` + ``std``.

The paired-significance gate produces ``delta``/``se``/``ci``/``verdict`` stats,
but the persisted eval observation only recorded ``before``/``after``/``sample_n``/
``passed`` — so a reviewer could not tell a significant result from noise.

These tests pin:
  * store round-trip of an optional ``stats`` dict (NULL when omitted);
  * back-compat for rows recorded the old way (no ``stats``);
  * ``run_eval_gate`` paired path persisting verdict/delta/se/ci/z + before/after
    ``std``, while the strict_band path records NULL stats and no ``std``.
"""
from __future__ import annotations

import json
import sqlite3

from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.eval_gate import (
    EvalGateConfig,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningItem, LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path, name: str = "learning.db") -> SqliteLearningStore:
    return SqliteLearningStore(db_path=name, workspace_root=str(tmp_path))


def _proposed_rule(store: SqliteLearningStore) -> LearningItem:
    item = LearningItem(
        id="learning:rule-stats",
        kind="rule",
        scope=LearningScope(taskKind="coding"),
        content={"when": "always", "then": "test first"},
        rationale="TDD",
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T00:00:00Z",
        ),
    )
    return store.propose(item)


def _candidate(*, kind: str = "example", sid: str = "sess-1") -> LearningCandidate:
    if kind == "rule":
        content = {"when": "user asks", "then": "be concise"}
    elif kind == "eval":
        content = {"input": "user asks", "expected": "concise"}
    else:
        content = {"situation": "user asks", "behavior": "be concise"}
    return LearningCandidate(
        kind=kind,
        scope=LearningScope(taskKind="general", tags=("style",)),
        content=content,
        rationale="prefer concise answers",
        provenance=Provenance(
            sessionIds=(sid,),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
        sourceSignalRef=f"signal:diff@{sid}",
    )


def _improved_checkset() -> StaticCheckSet:
    return StaticCheckSet(before=(0.5, 0.5, 0.4, 0.6), after=(0.8, 0.9, 0.9, 0.9))


def _raw_obs_row(store: SqliteLearningStore, ref: str) -> dict[str, object]:
    conn = store._get_conn()  # test-only direct read
    row = conn.execute(
        "SELECT * FROM learning_eval_observations WHERE ref = ?", (ref,)
    ).fetchone()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------


def test_record_with_stats_round_trips(tmp_path) -> None:
    store = _store(tmp_path)
    proposed = _proposed_rule(store)
    stats = {
        "delta": 0.4,
        "se": 0.05,
        "ci_low": 0.3,
        "ci_high": 0.5,
        "verdict": "improved",
        "z": 1.96,
        "repeats": 1,
    }
    ref = store.record_eval_observation(
        item_id=proposed.id,
        before={"mean": 0.5, "n": 4},
        after={"mean": 0.9, "n": 4},
        sample_n=4,
        passed=True,
        stats=stats,
    )

    obs = store.get_eval_observation(ref)
    store.close()

    assert obs is not None
    assert obs["stats"] == stats
    assert obs["passed"] is True
    assert obs["sample_n"] == 4


def test_record_without_stats_reads_back_none(tmp_path) -> None:
    store = _store(tmp_path)
    proposed = _proposed_rule(store)
    ref = store.record_eval_observation(
        item_id=proposed.id,
        before={"mean": 0.5},
        after={"mean": 0.9},
        sample_n=4,
        passed=True,
    )

    obs = store.get_eval_observation(ref)
    raw = _raw_obs_row(store, ref)
    store.close()

    assert obs is not None
    assert obs["stats"] is None
    # SQL NULL, not the JSON string "null".
    assert raw["stats_json"] is None


def test_get_eval_observation_unknown_ref_returns_none(tmp_path) -> None:
    store = _store(tmp_path)
    out = store.get_eval_observation("eval-obs:does-not-exist")
    store.close()
    assert out is None


def test_existing_rows_without_stats_column_still_read(tmp_path) -> None:
    """Migration back-compat: an observation row created before the stats_json
    column existed must still read fine (column added by the additive ALTER)."""
    store = _store(tmp_path)
    proposed = _proposed_rule(store)
    ref = store.record_eval_observation(
        item_id=proposed.id,
        before={"mean": 0.5},
        after={"mean": 0.9},
        sample_n=4,
        passed=False,
    )
    obs = store.get_eval_observation(ref)
    store.close()
    assert obs is not None
    assert obs["stats"] is None
    assert obs["passed"] is False


def test_migration_6_adds_stats_json_to_preexisting_db(tmp_path) -> None:
    """A DB created BEFORE the stats_json column existed gets it via the additive
    ALTER (migration 6), and records then round-trip with stats."""
    db_path = tmp_path / "old.db"
    # Hand-build a pre-migration-6 schema: eval-observation table WITHOUT
    # stats_json, version pinned at 5 so migration 6 is the only one to run.
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE _learning_schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        INSERT INTO _learning_schema_version (version)
            VALUES (1), (2), (3), (4), (5);
        CREATE TABLE learning_eval_observations (
            ref         TEXT PRIMARY KEY,
            item_id     TEXT NOT NULL,
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json  TEXT NOT NULL DEFAULT '{}',
            sample_n    INTEGER NOT NULL DEFAULT 0,
            passed      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE TABLE learning_items (
            id          TEXT NOT NULL,
            tenant_id   TEXT NOT NULL DEFAULT 'local',
            kind        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'proposed',
            scope_json  TEXT NOT NULL DEFAULT '{}',
            content_json TEXT NOT NULL DEFAULT '{}',
            rationale   TEXT NOT NULL DEFAULT '',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            version     INTEGER NOT NULL DEFAULT 1,
            supersedes  TEXT,
            embedding_ref TEXT,
            stats_json  TEXT NOT NULL DEFAULT '{}',
            eval_observation_ref TEXT,
            approval_ref TEXT,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            scope_task_kind TEXT
                GENERATED ALWAYS AS (json_extract(scope_json, '$.taskKind')) VIRTUAL,
            PRIMARY KEY (id, tenant_id)
        );
        """
    )
    conn.commit()
    cols_before = {
        r[1]
        for r in conn.execute("PRAGMA table_info(learning_eval_observations)").fetchall()
    }
    conn.close()
    assert "stats_json" not in cols_before  # precondition: old schema

    # Opening through the store runs migration 6 (the additive ALTER).
    store = _store(tmp_path, name="old.db")
    proposed = _proposed_rule(store)
    ref = store.record_eval_observation(
        item_id=proposed.id,
        before={"mean": 0.5, "n": 4},
        after={"mean": 0.9, "n": 4},
        sample_n=4,
        passed=True,
        stats={"verdict": "improved", "delta": 0.4},
    )
    obs = store.get_eval_observation(ref)
    conn2 = store._get_conn()
    cols_after = {
        r[1]
        for r in conn2.execute("PRAGMA table_info(learning_eval_observations)").fetchall()
    }
    store.close()

    assert "stats_json" in cols_after  # ALTER applied
    assert obs is not None
    assert obs["stats"] == {"verdict": "improved", "delta": 0.4}


# ---------------------------------------------------------------------------
# run_eval_gate wiring
# ---------------------------------------------------------------------------


def test_paired_path_persists_stats_and_std(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_improved_checkset(),
        config=EvalGateConfig(decisionRule="paired_significance", z=1.96),
    )
    decision = decisions[0]
    obs = store.get_eval_observation(decision.eval_observation_ref)
    store.close()

    assert obs is not None
    stats = obs["stats"]
    assert stats is not None
    assert stats["verdict"] == "improved"
    assert stats["delta"] == decision.delta
    assert stats["se"] == decision.se
    assert stats["ci_low"] == decision.ci_low
    assert stats["ci_high"] == decision.ci_high
    assert stats["z"] == 1.96
    assert stats["repeats"] == 1

    # before/after enriched with std (>=2 samples).
    before = json.loads(obs["before_json"])
    after = json.loads(obs["after_json"])
    assert "std" in before
    assert "std" in after
    assert before["n"] == 4
    assert after["n"] == 4


def test_strict_band_path_records_null_stats_and_no_std(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_improved_checkset(),
        # default → strict_band
    )
    decision = decisions[0]
    obs = store.get_eval_observation(decision.eval_observation_ref)
    store.close()

    assert obs is not None
    assert obs["stats"] is None
    before = json.loads(obs["before_json"])
    after = json.loads(obs["after_json"])
    # strict_band path unchanged — no std key added.
    assert "std" not in before
    assert "std" not in after
    assert before == {"mean": 0.5, "n": 4}
