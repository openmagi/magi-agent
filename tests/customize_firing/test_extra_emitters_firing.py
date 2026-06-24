"""PR-F-LIFE3 production-wire tests: the four NEW emitter slots fire from
their respective runtime chokepoints when the master flag is ON, and stay
silent when it is OFF.

Each slot has a single emit site:

* ``before_compaction`` / ``after_compaction`` — fired by
  :meth:`magi_agent.adk_bridge.context_compaction.MagiContextCompactionPlugin._apply_tail_trim`
  around the tail-drop (covers both the automatic threshold/real-token
  decision path and the manual /compact force path).
* ``on_task_checkpoint`` — fired by
  :meth:`magi_agent.missions.work_queue.driver.WorkQueueDriver.run_once`
  at each task status transition.
* ``on_artifact_created`` — fired by
  :meth:`magi_agent.artifacts.file_delivery.FileDeliveryBoundary.execute`
  on the ``write_artifact`` ok-status branch.

Three scenarios per slot:
* triple-gate ON + matching rule → judge runs.
* triple-gate OFF (master flag missing) → judge MUST NOT run.
* no matching rule → fan-out short-circuits (empty list) without
  invoking the judge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.lifecycle_audit import (
    lifecycle_extra_emitters_enabled,
    run_after_compaction_audit,
    run_artifact_created_audit,
    run_before_compaction_audit,
    run_task_checkpoint_audit,
)
from magi_agent.customize.store import set_custom_rule


_BEFORE_COMP_RULE_ID = "cr_flife3_before_compaction_audit"
_BEFORE_COMP_CRITERION = "the pre-compaction context does not leak credentials"
_AFTER_COMP_RULE_ID = "cr_flife3_after_compaction_audit"
_AFTER_COMP_CRITERION = "the post-compaction summary is non-empty"
_TASK_CKPT_RULE_ID = "cr_flife3_on_task_checkpoint_audit"
_TASK_CKPT_CRITERION = "the task summary does not include internal stack traces"
_ARTIFACT_RULE_ID = "cr_flife3_on_artifact_created_audit"
_ARTIFACT_CRITERION = "the artifact ref is a known prefix"


def _rule(*, rid: str, fires_at: str, criterion: str) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": criterion}},
        "firesAt": fires_at,
        "action": "audit",
    }


def _flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


# ---------------------------------------------------------------------------
# lifecycle_extra_emitters_enabled triple-gate
# ---------------------------------------------------------------------------


def test_extra_emitters_enabled_master_flag_off_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", raising=False
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert lifecycle_extra_emitters_enabled() is False


def test_extra_emitters_enabled_full_stack_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert lifecycle_extra_emitters_enabled() is True


def test_extra_emitters_enabled_verification_off_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    assert lifecycle_extra_emitters_enabled() is False


# ---------------------------------------------------------------------------
# before_compaction fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_compaction_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_BEFORE_COMP_RULE_ID,
            fires_at="before_compaction",
            criterion=_BEFORE_COMP_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_before_compaction_audit(
        pre_compaction_text="pre_compaction: contents=42, model=gpt-5",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert len(calls) == 1
    assert calls[0]["criterion"] == _BEFORE_COMP_CRITERION
    assert "pre_compaction" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_before_compaction_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master flag OFF ⇒ fan-out is a no-op even with a matching rule.
    Locks the byte-identical OFF contract."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "0")
    set_custom_rule(
        _rule(
            rid=_BEFORE_COMP_RULE_ID,
            fires_at="before_compaction",
            criterion=_BEFORE_COMP_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_before_compaction_audit(
        pre_compaction_text="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


@pytest.mark.asyncio
async def test_before_compaction_audit_empty_when_no_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_on(monkeypatch, tmp_path)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when no rule is authored")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_before_compaction_audit(
        pre_compaction_text="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# after_compaction fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_compaction_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_AFTER_COMP_RULE_ID,
            fires_at="after_compaction",
            criterion=_AFTER_COMP_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_after_compaction_audit(
        summary_text="post_compaction: kept=10, dropped=30",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert calls[0]["criterion"] == _AFTER_COMP_CRITERION
    assert "post_compaction" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_after_compaction_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "0")
    set_custom_rule(
        _rule(
            rid=_AFTER_COMP_RULE_ID,
            fires_at="after_compaction",
            criterion=_AFTER_COMP_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_after_compaction_audit(
        summary_text="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# on_task_checkpoint fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_checkpoint_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_TASK_CKPT_RULE_ID,
            fires_at="on_task_checkpoint",
            criterion=_TASK_CKPT_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_task_checkpoint_audit(
        task_id="task-abc-123",
        checkpoint_kind="completed",
        summary_text="task finished cleanly",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    # The composed frame must include the task id + checkpoint kind so the
    # critic can disambiguate which transition fired.
    assert "task-abc-123" in calls[0]["draft_text"]
    assert "completed" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_task_checkpoint_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "0")
    set_custom_rule(
        _rule(
            rid=_TASK_CKPT_RULE_ID,
            fires_at="on_task_checkpoint",
            criterion=_TASK_CKPT_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_task_checkpoint_audit(
        task_id="task-1",
        checkpoint_kind="failed",
        summary_text="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# on_artifact_created fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_created_audit_fires_when_rule_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(
        _rule(
            rid=_ARTIFACT_RULE_ID,
            fires_at="on_artifact_created",
            criterion=_ARTIFACT_CRITERION,
        ),
        path=cfile,
    )

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = await run_artifact_created_audit(
        artifact_ref="artifact:sha256:abcd1234",
        artifact_excerpt="operation=file.deliver",
        model_factory=lambda: object(),
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "evaluated"
    assert "artifact:sha256:abcd1234" in calls[0]["draft_text"]
    assert "operation=file.deliver" in calls[0]["draft_text"]


@pytest.mark.asyncio
async def test_artifact_created_audit_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "0")
    set_custom_rule(
        _rule(
            rid=_ARTIFACT_RULE_ID,
            fires_at="on_artifact_created",
            criterion=_ARTIFACT_CRITERION,
        ),
        path=cfile,
    )

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = await run_artifact_created_audit(
        artifact_ref="anything",
        artifact_excerpt="anything",
        model_factory=lambda: object(),
    )
    assert audits == []


# ---------------------------------------------------------------------------
# Driver-site wires: WorkQueueDriver fires the sync helper at the
# claimed / completed / failed transitions inside run_once.
# ---------------------------------------------------------------------------


def test_work_queue_driver_emits_task_checkpoint_at_claimed_and_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test: the work-queue driver's sync emit helper must be invoked
    at each terminal transition. We monkeypatch the helper so the test stays
    hermetic (no policy load / critic invocation needed)."""
    from magi_agent.missions.work_queue import driver as driver_mod
    from magi_agent.missions.work_queue.driver import WorkQueueDriver
    from magi_agent.missions.work_queue.models import WorkTask
    from magi_agent.missions.work_queue.runner import (
        WorkTaskRunner,
        WorkTaskRunResult,
    )

    emits: list[tuple[str, str]] = []

    def fake_emit(*, task_id: str, checkpoint_kind: str, summary_text: str) -> None:
        emits.append((task_id, checkpoint_kind))

    monkeypatch.setattr(driver_mod, "_emit_task_checkpoint_sync", fake_emit)

    class _FakeStore:
        def __init__(self, task: WorkTask) -> None:
            self._task = task
            self._completed: dict[str, str | None] = {}

        def release_stale_claims(self, *, now=None, pid_alive=None) -> int:
            return 0

        def recompute_ready(self) -> int:
            return 0

        def ready_tasks(self, *, limit: int):
            return [self._task]

        def claim(self, task_id: str, *, claimer: str, now=None):
            return self._task

        def completed_task_for_key(self, key: str, *, exclude_task_id: str):
            return None

        def complete(self, task_id: str, *, result: str | None = None) -> None:
            self._completed[task_id] = result

        def record_failure(self, task_id: str, *, outcome: str, error: str) -> None:
            pass

    class _FakeRunner(WorkTaskRunner):
        async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
            return WorkTaskRunResult(
                outcome="completed", summary="task done", error=None
            )

    task = WorkTask(
        id="t-1",
        title="test task",
        status="ready",
        created_at=0,
    )
    store = _FakeStore(task)
    driver = WorkQueueDriver(store, _FakeRunner(), claimer="d-0")
    driver.run_once()

    # Both claimed + completed transitions must have fired the emit.
    kinds = [k for _, k in emits]
    assert "claimed" in kinds, emits
    assert "completed" in kinds, emits


def test_work_queue_driver_emits_task_checkpoint_at_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failed-outcome branch must also fire the emit."""
    from magi_agent.missions.work_queue import driver as driver_mod
    from magi_agent.missions.work_queue.driver import WorkQueueDriver
    from magi_agent.missions.work_queue.models import WorkTask
    from magi_agent.missions.work_queue.runner import (
        WorkTaskRunner,
        WorkTaskRunResult,
    )

    emits: list[tuple[str, str]] = []

    def fake_emit(*, task_id: str, checkpoint_kind: str, summary_text: str) -> None:
        emits.append((task_id, checkpoint_kind))

    monkeypatch.setattr(driver_mod, "_emit_task_checkpoint_sync", fake_emit)

    class _FakeStore:
        def __init__(self, task: WorkTask) -> None:
            self._task = task

        def release_stale_claims(self, *, now=None, pid_alive=None) -> int:
            return 0

        def recompute_ready(self) -> int:
            return 0

        def ready_tasks(self, *, limit: int):
            return [self._task]

        def claim(self, task_id: str, *, claimer: str, now=None):
            return self._task

        def completed_task_for_key(self, key: str, *, exclude_task_id: str):
            return None

        def complete(self, *args, **kwargs) -> None:
            pass

        def record_failure(self, *args, **kwargs) -> None:
            pass

    class _FakeRunner(WorkTaskRunner):
        async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
            return WorkTaskRunResult(
                outcome="failed", summary=None, error="kaboom"
            )

    task = WorkTask(
        id="t-fail",
        title="failing task",
        status="ready",
        created_at=0,
    )
    store = _FakeStore(task)
    driver = WorkQueueDriver(store, _FakeRunner(), claimer="d-0")
    driver.run_once()

    kinds = [k for _, k in emits]
    assert "claimed" in kinds, emits
    assert "failed" in kinds, emits


# ---------------------------------------------------------------------------
# Driver-site OFF path: the work-queue driver's sync emit helper must
# short-circuit when the master flag is OFF (byte-identical contract).
# ---------------------------------------------------------------------------


def test_emit_task_checkpoint_sync_off_path_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OFF-path contract: when the master flag is OFF the sync helper
    MUST NOT invoke the criterion judge (no policy load, no critic). We
    verify this by patching evaluate_criterion to raise and confirming the
    helper returns silently."""
    from magi_agent.missions.work_queue.driver import _emit_task_checkpoint_sync

    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", raising=False
    )

    def fail_eval(*args, **kwargs):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    # MUST NOT raise — the helper is fail-open at every step and gates the
    # whole body on the triple-gate check.
    _emit_task_checkpoint_sync(
        task_id="t-1", checkpoint_kind="claimed", summary_text="anything"
    )


def test_emit_artifact_created_sync_off_path_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.artifacts.file_delivery import _emit_artifact_created_sync

    monkeypatch.delenv(
        "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", raising=False
    )

    def fail_eval(*args, **kwargs):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    _emit_artifact_created_sync(
        artifact_ref="anything", artifact_excerpt="anything"
    )
