from __future__ import annotations

from openmagi_core_agent.harness.mission_runtime_boundary import (
    GoalRecord,
    MissionRuntimeAuthorityFlags,
    MissionRuntimeBoundary,
    MissionRuntimeConfig,
    MissionRuntimeRequest,
    build_scheduler_tick_lock_metadata,
)


class FakeMissionStore:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.goals: dict[str, GoalRecord] = {
            "goal-1": GoalRecord(
                goalId="goal-1",
                objective="Ship report",
                status="running",
            )
        }

    def save_goal(self, goal: GoalRecord) -> GoalRecord:
        self.goals[goal.goal_id] = goal
        return goal

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        return self.goals.get(goal_id)

    def save_task(self, task: object) -> object:
        return task

    def get_task(self, task_id: str) -> None:
        return None

    def list_tasks(self) -> tuple[object, ...]:
        return ()


class FakeScheduler:
    openmagi_local_fake_provider = True

    def record_tick(self, request: MissionRuntimeRequest, goal: GoalRecord | None) -> dict[str, object]:
        _ = request, goal
        return {
            "stopCondition": "no_background_tick_started",
            "backgroundSchedulerAttached": True,
            "channelDeliveryEnabled": True,
            "modelCallEnabled": True,
            "note": "local-fake",
        }


class OverridingScheduler(FakeScheduler):
    def record_tick(self, request: MissionRuntimeRequest, goal: GoalRecord | None) -> dict[str, object]:
        _ = request, goal
        return {
            "tickId": "model:gpt-5",
            "tickLockRef": "provider:openai",
            "recordOnly": False,
            "recursiveDenied": False,
            "note": "model:gpt-5",
        }


def test_scheduler_tick_lock_metadata_is_record_only_and_does_not_attach_authority() -> None:
    metadata = build_scheduler_tick_lock_metadata(
        tickId="tick:20260528T190000Z",
        tickLockRef="tick-lock:runtime-heartbeat",
        overlapDetected=True,
    )

    assert metadata == {
        "tickId": "tick:20260528T190000Z",
        "tickLockRef": "tick-lock:runtime-heartbeat",
        "overlapPolicy": "skip-if-held",
        "overlapDetected": True,
        "recursiveDenied": False,
        "recordOnly": True,
    }


def test_scheduler_tick_public_projection_keeps_lock_metadata_but_drops_live_authority() -> None:
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        )
    ).execute(
        MissionRuntimeRequest(
            operation="scheduler.tick",
            goalId="goal-1",
            metadata={
                "tickId": "tick:20260528T190000Z",
                "tickLockRef": "tick-lock:runtime-heartbeat",
                "overlapDetected": True,
            },
        ),
        state_store=FakeMissionStore(),
        scheduler=FakeScheduler(),
    )

    projection = decision.public_projection()
    scheduler_metadata = projection["schedulerMetadata"]

    assert decision.status == "scheduler_tick_recorded_local_fake"
    assert scheduler_metadata["tickId"] == "tick:20260528T190000Z"
    assert scheduler_metadata["tickLockRef"] == "tick-lock:runtime-heartbeat"
    assert scheduler_metadata["overlapPolicy"] == "skip-if-held"
    assert scheduler_metadata["overlapDetected"] is True
    assert scheduler_metadata["recordOnly"] is True
    assert scheduler_metadata["note"] == "local-fake"
    assert "backgroundSchedulerAttached" not in scheduler_metadata
    assert "channelDeliveryEnabled" not in scheduler_metadata
    assert "modelCallEnabled" not in scheduler_metadata
    assert set(projection["authorityFlags"].values()) == {False}


def test_scheduler_tick_local_fake_metadata_cannot_override_lock_or_project_authority_values() -> None:
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        )
    ).execute(
        MissionRuntimeRequest(
            operation="scheduler.tick",
            goalId="goal-1",
            metadata={
                "tickId": "tick:20260528T190000Z",
                "tickLockRef": "tick-lock:runtime-heartbeat",
            },
        ),
        state_store=FakeMissionStore(),
        scheduler=OverridingScheduler(),
    )

    projection = decision.public_projection()
    scheduler_metadata = projection["schedulerMetadata"]
    encoded = str(projection)

    assert decision.status == "scheduler_tick_recorded_local_fake"
    assert scheduler_metadata["tickId"] == "tick:20260528T190000Z"
    assert scheduler_metadata["tickLockRef"] == "tick-lock:runtime-heartbeat"
    assert scheduler_metadata["recordOnly"] is True
    assert scheduler_metadata["recursiveDenied"] is False
    assert "model:gpt-5" not in encoded
    assert "provider:openai" not in encoded
    assert set(projection["authorityFlags"].values()) == {False}


def test_scheduler_tick_recursive_request_is_denied_as_metadata_only() -> None:
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        )
    ).execute(
        MissionRuntimeRequest(
            operation="scheduler.tick",
            goalId="goal-1",
            metadata={
                "tickId": "tick:recursive",
                "tickLockRef": "tick-lock:runtime-heartbeat",
                "recursiveRequested": True,
            },
        ),
        state_store=FakeMissionStore(),
        scheduler=FakeScheduler(),
    )

    projection = decision.public_projection()

    assert decision.status == "blocked"
    assert decision.reason_codes == ("recursive_scheduler_denied",)
    assert projection["schedulerMetadata"]["recursiveDenied"] is True
    assert projection["schedulerMetadata"]["recordOnly"] is True
    assert projection["authorityFlags"]["backgroundSchedulerAttached"] is False
    assert projection["authorityFlags"]["modelCallEnabled"] is False
    assert projection["authorityFlags"]["providerCallEnabled"] is False
    assert projection["authorityFlags"]["toolExecutionEnabled"] is False
    assert projection["authorityFlags"]["channelDeliveryEnabled"] is False


def test_scheduler_tick_recursive_request_is_denied_without_lock_metadata() -> None:
    class CountingScheduler(FakeScheduler):
        def __init__(self) -> None:
            self.calls = 0

        def record_tick(
            self,
            request: MissionRuntimeRequest,
            goal: GoalRecord | None,
        ) -> dict[str, object]:
            self.calls += 1
            return super().record_tick(request, goal)

    scheduler = CountingScheduler()
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        )
    ).execute(
        MissionRuntimeRequest(
            operation="scheduler.tick",
            goalId="goal-1",
            metadata={"recursiveRequested": True},
        ),
        state_store=FakeMissionStore(),
        scheduler=scheduler,
    )

    projection = decision.public_projection()

    assert decision.status == "blocked"
    assert decision.reason_codes == ("recursive_scheduler_denied",)
    assert scheduler.calls == 0
    assert projection["schedulerMetadata"]["recursiveDenied"] is True
    assert projection["schedulerMetadata"]["recordOnly"] is True
    assert set(projection["authorityFlags"].values()) == {False}


def test_scheduler_tick_invalid_lock_metadata_fails_closed_without_scheduler_call() -> None:
    class CountingScheduler(FakeScheduler):
        def __init__(self) -> None:
            self.calls = 0

        def record_tick(
            self,
            request: MissionRuntimeRequest,
            goal: GoalRecord | None,
        ) -> dict[str, object]:
            self.calls += 1
            return super().record_tick(request, goal)

    scheduler = CountingScheduler()
    decision = MissionRuntimeBoundary(
        MissionRuntimeConfig(
            enabled=True,
            localFakeStateStoreEnabled=True,
            localFakeSchedulerEnabled=True,
        )
    ).execute(
        MissionRuntimeRequest(
            operation="scheduler.tick",
            goalId="goal-1",
            metadata={
                "tickId": "/Users/kevin/private",
                "tickLockRef": "tick-lock:runtime-heartbeat",
            },
        ),
        state_store=FakeMissionStore(),
        scheduler=scheduler,
    )

    encoded = str(decision.public_projection())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("scheduler_tick_lock_metadata_invalid",)
    assert scheduler.calls == 0
    assert "/Users/kevin" not in encoded
    assert set(decision.public_projection()["authorityFlags"].values()) == {False}


def test_scheduler_tick_rejects_authority_shaped_lock_refs() -> None:
    for metadata in (
        {
            "tickId": "model:gpt-5",
            "tickLockRef": "tick-lock:runtime-heartbeat",
        },
        {
            "tickId": "tick:20260528T190000Z",
            "tickLockRef": "provider:openai",
        },
    ):
        decision = MissionRuntimeBoundary(
            MissionRuntimeConfig(
                enabled=True,
                localFakeStateStoreEnabled=True,
                localFakeSchedulerEnabled=True,
            )
        ).execute(
            MissionRuntimeRequest(
                operation="scheduler.tick",
                goalId="goal-1",
                metadata=metadata,
            ),
            state_store=FakeMissionStore(),
            scheduler=FakeScheduler(),
        )

        encoded = str(decision.public_projection())

        assert decision.status == "blocked"
        assert decision.reason_codes == ("scheduler_tick_lock_metadata_invalid",)
        assert "model:gpt-5" not in encoded
        assert "provider:openai" not in encoded
        assert set(decision.public_projection()["authorityFlags"].values()) == {False}


def test_mission_runtime_authority_flags_force_new_live_authority_fields_false() -> None:
    flags = MissionRuntimeAuthorityFlags.model_construct(
        backgroundSchedulerAttached=True,
        modelCallEnabled=True,
        providerCallEnabled=True,
        toolExecutionEnabled=True,
        childExecutionEnabled=True,
        channelDeliveryEnabled=True,
        schedulerAttached=True,
        workspaceMutationEnabled=True,
        memoryWriteEnabled=True,
    )

    projection = flags.model_dump(by_alias=True)

    assert projection["backgroundSchedulerAttached"] is False
    assert projection["modelCallEnabled"] is False
    assert projection["providerCallEnabled"] is False
    assert projection["toolExecutionEnabled"] is False
    assert projection["childExecutionEnabled"] is False
    assert projection["channelDeliveryEnabled"] is False
    assert projection["schedulerAttached"] is False
    assert projection["workspaceMutationEnabled"] is False
    assert projection["memoryWriteEnabled"] is False
