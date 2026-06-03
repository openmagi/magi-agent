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
