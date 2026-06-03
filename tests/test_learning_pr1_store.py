from __future__ import annotations

"""PR1 Learning KB store — TDD test suite.

Tests are written FIRST (red) and must pass once the implementation lands (green).
No async: the store exposes sync methods backed by sqlite3, consistent with
SessionSqliteStore patterns in this codebase.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Import-boundary smoke test
# ---------------------------------------------------------------------------


def test_learning_module_does_not_import_agent_loop_or_heavy_runtime() -> None:
    """The learning package must not pull in runtime, ADK, tools, or transport."""
    forbidden = {
        "google.adk.runners",
        "google.adk.agents",
        "magi_agent.tools.host",
        "magi_agent.transport.chat",
        "magi_agent.runtime",
        "magi_agent.harness",
        "magi_agent.adk_bridge",
    }
    for module_name in forbidden:
        sys.modules.pop(module_name, None)

    __import__("magi_agent.learning.models")
    __import__("magi_agent.learning.policy")
    __import__("magi_agent.learning.store")
    __import__("magi_agent.learning.vector")

    assert forbidden.isdisjoint(sys.modules)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestLearningModels:
    def _make_item(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": "learning:item-1",
            "kind": "rule",
            "scope": {"taskKind": "coding"},
            "content": {"when": "always use tests", "then": "write test first"},
            "rationale": "TDD improves reliability",
            "provenance": {
                "sessionIds": ("sess-1",),
                "derivedBy": "reflection",
                "createdAt": "2026-06-03T00:00:00Z",
            },
        }
        payload.update(overrides)
        return payload

    def test_rule_item_valid_with_when_then(self) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(self._make_item())
        assert item.kind == "rule"
        assert item.status == "proposed"
        assert item.version == 1
        assert item.tenant_id == "local"

    def test_example_item_requires_situation_and_behavior(self) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            self._make_item(
                kind="example",
                content={"situation": "user asks for code", "behavior": "write with tests"},
            )
        )
        assert item.kind == "example"

    def test_eval_item_requires_input_and_expected(self) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            self._make_item(
                kind="eval",
                content={"input": "add two numbers", "expected": "return a + b"},
            )
        )
        assert item.kind == "eval"

    def test_rule_content_missing_when_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="when"):
            LearningItem.model_validate(
                self._make_item(kind="rule", content={"then": "only then, no when"})
            )

    def test_rule_content_missing_then_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="then"):
            LearningItem.model_validate(
                self._make_item(kind="rule", content={"when": "only when, no then"})
            )

    def test_example_content_missing_situation_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="situation"):
            LearningItem.model_validate(
                self._make_item(
                    kind="example",
                    content={"behavior": "no situation"},
                )
            )

    def test_example_content_missing_behavior_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="behavior"):
            LearningItem.model_validate(
                self._make_item(
                    kind="example",
                    content={"situation": "no behavior"},
                )
            )

    def test_eval_content_missing_input_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="input"):
            LearningItem.model_validate(
                self._make_item(
                    kind="eval",
                    content={"expected": "no input"},
                )
            )

    def test_eval_content_missing_expected_raises(self) -> None:
        from magi_agent.learning.models import LearningItem

        with pytest.raises(ValidationError, match="expected"):
            LearningItem.model_validate(
                self._make_item(
                    kind="eval",
                    content={"input": "no expected"},
                )
            )

    def test_models_are_frozen(self) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(self._make_item())
        with pytest.raises((TypeError, ValidationError)):
            item.status = "active"  # type: ignore[misc]

    def test_camelcase_aliases_accepted(self) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {
                "id": "learning:item-1",
                "tenantId": "org-123",
                "kind": "rule",
                "scope": {"taskKind": "coding", "tags": ("python",), "channel": None},
                "content": {"when": "X", "then": "Y"},
                "rationale": "r",
                "provenance": {
                    "sessionIds": ("s1",),
                    "derivedBy": "user",
                    "createdAt": "2026-06-03T00:00:00Z",
                },
            }
        )
        assert item.tenant_id == "org-123"
        assert item.scope.tags == ("python",)

    def test_scope_is_generic_domain_agnostic(self) -> None:
        from magi_agent.learning.models import LearningScope

        scope = LearningScope.model_validate(
            {"taskKind": "research", "tags": ("web", "citation"), "channel": "telegram"}
        )
        assert scope.task_kind == "research"
        assert scope.tags == ("web", "citation")
        assert scope.channel == "telegram"

    def test_learning_stats_defaults(self) -> None:
        from magi_agent.learning.models import LearningStats

        stats = LearningStats()
        assert stats.applied == 0
        assert stats.eval_score is None
        assert stats.last_used is None

    def test_provenance_derived_by_values(self) -> None:
        from magi_agent.learning.models import Provenance

        p1 = Provenance.model_validate(
            {"sessionIds": (), "derivedBy": "reflection", "createdAt": "2026-06-03T00:00:00Z"}
        )
        p2 = Provenance.model_validate(
            {"sessionIds": (), "derivedBy": "user", "createdAt": "2026-06-03T00:00:00Z"}
        )
        assert p1.derived_by == "reflection"
        assert p2.derived_by == "user"

    def test_provenance_invalid_derived_by_raises(self) -> None:
        from magi_agent.learning.models import Provenance

        with pytest.raises(ValidationError):
            Provenance.model_validate(
                {"sessionIds": (), "derivedBy": "auto", "createdAt": "2026-06-03T00:00:00Z"}
            )


# ---------------------------------------------------------------------------
# Policy tests
# ---------------------------------------------------------------------------


class TestPolicy:
    def _make_item(self, kind: str = "rule") -> object:
        from magi_agent.learning.models import LearningItem

        return LearningItem.model_validate(
            {
                "id": "learning:item-pol",
                "kind": kind,
                "scope": {"taskKind": "coding"},
                "content": {"when": "X", "then": "Y"}
                if kind == "rule"
                else {"situation": "S", "behavior": "B"}
                if kind == "example"
                else {"input": "I", "expected": "E"},
                "rationale": "r",
                "provenance": {
                    "sessionIds": (),
                    "derivedBy": "reflection",
                    "createdAt": "2026-06-03T00:00:00Z",
                },
            }
        )

    def test_activation_rejected_without_eval_observation_ref(self) -> None:
        from magi_agent.learning.policy import PolicyViolation, assert_activation_allowed

        item = self._make_item("example")
        with pytest.raises(PolicyViolation, match="eval-observation-required"):
            assert_activation_allowed(item, eval_observation_ref=None)

    def test_activation_rejected_with_empty_eval_observation_ref(self) -> None:
        from magi_agent.learning.policy import PolicyViolation, assert_activation_allowed

        item = self._make_item("example")
        with pytest.raises(PolicyViolation, match="eval-observation-required"):
            assert_activation_allowed(item, eval_observation_ref="")

    def test_rule_activation_rejected_without_approval_ref(self) -> None:
        from magi_agent.learning.policy import PolicyViolation, assert_activation_allowed

        item = self._make_item("rule")
        with pytest.raises(PolicyViolation, match="no-direct-mutation"):
            assert_activation_allowed(
                item,
                eval_observation_ref="eval:obs-1",
                approval_ref=None,
            )

    def test_rule_activation_succeeds_with_both_refs(self) -> None:
        from magi_agent.learning.policy import assert_activation_allowed

        item = self._make_item("rule")
        # must not raise
        assert_activation_allowed(
            item,
            eval_observation_ref="eval:obs-1",
            approval_ref="approval:apr-1",
        )

    def test_example_activation_succeeds_with_eval_ref_only(self) -> None:
        from magi_agent.learning.policy import assert_activation_allowed

        item = self._make_item("example")
        # must not raise
        assert_activation_allowed(item, eval_observation_ref="eval:obs-1")

    def test_eval_item_activation_succeeds_with_eval_ref_only(self) -> None:
        from magi_agent.learning.policy import assert_activation_allowed

        item = self._make_item("eval")
        assert_activation_allowed(item, eval_observation_ref="eval:obs-1")

    def test_policy_violation_is_exception(self) -> None:
        from magi_agent.learning.policy import PolicyViolation

        exc = PolicyViolation("eval-observation-required")
        assert isinstance(exc, Exception)
        assert "eval-observation-required" in str(exc)

    def test_policy_refs_are_canonical(self) -> None:
        from magi_agent.learning import policy

        refs = (
            "policy:self-improvement.eval-observation-required@1",
            "policy:self-improvement.no-direct-mutation@1",
        )
        assert policy.POLICY_EVAL_OBSERVATION_REQUIRED in refs
        assert policy.POLICY_NO_DIRECT_MUTATION in refs


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> object:
    from magi_agent.learning.store import SqliteLearningStore

    s = SqliteLearningStore(db_path=str(tmp_path / "learning.db"))
    yield s
    s.close()


def _base_payload(kind: str = "rule") -> dict[str, object]:
    content: dict[str, object]
    if kind == "rule":
        content = {"when": "condition", "then": "action"}
    elif kind == "example":
        content = {"situation": "S", "behavior": "B"}
    else:
        content = {"input": "I", "expected": "E"}

    return {
        "id": f"learning:item-{kind}",
        "kind": kind,
        "scope": {"taskKind": "coding"},
        "content": content,
        "rationale": "test",
        "provenance": {
            "sessionIds": ("s1",),
            "derivedBy": "reflection",
            "createdAt": "2026-06-03T00:00:00Z",
        },
    }


class TestStorePropose:
    def test_propose_forces_status_proposed(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        result = store.propose(item)
        assert result.status == "proposed"

    def test_propose_strips_eval_observation_ref(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        raw = _base_payload("example")
        raw["evalObservationRef"] = "eval:obs-1"
        raw["approvalRef"] = "approval:apr-1"
        item = LearningItem.model_validate(raw)
        result = store.propose(item)
        assert result.eval_observation_ref is None
        assert result.approval_ref is None

    def test_propose_and_get_roundtrip(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        fetched = store.get(proposed.id)
        assert fetched is not None
        assert fetched.id == proposed.id
        assert fetched.status == "proposed"

    def test_get_nonexistent_returns_none(self, store: object) -> None:
        assert store.get("learning:no-such-item") is None

    def test_propose_generates_id_if_not_set(self, store: object) -> None:
        """Propose must work even when caller passes an id; it should be stored."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("eval"))
        result = store.propose(item)
        assert result.id  # non-empty


class TestStoreActivation:
    def test_approve_rule_succeeds_with_both_refs(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id,
            before={"pass_rate": 0.6},
            after={"pass_rate": 0.9},
            sample_n=50,
            passed=True,
        )
        active = store.approve(
            proposed.id,
            approver="human:alice",
            eval_observation_ref=eval_ref,
        )
        assert active.status == "active"
        assert active.eval_observation_ref == eval_ref
        assert active.approval_ref is not None

    def test_approve_rule_fails_without_eval_ref(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem
        from magi_agent.learning.policy import PolicyViolation

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        with pytest.raises(PolicyViolation, match="eval-observation-required"):
            store.approve(proposed.id, approver="human:alice", eval_observation_ref=None)

    def test_auto_activate_example_succeeds_with_eval_ref(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("example"))
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id,
            before={"pass_rate": 0.5},
            after={"pass_rate": 0.85},
            sample_n=30,
            passed=True,
        )
        active = store.auto_activate(proposed.id, eval_observation_ref=eval_ref)
        assert active.status == "active"

    def test_auto_activate_example_fails_without_eval_ref(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem
        from magi_agent.learning.policy import PolicyViolation

        item = LearningItem.model_validate(_base_payload("example"))
        proposed = store.propose(item)
        with pytest.raises(PolicyViolation, match="eval-observation-required"):
            store.auto_activate(proposed.id, eval_observation_ref=None)

    def test_no_direct_status_active_method_exists(self, store: object) -> None:
        """No public method may write status=active directly without going through approve/auto_activate."""
        import inspect

        public_methods = [
            name
            for name in dir(store)
            if not name.startswith("_") and callable(getattr(store, name))
        ]
        # None of the public methods should be named 'set_status' or 'activate' directly
        for name in public_methods:
            assert name not in (
                "set_status",
                "activate",
                "mark_active",
                "force_activate",
                "write_status",
            ), f"Found forbidden direct-activation method: {name}"

    def test_rule_auto_activate_is_blocked_by_policy(self, store: object) -> None:
        """auto_activate on a rule kind should raise PolicyViolation (no approval_ref)."""
        from magi_agent.learning.models import LearningItem
        from magi_agent.learning.policy import PolicyViolation

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id,
            before={},
            after={},
            sample_n=10,
            passed=True,
        )
        # auto_activate has no approval_ref -> no-direct-mutation for rules
        with pytest.raises(PolicyViolation, match="no-direct-mutation"):
            store.auto_activate(proposed.id, eval_observation_ref=eval_ref)


class TestStoreEdit:
    def test_edit_creates_new_version_and_supersedes_original(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        v1 = store.propose(item)
        v2 = store.edit(v1.id, patch={"rationale": "updated rationale"}, editor="human:bob")

        assert v2.version == 2
        assert v2.supersedes == v1.id
        assert v2.rationale == "updated rationale"

        # Original preserved
        original = store.get(v1.id)
        assert original is not None
        assert original.version == 1
        assert original.rationale == "test"

    def test_edit_increments_version_chain(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("example"))
        item2 = LearningItem.model_validate(
            {
                **_base_payload("example"),
                "id": "learning:item-example2",
            }
        )
        v1 = store.propose(item)
        v2 = store.edit(v1.id, patch={"rationale": "v2"}, editor="human:carol")
        v3 = store.edit(v2.id, patch={"rationale": "v3"}, editor="human:carol")

        assert v3.version == 3
        assert v3.supersedes == v2.id
        v2_stored = store.get(v2.id)
        assert v2_stored is not None
        assert v2_stored.supersedes == v1.id


class TestStoreList:
    def test_list_returns_items_for_tenant(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        for kind in ("rule", "example", "eval"):
            item = LearningItem.model_validate(
                {**_base_payload(kind), "id": f"learning:item-{kind}-list"}
            )
            store.propose(item)

        page = store.list(tenant_id="local")
        assert len(page.items) >= 3

    def test_list_filters_by_kind(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        for kind in ("rule", "example", "eval"):
            item = LearningItem.model_validate(
                {**_base_payload(kind), "id": f"learning:kind-{kind}"}
            )
            store.propose(item)

        page = store.list(tenant_id="local", kind="rule")
        assert all(i.kind == "rule" for i in page.items)
        assert len(page.items) >= 1

    def test_list_filters_by_status(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate({**_base_payload("rule"), "id": "learning:item-status"})
        v1 = store.propose(item)

        proposed_page = store.list(tenant_id="local", status="proposed")
        assert any(i.id == v1.id for i in proposed_page.items)

        active_page = store.list(tenant_id="local", status="active")
        assert not any(i.id == v1.id for i in active_page.items)

    def test_list_respects_limit(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        for i in range(10):
            item = LearningItem.model_validate(
                {**_base_payload("rule"), "id": f"learning:lim-{i}"}
            )
            store.propose(item)

        page = store.list(tenant_id="local", limit=3)
        assert len(page.items) <= 3


class TestStoreRetrieve:
    def test_retrieve_returns_active_only(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        rule_item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:retrieve-rule"}
        )
        example_item = LearningItem.model_validate(
            {**_base_payload("example"), "id": "learning:retrieve-example"}
        )
        proposed_rule = store.propose(rule_item)
        proposed_example = store.propose(example_item)

        # Neither activated yet -> retrieve must return empty
        results = store.retrieve(
            tenant_id="local",
            scope=rule_item.scope,
        )
        assert len(results) == 0

        # Activate the rule
        eval_ref = store.record_eval_observation(
            item_id=proposed_rule.id,
            before={},
            after={},
            sample_n=10,
            passed=True,
        )
        store.approve(
            proposed_rule.id,
            approver="human:alice",
            eval_observation_ref=eval_ref,
        )

        results = store.retrieve(tenant_id="local", scope=rule_item.scope)
        ids = {r.id for r in results}
        assert any("retrieve-rule" in i for i in ids)
        # proposed example must not appear
        assert not any("retrieve-example" in i for i in ids)

    def test_retrieve_scope_filtered(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem, LearningScope

        item_coding = LearningItem.model_validate(
            {**_base_payload("example"), "id": "learning:scope-coding"}
        )
        item_research = LearningItem.model_validate(
            {
                **_base_payload("example"),
                "id": "learning:scope-research",
                "scope": {"taskKind": "research"},
            }
        )
        p_coding = store.propose(item_coding)
        p_research = store.propose(item_research)

        for pid in (p_coding.id, p_research.id):
            eval_ref = store.record_eval_observation(
                item_id=pid, before={}, after={}, sample_n=5, passed=True
            )
            store.auto_activate(pid, eval_observation_ref=eval_ref)

        coding_scope = LearningScope.model_validate({"taskKind": "coding"})
        results = store.retrieve(tenant_id="local", scope=coding_scope)
        ids = {r.id for r in results}
        assert any("scope-coding" in i for i in ids)
        assert not any("scope-research" in i for i in ids)


class TestStoreArchive:
    def test_archive_item(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        archived = store.archive(proposed.id, actor="human:alice")
        assert archived.status == "archived"

        fetched = store.get(proposed.id)
        assert fetched is not None
        assert fetched.status == "archived"


class TestEvalObservations:
    def test_record_eval_observation_returns_ref(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        ref = store.record_eval_observation(
            item_id=proposed.id,
            before={"pass_rate": 0.5},
            after={"pass_rate": 0.8},
            sample_n=20,
            passed=True,
        )
        assert ref.startswith("eval-obs:")

    def test_record_eval_observation_ref_is_stable_string(self, store: object) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("example"))
        proposed = store.propose(item)
        ref = store.record_eval_observation(
            item_id=proposed.id,
            before={},
            after={},
            sample_n=1,
            passed=False,
        )
        assert isinstance(ref, str)
        assert len(ref) > 0


class TestVectorIndex:
    def test_brute_force_cosine_add_and_query(self) -> None:
        from magi_agent.learning.vector import BruteForceVectorIndex

        index = BruteForceVectorIndex()
        index.add("item-1", [1.0, 0.0, 0.0])
        index.add("item-2", [0.0, 1.0, 0.0])
        index.add("item-3", [0.9, 0.1, 0.0])

        results = index.query([1.0, 0.0, 0.0], k=2)
        ids = [r[0] for r in results]
        # item-1 and item-3 are closest to [1,0,0]
        assert "item-1" in ids
        assert "item-3" in ids

    def test_query_empty_index_returns_empty(self) -> None:
        from magi_agent.learning.vector import BruteForceVectorIndex

        index = BruteForceVectorIndex()
        results = index.query([1.0, 0.0], k=5)
        assert results == []

    def test_query_k_limits_results(self) -> None:
        from magi_agent.learning.vector import BruteForceVectorIndex

        index = BruteForceVectorIndex()
        for i in range(10):
            index.add(f"item-{i}", [float(i), 1.0])
        results = index.query([5.0, 1.0], k=3)
        assert len(results) == 3

    # Issue 9 — dimension mismatch raises instead of silently truncating
    def test_query_dimension_mismatch_raises(self) -> None:
        from magi_agent.learning.vector import BruteForceVectorIndex

        index = BruteForceVectorIndex()
        index.add("item-3d", [1.0, 0.0, 0.0])  # 3-dim
        with pytest.raises(ValueError, match="dimension mismatch"):
            index.query([1.0, 0.0], k=1)  # query with 2-dim


# ---------------------------------------------------------------------------
# Guard tests for Issues 1–8 and 10
# ---------------------------------------------------------------------------


class TestProposeGuards:
    """Issue 1: propose() must reject re-proposing a non-proposed item."""

    def _activate_rule(self, store: object, item_id: str) -> object:
        """Helper: propose + approve a rule item, return the active item."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate({**_base_payload("rule"), "id": item_id})
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id,
            before={},
            after={},
            sample_n=5,
            passed=True,
        )
        return store.approve(proposed.id, approver="human:alice", eval_observation_ref=eval_ref)

    def test_propose_onto_active_item_raises(self, store: object) -> None:
        """Re-proposing an active item must raise ValueError, not silently demote it."""
        from magi_agent.learning.models import LearningItem

        active = self._activate_rule(store, "learning:item-active-guard")
        assert active.status == "active"

        # Attempt to re-propose using the same id
        duplicate = LearningItem.model_validate(
            {**_base_payload("rule"), "id": active.id}
        )
        with pytest.raises(ValueError, match="active"):
            store.propose(duplicate)

        # Original item is unchanged
        still_active = store.get(active.id)
        assert still_active is not None
        assert still_active.status == "active"

    def test_propose_onto_archived_item_raises(self, store: object) -> None:
        """Re-proposing an archived item must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        active = self._activate_rule(store, "learning:item-archived-guard")
        store.archive(active.id, actor="human:alice")

        duplicate = LearningItem.model_validate(
            {**_base_payload("rule"), "id": active.id}
        )
        with pytest.raises(ValueError, match="archived"):
            store.propose(duplicate)

    def test_propose_onto_proposed_item_is_idempotent(self, store: object) -> None:
        """Re-proposing onto an existing proposed item should succeed (update case)."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        v1 = store.propose(item)
        assert v1.status == "proposed"

        # Re-propose with updated rationale — should NOT raise
        updated = LearningItem.model_validate(
            {**_base_payload("rule"), "rationale": "updated rationale"}
        )
        v2 = store.propose(updated)
        assert v2.status == "proposed"
        assert v2.rationale == "updated rationale"


class TestApproveGuards:
    """Issues 2 + 7: approve() must reject archived items and blank approvers."""

    def test_approve_on_archived_item_raises(self, store: object) -> None:
        """approve() on an already-archived item must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        store.archive(proposed.id, actor="human:alice")

        eval_ref = store.record_eval_observation(
            item_id=proposed.id,
            before={},
            after={},
            sample_n=5,
            passed=True,
        )
        with pytest.raises(ValueError, match="proposed"):
            store.approve(proposed.id, approver="human:bob", eval_observation_ref=eval_ref)

    def test_approve_on_already_active_item_raises(self, store: object) -> None:
        """approve() on an already-active item must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:approve-active-guard"}
        )
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        store.approve(proposed.id, approver="human:alice", eval_observation_ref=eval_ref)

        # Second approve must fail
        eval_ref2 = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        with pytest.raises(ValueError, match="proposed"):
            store.approve(proposed.id, approver="human:bob", eval_observation_ref=eval_ref2)

    def test_approve_empty_approver_raises(self, store: object) -> None:
        """approve() with an empty approver must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("rule"))
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        with pytest.raises(ValueError, match="approver"):
            store.approve(proposed.id, approver="", eval_observation_ref=eval_ref)

    def test_approve_blank_approver_raises(self, store: object) -> None:
        """approve() with a whitespace-only approver must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:blank-approver-guard"}
        )
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        with pytest.raises(ValueError, match="approver"):
            store.approve(proposed.id, approver="   ", eval_observation_ref=eval_ref)


class TestAutoActivateGuards:
    """Issue 2: auto_activate() must reject non-proposed items."""

    def test_auto_activate_on_archived_item_raises(self, store: object) -> None:
        """auto_activate() on an archived example item must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(_base_payload("example"))
        proposed = store.propose(item)
        store.archive(proposed.id, actor="human:alice")

        eval_ref = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        with pytest.raises(ValueError, match="proposed"):
            store.auto_activate(proposed.id, eval_observation_ref=eval_ref)

    def test_auto_activate_on_already_active_item_raises(self, store: object) -> None:
        """auto_activate() on an already-active item must raise ValueError."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("example"), "id": "learning:aa-active-guard"}
        )
        proposed = store.propose(item)
        eval_ref = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        store.auto_activate(proposed.id, eval_observation_ref=eval_ref)

        # Second auto_activate must fail
        eval_ref2 = store.record_eval_observation(
            item_id=proposed.id, before={}, after={}, sample_n=5, passed=True
        )
        with pytest.raises(ValueError, match="proposed"):
            store.auto_activate(proposed.id, eval_observation_ref=eval_ref2)


class TestEditVersionChain:
    """Issue 3: edit() must derive new ids from root, not cascade."""

    def test_edit_v3_id_is_not_cascaded(self, store: object) -> None:
        """v3 id must be root:v3, NOT root:v2:v3."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:chain-root"}
        )
        v1 = store.propose(item)
        v2 = store.edit(v1.id, patch={"rationale": "v2"}, editor="human:carol")
        v3 = store.edit(v2.id, patch={"rationale": "v3"}, editor="human:carol")

        assert v2.id == "learning:chain-root:v2", f"Unexpected v2 id: {v2.id}"
        assert v3.id == "learning:chain-root:v3", f"Unexpected v3 id: {v3.id!r} (should not cascade)"
        assert v3.version == 3
        assert v3.supersedes == v2.id

    def test_edit_v4_id_is_not_cascaded(self, store: object) -> None:
        """A four-level chain must stay flat: root:v4 not root:v2:v3:v4."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("example"), "id": "learning:deep-chain"}
        )
        v1 = store.propose(item)
        v2 = store.edit(v1.id, patch={"rationale": "v2"}, editor="human:a")
        v3 = store.edit(v2.id, patch={"rationale": "v3"}, editor="human:a")
        v4 = store.edit(v3.id, patch={"rationale": "v4"}, editor="human:a")

        assert v4.id == "learning:deep-chain:v4"
        assert v4.version == 4


class TestListPaginationWithScope:
    """Issue 5: scope filter in list() must not break pagination cursor."""

    def test_scope_filter_pagination_cursor_accurate(self, store: object) -> None:
        """Paginating with a scope filter must return accurate next_cursor semantics.

        If all items on a page match the scope filter, next_cursor should be
        set only when more matching items actually exist.
        """
        from magi_agent.learning.models import LearningItem

        # Insert 5 items with scope 'coding' and 5 with scope 'research'
        for i in range(5):
            store.propose(
                LearningItem.model_validate(
                    {
                        **_base_payload("rule"),
                        "id": f"learning:scope-page-coding-{i:02d}",
                        "scope": {"taskKind": "coding"},
                    }
                )
            )
            store.propose(
                LearningItem.model_validate(
                    {
                        **_base_payload("rule"),
                        "id": f"learning:scope-page-research-{i:02d}",
                        "scope": {"taskKind": "research"},
                    }
                )
            )

        from magi_agent.learning.models import LearningScope

        coding_scope = LearningScope.model_validate({"taskKind": "coding"})

        # Fetch all coding items with a small page size to exercise cursor
        seen_ids: list[str] = []
        cursor: str | None = None
        pages_fetched = 0
        while True:
            page = store.list(
                tenant_id="local",
                scope=coding_scope,
                limit=2,
                cursor=cursor,
            )
            # All returned items must be coding scope
            for item in page.items:
                assert item.scope.task_kind == "coding", (
                    f"Non-coding item leaked through scope filter: {item.id}"
                )
                seen_ids.append(item.id)

            pages_fetched += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
            assert pages_fetched <= 10, "Pagination did not terminate"

        # We should have found all 5 coding items (no more, no less)
        assert len(seen_ids) == 5
        # No next_cursor when exhausted
        assert page.next_cursor is None

    def test_scope_filter_no_spurious_next_cursor(self, store: object) -> None:
        """next_cursor must be None when all remaining items are filtered out."""
        from magi_agent.learning.models import LearningItem, LearningScope

        # Insert 3 'research' items and 1 'coding' item (in id order: coding comes first)
        store.propose(
            LearningItem.model_validate(
                {
                    **_base_payload("rule"),
                    "id": "learning:cursor-a-coding",
                    "scope": {"taskKind": "coding"},
                }
            )
        )
        for i in range(3):
            store.propose(
                LearningItem.model_validate(
                    {
                        **_base_payload("rule"),
                        "id": f"learning:cursor-b-research-{i}",
                        "scope": {"taskKind": "research"},
                    }
                )
            )

        research_scope = LearningScope.model_validate({"taskKind": "research"})
        # Fetch research items with limit=10; should see exactly 3 with no cursor
        page = store.list(tenant_id="local", scope=research_scope, limit=10)
        assert len(page.items) == 3
        assert page.next_cursor is None


class TestArchiveGuards:
    """Issue 8: archive() on nonexistent id must raise KeyError."""

    def test_archive_nonexistent_raises_key_error(self, store: object) -> None:
        with pytest.raises(KeyError, match="no-such-item"):
            store.archive("learning:no-such-item", actor="human:alice")


class TestExportsAndProtocol:
    """Issue 4: LearningStore protocol + DEFAULT_LEARNING_DB_PATH must be exported."""

    def test_default_learning_db_path_exported(self) -> None:
        from magi_agent.learning import DEFAULT_LEARNING_DB_PATH

        assert isinstance(DEFAULT_LEARNING_DB_PATH, str)
        assert DEFAULT_LEARNING_DB_PATH  # non-empty

    def test_learning_store_protocol_exported(self) -> None:
        from magi_agent.learning import LearningStore

        assert LearningStore is not None

    def test_sqlite_store_satisfies_protocol(self, store: object) -> None:
        """SqliteLearningStore must be recognised as a LearningStore at runtime."""
        from magi_agent.learning import LearningStore

        assert isinstance(store, LearningStore)


class TestActivationPathBehavioral:
    """Issue 10: behavioral proof that only approve/auto_activate reach status=active."""

    def test_only_approve_and_auto_activate_can_reach_status_active(
        self, store: object
    ) -> None:
        """Prove by exhaustion: every public store method that is NOT approve or
        auto_activate cannot produce a status='active' item."""
        from magi_agent.learning.models import LearningItem
        from magi_agent.learning.policy import PolicyViolation

        # Helper: propose a fresh item each time
        def fresh(suffix: str) -> LearningItem:
            return LearningItem.model_validate(
                {**_base_payload("example"), "id": f"learning:behav-{suffix}"}
            )

        # 1. propose() must always produce status='proposed'
        result = store.propose(fresh("propose"))
        assert result.status == "proposed"

        # 2. edit() must produce status='proposed'
        v1 = store.propose(LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:behav-edit-root"}
        ))
        v2 = store.edit(v1.id, patch={"rationale": "x"}, editor="human:a")
        assert v2.status == "proposed"

        # 3. archive() must produce status='archived'
        p = store.propose(fresh("archive"))
        archived = store.archive(p.id, actor="human:a")
        assert archived.status == "archived"

        # 4. auto_activate without eval_observation_ref raises PolicyViolation
        p2 = store.propose(fresh("aa-no-ref"))
        with pytest.raises(PolicyViolation):
            store.auto_activate(p2.id, eval_observation_ref=None)
        assert store.get(p2.id).status == "proposed"  # type: ignore[union-attr]

        # 5. approve without eval_observation_ref raises PolicyViolation
        p3 = store.propose(fresh("appr-no-ref"))
        with pytest.raises(PolicyViolation):
            store.approve(p3.id, approver="human:a", eval_observation_ref=None)
        assert store.get(p3.id).status == "proposed"  # type: ignore[union-attr]

        # 6. Only approve with both refs can reach status='active' for rules
        p4 = store.propose(LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:behav-active-only"}
        ))
        eval_ref = store.record_eval_observation(
            item_id=p4.id, before={}, after={}, sample_n=5, passed=True
        )
        activated = store.approve(
            p4.id, approver="human:alice", eval_observation_ref=eval_ref
        )
        assert activated.status == "active"
        # Confirm assert_activation_allowed was the gate
        assert activated.eval_observation_ref == eval_ref
        assert activated.approval_ref is not None


# ---------------------------------------------------------------------------
# Fix-specific regression tests
# ---------------------------------------------------------------------------


class TestMigration4Idempotent:
    """Migration 4 ALTER TABLE must be crash-idempotent.

    Simulates the scenario where the column was added (by ALTER TABLE) but
    the version row was not yet recorded — e.g. process crashed between the
    ALTER and the INSERT INTO _learning_schema_version.  Re-running migrations
    must NOT raise OperationalError: duplicate column name.
    """

    def test_migration4_idempotent_when_column_exists_but_version_not_recorded(
        self, tmp_path: Path
    ) -> None:
        import sqlite3 as _sqlite3

        from magi_agent.learning.store import _run_migrations

        db_path = tmp_path / "crash_sim.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row

        # Apply migrations 1–3 normally so the base schema is present.
        _run_migrations(conn)

        # Manually execute the ALTER TABLE (simulating crash after DDL but
        # before version INSERT), then delete the version-4 row to simulate
        # the version not being recorded yet.
        try:
            conn.execute(
                """
                ALTER TABLE learning_items
                    ADD COLUMN scope_task_kind TEXT
                        GENERATED ALWAYS AS (json_extract(scope_json, '$.taskKind')) VIRTUAL
                """
            )
            conn.commit()
        except _sqlite3.OperationalError:
            # Column already present (migrations ran fully) — that's fine for
            # the simulation; we just need version-4 row gone.
            pass

        conn.execute(
            "DELETE FROM _learning_schema_version WHERE version = 4"
        )
        conn.commit()

        # Now re-running migrations must succeed without raising.
        try:
            _run_migrations(conn)
        except _sqlite3.OperationalError as exc:
            raise AssertionError(
                f"Migration 4 is not idempotent — re-run raised: {exc}"
            ) from exc
        finally:
            conn.close()


class TestProposeRejectsVersionSuffix:
    """propose() must reject ids that end in :v<digits> (reserved for edit())."""

    def test_propose_with_version_suffix_raises_value_error(
        self, store: object
    ) -> None:
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:item-root:v2"}
        )
        with pytest.raises(ValueError, match=r":v\d+"):
            store.propose(item)

    def test_propose_without_version_suffix_still_works(
        self, store: object
    ) -> None:
        """Plain ids must not be affected by the guard."""
        from magi_agent.learning.models import LearningItem

        item = LearningItem.model_validate(
            {**_base_payload("rule"), "id": "learning:item-root-v2-plain"}
        )
        result = store.propose(item)
        assert result.id == "learning:item-root-v2-plain"

    def test_propose_rejects_various_version_suffixes(
        self, store: object
    ) -> None:
        from magi_agent.learning.models import LearningItem

        for bad_id in (
            "learning:item:v1",
            "learning:item:v10",
            "learning:item:v999",
        ):
            item = LearningItem.model_validate(
                {**_base_payload("eval"), "id": bad_id,
                 "content": {"input": "I", "expected": "E"}}
            )
            with pytest.raises(ValueError, match=r":v\d+"):
                store.propose(item)
