from __future__ import annotations

import subprocess
import sys

from openmagi_core_agent.harness.mission_runtime_boundary import (
    BackgroundTaskRecord,
    GoalBudget,
    GoalRecord,
    MissionRuntimeBoundary,
    MissionRuntimeConfig,
    MissionRuntimeRequest,
)


class FakeMissionStore:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.goals: dict[str, GoalRecord] = {}
        self.tasks: dict[str, BackgroundTaskRecord] = {}
        self.goal_saves: list[GoalRecord] = []

    def save_goal(self, goal: GoalRecord) -> GoalRecord:
        self.goals[goal.goal_id] = goal
        self.goal_saves.append(goal)
        return goal

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        return self.goals.get(goal_id)

    def save_task(self, task: BackgroundTaskRecord) -> BackgroundTaskRecord:
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> BackgroundTaskRecord | None:
        return self.tasks.get(task_id)

    def list_tasks(self) -> tuple[BackgroundTaskRecord, ...]:
        return tuple(self.tasks.values())


class FakeMissionScheduler:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.ticks: list[MissionRuntimeRequest] = []

    def record_tick(self, request: MissionRuntimeRequest, goal: GoalRecord | None) -> dict[str, object]:
        self.ticks.append(request)
        return {
            "goalId": None if goal is None else goal.goal_id,
            "operatorState": "idle",
            "stopCondition": "no_background_tick_started",
        }


class ThrowingMissionStore(FakeMissionStore):
    def save_goal(self, goal: GoalRecord) -> GoalRecord:
        raise RuntimeError("store failed /Users/kevin/private ghp_missionSecret")

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        raise RuntimeError("get failed /workspace/private 123456:ABC-secret-token")

    def save_task(self, task: BackgroundTaskRecord) -> BackgroundTaskRecord:
        raise RuntimeError("task failed /data/bots/private sk-mission-secret")

    def get_task(self, task_id: str) -> BackgroundTaskRecord | None:
        raise RuntimeError("task get failed /workspace/private")

    def list_tasks(self) -> tuple[BackgroundTaskRecord, ...]:
        raise RuntimeError("task list failed /Users/kevin/private")


class ThrowingMissionScheduler(FakeMissionScheduler):
    def record_tick(self, request: MissionRuntimeRequest, goal: GoalRecord | None) -> dict[str, object]:
        raise RuntimeError("scheduler failed /workspace/private ghp_schedulerSecret")


def test_mission_runtime_boundary_is_disabled_by_default() -> None:
    store = FakeMissionStore()
    decision = MissionRuntimeBoundary(MissionRuntimeConfig()).execute(
        MissionRuntimeRequest(operation="goal.create", objective="Ship report"),
        state_store=store,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mission_runtime_disabled",)
    assert store.goals == {}
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_goal_create_progress_and_completion_audit_use_local_fake_store_only() -> None:
    store = FakeMissionStore()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    )

    created = boundary.execute(
        MissionRuntimeRequest(
            operation="goal.create",
            goalId="goal-1",
            objective="Ship the report",
            budget=GoalBudget(maxTurns=3, turnsUsed=0),
            now=100,
        ),
        state_store=store,
    )
    assert created.status == "goal_recorded_local_fake"
    assert created.goal is not None
    assert created.goal.status == "running"
    assert created.goal.budget.max_turns == 3
    assert created.goal.created_at == 100

    progressed = boundary.execute(
        MissionRuntimeRequest(
            operation="goal.progress",
            goalId="goal-1",
            progressNote="drafted outline",
            now=110,
        ),
        state_store=store,
    )
    assert progressed.goal is not None
    assert progressed.goal.budget.turns_used == 1
    assert progressed.goal.progress.current_step == "drafted outline"
    assert progressed.goal.updated_at == 110

    completed = boundary.execute(
        MissionRuntimeRequest(
            operation="goal.complete",
            goalId="goal-1",
            completionSummary="report delivered",
            evidenceRefs=("evidence:test-pass",),
            now=120,
        ),
        state_store=store,
    )
    assert completed.goal is not None
    assert completed.goal.status == "completed"
    assert completed.goal.completion_audit is not None
    assert completed.goal.completion_audit.evidence_refs == ("evidence:test-pass",)
    assert completed.public_projection()["goal"]["completionAudit"]["summary"] == (
        "report delivered"
    )


def test_goal_pause_resume_cancel_and_budget_exhaustion() -> None:
    store = FakeMissionStore()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    )
    boundary.execute(
        MissionRuntimeRequest(
            operation="goal.create",
            goalId="goal-budget",
            objective="Finish",
            budget=GoalBudget(maxTurns=1, turnsUsed=0),
        ),
        state_store=store,
    )

    paused = boundary.execute(
        MissionRuntimeRequest(operation="goal.pause", goalId="goal-budget"),
        state_store=store,
    )
    assert paused.goal is not None
    assert paused.goal.status == "paused"

    resumed = boundary.execute(
        MissionRuntimeRequest(operation="goal.resume", goalId="goal-budget"),
        state_store=store,
    )
    assert resumed.goal is not None
    assert resumed.goal.status == "running"

    exhausted = boundary.execute(
        MissionRuntimeRequest(operation="goal.progress", goalId="goal-budget"),
        state_store=store,
    )
    assert exhausted.goal is not None
    assert exhausted.goal.status == "blocked"
    assert exhausted.reason_codes == ("goal_budget_exhausted",)

    cancelled = boundary.execute(
        MissionRuntimeRequest(operation="goal.cancel", goalId="goal-budget"),
        state_store=store,
    )
    assert cancelled.goal is not None
    assert cancelled.goal.status == "cancelled"


def test_scheduler_tick_records_metadata_without_background_execution() -> None:
    store = FakeMissionStore()
    scheduler = FakeMissionScheduler()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        ),
    )
    boundary.execute(
        MissionRuntimeRequest(
            operation="goal.create",
            goalId="goal-1",
            objective="Ship report",
        ),
        state_store=store,
    )

    tick = boundary.execute(
        MissionRuntimeRequest(operation="scheduler.tick", goalId="goal-1"),
        state_store=store,
        scheduler=scheduler,
    )

    assert tick.status == "scheduler_tick_recorded_local_fake"
    assert scheduler.ticks[0].operation == "scheduler.tick"
    projection = tick.public_projection()
    assert projection["schedulerMetadata"]["stopCondition"] == "no_background_tick_started"
    assert projection["authorityFlags"]["backgroundSchedulerAttached"] is False


def test_mission_scheduler_decision_emits_child_intent_only_without_execution() -> None:
    from openmagi_core_agent.harness.mission_runtime_boundary import (
        build_mission_scheduler_decision,
    )

    goal = GoalRecord(
        goalId="goal-impl",
        objective="Implement bounded feature",
        status="running",
        budget=GoalBudget(maxTurns=5, turnsUsed=1),
    )

    decision = build_mission_scheduler_decision(goal=goal, now=200)

    assert decision.status == "planned"
    assert decision.stop_condition == "child_intent_planned"
    assert decision.next_tick_after == 200
    assert decision.child_task_intents[0].role == "implementer"
    assert decision.child_task_intents[0].goal_ref == "goal-impl"
    assert decision.child_task_intents[0].execution_allowed is False
    assert decision.authority_flags.real_child_runner_invoked is False
    assert decision.authority_flags.background_task_started is False
    assert "raw" not in str(decision.public_projection()).lower()


def test_mission_scheduler_decision_suppresses_paused_completed_and_exhausted_goals() -> None:
    from openmagi_core_agent.harness.mission_runtime_boundary import (
        build_mission_scheduler_decision,
    )

    paused = GoalRecord(goalId="goal-paused", objective="Pause", status="paused")
    completed = GoalRecord(goalId="goal-done", objective="Done", status="completed")
    exhausted = GoalRecord(
        goalId="goal-budget",
        objective="Budget",
        status="running",
        budget=GoalBudget(maxTurns=1, turnsUsed=1),
    )

    paused_decision = build_mission_scheduler_decision(goal=paused, now=10)
    completed_decision = build_mission_scheduler_decision(goal=completed, now=10)
    exhausted_decision = build_mission_scheduler_decision(goal=exhausted, now=10)

    assert paused_decision.child_task_intents == ()
    assert paused_decision.stop_condition == "goal_paused"
    assert completed_decision.stop_condition == "goal_terminal"
    assert exhausted_decision.stop_condition == "budget_exhausted"
    assert all(
        decision.authority_flags.real_child_runner_invoked is False
        for decision in (paused_decision, completed_decision, exhausted_decision)
    )


def test_mission_runtime_rejects_unmarked_local_fake_store_and_scheduler() -> None:
    class UnmarkedMissionStore(FakeMissionStore):
        openmagi_local_fake_provider = False

    class UnmarkedMissionScheduler(FakeMissionScheduler):
        openmagi_local_fake_provider = False

    store = UnmarkedMissionStore()
    scheduler = UnmarkedMissionScheduler()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        ),
    )

    goal = boundary.execute(
        MissionRuntimeRequest(operation="goal.create", objective="Ship report"),
        state_store=store,
    )
    tick = boundary.execute(
        MissionRuntimeRequest(operation="scheduler.tick"),
        scheduler=scheduler,
    )

    assert goal.status == "blocked"
    assert goal.reason_codes == ("local_fake_state_store_untrusted",)
    assert tick.status == "blocked"
    assert tick.reason_codes == ("local_fake_scheduler_untrusted",)
    assert store.goals == {}
    assert scheduler.ticks == []


def test_mission_runtime_catches_fake_store_and_scheduler_errors() -> None:
    store = ThrowingMissionStore()
    scheduler = ThrowingMissionScheduler()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        ),
    )

    goal_create = boundary.execute(
        MissionRuntimeRequest(operation="goal.create", objective="Ship report"),
        state_store=store,
    )
    task_create = boundary.execute(
        MissionRuntimeRequest(operation="task.create", objective="review docs"),
        state_store=store,
    )
    scheduler_tick = boundary.execute(
        MissionRuntimeRequest(operation="scheduler.tick"),
        scheduler=scheduler,
    )

    assert goal_create.status == "blocked"
    assert goal_create.reason_codes == ("local_fake_state_store_error",)
    assert task_create.status == "blocked"
    assert task_create.reason_codes == ("local_fake_state_store_error",)
    assert scheduler_tick.status == "blocked"
    assert scheduler_tick.reason_codes == ("local_fake_scheduler_error",)
    encoded = (
        str(goal_create.public_projection())
        + str(task_create.public_projection())
        + str(scheduler_tick.public_projection())
    )
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "/data/bots" not in encoded
    assert "ghp_missionSecret" not in encoded
    assert "ghp_schedulerSecret" not in encoded
    assert "123456:ABC-secret-token" not in encoded


def test_background_task_lifecycle_is_metadata_only() -> None:
    store = FakeMissionStore()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    )

    created = boundary.execute(
        MissionRuntimeRequest(
            operation="task.create",
            taskId="task-1",
            objective="review docs",
            parentTurnId="turn-1",
            now=100,
        ),
        state_store=store,
    )
    assert created.task is not None
    assert created.task.status == "running"

    stopped = boundary.execute(
        MissionRuntimeRequest(operation="task.stop", taskId="task-1", now=110),
        state_store=store,
    )
    assert stopped.task is not None
    assert stopped.task.status == "aborted"
    assert stopped.public_projection()["task"]["promptPreview"] == "review docs"


def test_background_task_prompt_preview_redacts_provider_tokens() -> None:
    store = FakeMissionStore()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    )

    created = boundary.execute(
        MissionRuntimeRequest(
            operation="task.create",
            taskId="task-token",
            objective=(
                "review docs github_pat_unsafeToken12345 "
                "xoxb-unsafeToken12345 AKIAUNSAFEKEY12345 AIzaUnsafeGoogleToken12345"
            ),
        ),
        state_store=store,
    )
    encoded = str(created.public_projection())

    assert "review docs" in encoded
    for forbidden in (
        "github_pat_unsafe",
        "xoxb-unsafe",
        "AKIAUNSAFE",
        "AIzaUnsafe",
    ):
        assert forbidden not in encoded


def test_background_task_get_list_and_wait_are_metadata_only() -> None:
    store = FakeMissionStore()
    boundary = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    )
    boundary.execute(
        MissionRuntimeRequest(
            operation="task.create",
            taskId="task-a",
            objective="first",
            now=100,
        ),
        state_store=store,
    )
    boundary.execute(
        MissionRuntimeRequest(
            operation="task.create",
            taskId="task-b",
            objective="second",
            sessionKey="session-1",
            now=101,
        ),
        state_store=store,
    )
    boundary.execute(
        MissionRuntimeRequest(
            operation="task.create",
            taskId="task-c",
            objective="third",
            sessionKey="session-2",
            now=102,
        ),
        state_store=store,
    )

    listed = boundary.execute(MissionRuntimeRequest(operation="task.list"), state_store=store)
    filtered = boundary.execute(
        MissionRuntimeRequest(
            operation="task.list",
            sessionKey="session-1",
            taskStatusFilter="running",
            limit=1,
        ),
        state_store=store,
    )
    got = boundary.execute(
        MissionRuntimeRequest(operation="task.get", taskId="task-a"),
        state_store=store,
    )
    waited = boundary.execute(
        MissionRuntimeRequest(operation="task.wait", taskId="task-b"),
        state_store=store,
    )

    assert listed.status == "task_recorded_local_fake"
    assert listed.tasks is not None
    assert tuple(task.task_id for task in listed.tasks) == ("task-a", "task-b", "task-c")
    assert tuple(task.task_id for task in filtered.tasks) == ("task-b",)
    assert got.task is not None
    assert got.task.task_id == "task-a"
    assert waited.task is not None
    assert waited.task.task_id == "task-b"
    assert waited.reason_codes == ("task_wait_pending_metadata_only",)
    assert listed.public_projection()["authorityFlags"]["backgroundTaskStarted"] is False


def test_mission_runtime_public_projection_redacts_raw_state_and_tokens() -> None:
    store = FakeMissionStore()
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(enabled=True, localFakeStateStoreEnabled=True),
    ).execute(
        MissionRuntimeRequest(
            operation="goal.create",
            goalId="goal-secret",
            objective=(
                "Use /Users/kevin/private, /home/kevin/.ssh/id_rsa, "
                "/var/lib/kubelet/pods/x and token ghp_missionSecret"
            ),
            metadata={
                "rawTranscript": "hidden reasoning",
                "botToken": "123456:ABC-secret-token",
                "note": "public",
            },
        ),
        state_store=store,
    )

    projection = decision.public_projection()
    assert "/Users/kevin" not in str(projection)
    assert "/home/kevin" not in str(projection)
    assert "/var/lib/kubelet" not in str(projection)
    assert "ghp_missionSecret" not in str(projection)
    assert "123456:ABC-secret-token" not in str(projection)
    assert "hidden reasoning" not in str(projection)
    assert projection["diagnosticMetadata"]["note"] == "public"


def test_mission_runtime_diagnostic_metadata_cannot_forge_authority() -> None:
    decision = MissionRuntimeBoundary(MissionRuntimeConfig()).execute(
        MissionRuntimeRequest(
            operation="goal.create",
            objective="Ship report",
            metadata={
                "productionWritesEnabled": True,
                "routeAttached": True,
                "backgroundSchedulerAttached": True,
                "backgroundTaskStarted": True,
                "authority": "python",
                "authoritative": True,
                "trusted": True,
                "verified": True,
                "note": "public",
            },
        )
    )

    projection = decision.public_projection()
    diagnostic = str(projection["diagnosticMetadata"])

    assert decision.status == "disabled"
    assert "productionWritesEnabled" not in diagnostic
    assert "routeAttached" not in diagnostic
    assert "backgroundSchedulerAttached" not in diagnostic
    assert "backgroundTaskStarted" not in diagnostic
    assert "authority" not in diagnostic
    assert "authoritative" not in diagnostic
    assert "trusted" not in diagnostic
    assert "verified" not in diagnostic
    assert projection["diagnosticMetadata"]["note"] == "public"
    assert projection["authorityFlags"]["productionWritesEnabled"] is False


def test_mission_runtime_config_cannot_enable_live_authority_by_construct_or_copy() -> None:
    constructed = MissionRuntimeConfig.model_construct(
        enabled=True,
        localFakeStateStoreEnabled=True,
        localFakeSchedulerEnabled=True,
        backgroundSchedulerAttached=True,
        productionWritesEnabled=True,
        routeAttached=True,
    )
    copied = MissionRuntimeConfig().model_copy(
        update={
            "backgroundSchedulerAttached": True,
            "productionWritesEnabled": True,
            "routeAttached": True,
        }
    )
    deprecated_copy = MissionRuntimeConfig().copy(
        update={
            "background_scheduler_attached": True,
            "production_writes_enabled": True,
            "route_attached": True,
        }
    )

    for config in (constructed, copied, deprecated_copy):
        dump = config.model_dump(by_alias=True)
        assert dump["backgroundSchedulerAttached"] is False
        assert dump["productionWritesEnabled"] is False
        assert dump["routeAttached"] is False

    forged = MissionRuntimeConfig.model_construct(
        enabled=True,
        localFakeStateStoreEnabled=True,
        localFakeSchedulerEnabled=True,
        backgroundSchedulerAttached=True,
        productionWritesEnabled=True,
        routeAttached=True,
    )
    boundary = MissionRuntimeBoundary(forged)

    assert boundary.config.enabled is True
    assert boundary.config.local_fake_state_store_enabled is True
    assert boundary.config.local_fake_scheduler_enabled is True
    assert boundary.config.background_scheduler_attached is False
    assert boundary.config.production_writes_enabled is False
    assert boundary.config.route_attached is False


def test_mission_runtime_forged_projection_redacts_nested_goal_and_task_payloads() -> None:
    from openmagi_core_agent.harness.mission_runtime_boundary import (
        MissionRuntimeAuthorityFlags,
        MissionRuntimeDecision,
    )

    forged = MissionRuntimeDecision.model_construct(
        status="goal_recorded_local_fake",
        operation="goal.create",
        goal=GoalRecord.model_construct(
            goal_id="/Users/kevin/private-goal",
            objective="Ship /workspace/private token ghp_missionSecret",
            status="running",
            budget=GoalBudget(maxTurns=1),
            created_at=0,
            updated_at=0,
        ),
        task=BackgroundTaskRecord.model_construct(
            task_id="/data/bots/private-task",
            parent_turn_id="turn-1",
            status="running",
            prompt_preview="raw transcript hidden reasoning",
            mission_id="/Users/kevin/private-mission",
            created_at=0,
            updated_at=0,
        ),
        reasonCodes=("forged",),
        authorityFlags=MissionRuntimeAuthorityFlags.model_construct(
            backgroundSchedulerAttached=True,
            backgroundTaskStarted=True,
            productionWritesEnabled=True,
        ),
    )

    projection = forged.public_projection()
    encoded = str(projection)
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "/data/bots" not in encoded
    assert "ghp_missionSecret" not in encoded
    assert "hidden reasoning" not in encoded
    assert projection["authorityFlags"]["backgroundSchedulerAttached"] is False
    assert projection["authorityFlags"]["productionWritesEnabled"] is False


def test_mission_runtime_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.mission_runtime_boundary")
forbidden = (
    "google.adk.runners",
    "google.adk.agents",
    "openmagi_core_agent.runtime.runner",
    "subprocess",
    "telegram",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
