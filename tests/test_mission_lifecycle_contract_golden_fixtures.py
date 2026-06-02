from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.mission_lifecycle_contract import (
    MissionLifecycleAttachmentFlags,
    MissionLifecycleContractFixture,
    load_mission_lifecycle_contract_fixture,
    project_mission_lifecycle_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "mission_lifecycle"


def test_mission_lifecycle_fixture_covers_scheduled_goal_and_operator_boundaries() -> None:
    fixture = load_mission_lifecycle_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_mission_lifecycle_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "mission_lifecycle_matrix_0001"
    assert projection.local_diagnostic is True
    assert fixture.ts_parity_inputs.core_agent_goal_loop is True
    assert fixture.ts_parity_inputs.core_agent_missions is True
    assert fixture.ts_parity_inputs.core_agent_script_cron is True
    assert fixture.activation_defaults.persistent_goal_loop.enabled is False
    assert fixture.activation_defaults.persistent_goal_loop.scheduling_enabled is False
    assert fixture.activation_defaults.persistent_goal_loop.background_resume_enabled is False
    assert fixture.activation_defaults.scheduled_work.enabled is False
    assert fixture.activation_defaults.scheduled_work.scheduler_attached is False
    assert fixture.activation_defaults.scheduled_work.channel_delivery_attached is False
    assert fixture.activation_defaults.operator_controls.enabled is False
    assert fixture.activation_defaults.operator_controls.polling_attached is False
    assert fixture.activation_defaults.operator_controls.idempotency_required is True
    assert set(fixture.runtime_authority.model_dump(by_alias=True).values()) == {False}
    assert projection.case_order == (
        "manual_mission_identity_metadata",
        "goal_continuation_snapshot_continue",
        "goal_budget_exhausted_blocks_continuation",
        "operator_cancel_checkpoint_blocks_continuation",
        "operator_retry_unblock_requires_idempotency",
        "scheduled_agent_recipe_metadata",
        "scheduled_script_timeout_metadata",
        "pipeline_context_artifact_handoff",
        "pipeline_last_output_context_handoff",
        "long_running_tool_policy_not_mission_control",
    )
    assert projection.by_category == {
        "mission_identity": 1,
        "goal_continuation": 2,
        "operator_control": 2,
        "scheduled_work": 2,
        "pipeline_artifact_handoff": 2,
        "long_running_tool_boundary": 1,
    }
    assert projection.by_status == {
        "queued": 1,
        "running": 3,
        "blocked": 1,
        "cancelled": 1,
        "completed": 4,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True
    assert projection.ts_parity_inputs == {
        "CORE_AGENT_GOAL_LOOP": "1",
        "CORE_AGENT_MISSIONS": "1",
        "CORE_AGENT_SCRIPT_CRON": "1",
    }
    assert projection.activation_defaults["persistentGoalLoop"]["enabled"] is False
    assert projection.activation_defaults["scheduledWork"]["enabled"] is False
    assert projection.activation_defaults["operatorControls"]["pollingAttached"] is False
    assert set(projection.runtime_authority.model_dump(by_alias=True).values()) == {False}

    manual = cases["manual_mission_identity_metadata"]
    assert manual.recipe is not None
    assert manual.recipe.pack_id == "openmagi.missions"
    assert manual.recipe.mission_lifecycle.mission_uses_long_running_function_tool is False
    assert manual.identity.mission_id == "mission-manual-1"
    assert manual.identity.run_id == "run-manual-1"
    assert projection.case_snapshots[manual.case_id]["missionUsesLongRunningFunctionTool"] is False

    continuation = cases["goal_continuation_snapshot_continue"]
    assert continuation.continuation is not None
    assert continuation.continuation.decision == "continue"
    assert continuation.continuation.continuation_allowed is True
    assert continuation.continuation.turns_used == 4
    assert continuation.continuation.max_turns == 30
    assert continuation.goal_policy is not None
    assert continuation.goal_policy.enabled is False
    assert continuation.goal_policy.scheduling_enabled is False

    budget = cases["goal_budget_exhausted_blocks_continuation"]
    assert budget.continuation is not None
    assert budget.continuation.decision == "budget_exhausted"
    assert budget.continuation.continuation_allowed is False
    assert budget.reason_codes == ("budget_exhausted",)

    cancel = cases["operator_cancel_checkpoint_blocks_continuation"]
    assert cancel.operator_action is not None
    assert cancel.operator_action.action_type == "cancel"
    assert cancel.operator_action.checkpointed is True
    assert cancel.continuation is not None
    assert cancel.continuation.cancellation_requested is True
    assert cancel.continuation.continuation_allowed is False

    retry = cases["operator_retry_unblock_requires_idempotency"]
    assert retry.operator_action is not None
    assert retry.operator_action.action_type == "retry"
    assert retry.operator_action.idempotency_key == "mission-action:retry-1"
    assert retry.operator_action.result == "recorded_for_future_run"

    scheduled_agent = cases["scheduled_agent_recipe_metadata"]
    assert scheduled_agent.scheduled_work is not None
    assert scheduled_agent.scheduled_work.mode == "agent"
    assert scheduled_agent.scheduled_work.scheduler_attached is False
    assert scheduled_agent.scheduled_work.channel_delivery_attached is False

    scheduled_script = cases["scheduled_script_timeout_metadata"]
    assert scheduled_script.scheduled_work is not None
    assert scheduled_script.scheduled_work.mode == "script"
    assert scheduled_script.scheduled_work.timeout_ms == 300000
    assert scheduled_script.scheduled_work.script_path_hash == "sha256:" + "a" * 64
    assert scheduled_script.scheduled_work.stdout_preview == "script output redacted"

    pipeline = cases["pipeline_context_artifact_handoff"]
    assert pipeline.pipeline_context is not None
    assert pipeline.pipeline_context.context_from == "previous_output"
    assert pipeline.pipeline_context.used_context_artifact_ids == ("artifact-upstream-1",)
    assert pipeline.pipeline_context.transient_session_memory_replay is False

    last_output_pipeline = cases["pipeline_last_output_context_handoff"]
    assert last_output_pipeline.pipeline_context is not None
    assert last_output_pipeline.pipeline_context.context_from == "last_output_context"
    assert last_output_pipeline.pipeline_context.used_context_artifact_ids == (
        "artifact-last-output-redacted",
    )
    assert last_output_pipeline.pipeline_context.transient_session_memory_replay is False
    assert set(last_output_pipeline.attachment_flags.model_dump(by_alias=True).values()) == {
        False
    }

    long_running = cases["long_running_tool_policy_not_mission_control"]
    assert long_running.long_running_tool_policy is not None
    assert long_running.long_running_tool_policy.tool_step_uses_long_running_function_tool is True
    assert long_running.long_running_tool_policy.mission_uses_long_running_function_tool is False
    assert long_running.long_running_tool_policy.tool_name == "TaskWait"

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "mission-store",
        "scheduler-store",
        "Bearer unsafe",
        "ghp_missionsecret",
        "sk-mission-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "gateway token",
        "hidden reasoning",
        "missionStoreWritten\": true",
        "schedulerTickAttached\": true",
        "backgroundResumeAttached\": true",
        "channelDeliveryAttached\": true",
        "adkRunnerInvoked\": true",
        "adkRunnerInvocationAuthority\": true",
        "toolHostLiveDispatchAuthority\": true",
        "routeApiDashboardProxyDeployAuthority\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


def test_ts_env_parity_inputs_do_not_enable_python_mission_authority() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    payload["tsParityInputs"] = {
        "CORE_AGENT_GOAL_LOOP": "1",
        "CORE_AGENT_MISSIONS": "1",
        "CORE_AGENT_SCRIPT_CRON": "1",
    }

    fixture = MissionLifecycleContractFixture.model_validate(payload)
    projection = project_mission_lifecycle_contract_fixture(fixture)

    assert fixture.activation_defaults.persistent_goal_loop.enabled is False
    assert fixture.activation_defaults.scheduled_work.enabled is False
    assert fixture.activation_defaults.operator_controls.enabled is False
    assert set(fixture.runtime_authority.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"missionStoreWritten": True}),
            id="fixture-mission-store-written",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["attachmentFlags"].update(
                {"backgroundResumeAttached": True}
            ),
            id="case-background-resume-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["recipe"]["missionLifecycle"].update(
                {"missionUsesLongRunningFunctionTool": True}
            ),
            id="mission-modeled-as-long-running-tool",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["continuation"].update(
                {"turnsUsed": 30, "continuationAllowed": True}
            ),
            id="continuation-over-budget-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][3]["operatorAction"].update(
                {"checkpointed": False}
            ),
            id="operator-action-not-checkpointed",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["operatorAction"].update(
                {"idempotencyKey": None}
            ),
            id="retry-without-idempotency",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["scheduledWork"].update(
                {"timeoutMs": 300001}
            ),
            id="script-timeout-over-cap",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["scheduledWork"].update(
                {"scriptPath": "scripts/unsafe.py"}
            ),
            id="raw-script-path-extra-field",
        ),
        pytest.param(
            lambda payload: payload["cases"][7]["pipelineContext"].update(
                {"usedContextArtifactIds": []}
            ),
            id="pipeline-without-artifact-handoff",
        ),
        pytest.param(
            lambda payload: payload["cases"][9]["longRunningToolPolicy"].update(
                {"missionUsesLongRunningFunctionTool": True}
            ),
            id="long-running-tool-policy-mission-true",
        ),
        pytest.param(
            lambda payload: payload["cases"][5].update(
                {
                    "longRunningToolPolicy": {
                        "toolName": "TaskWait",
                        "unitOfWork": "long_tool_job",
                        "toolStepUsesLongRunningFunctionTool": True,
                        "missionUsesLongRunningFunctionTool": False,
                        "backgroundResumeAttached": False,
                        "schedulerAttached": False,
                        "operatorPollingAttached": False
                    }
                }
            ),
            id="scheduled-work-cannot-carry-long-running-tool-policy",
        ),
        pytest.param(
            lambda payload: payload["cases"][3]["operatorAction"].update(
                {"idempotencyKey": None}
            ),
            id="cancel-without-idempotency",
        ),
        pytest.param(
            lambda payload: payload["cases"][3]["operatorAction"].update(
                {"idempotencyKey": "   "}
            ),
            id="cancel-with-blank-idempotency",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["progressEvent"].update(
                {"publicPreview": "/data/bots/bot-secret/mission-store"}
            ),
            id="unsafe-progress-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["progressEvent"].update(
                {"message": "gateway token sk-mission-secret"}
            ),
            id="unsafe-secret-shaped-progress-message",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["progressEvent"].update(
                {"message": "safe " * 120 + "sk-live-missionsecret"}
            ),
            id="unsafe-secret-after-preview-truncation",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["scheduledWork"].update(
                {"enabled": True}
            ),
            id="scheduled-work-activation-enabled",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["scheduledWork"].update(
                {"schedulerAttached": True}
            ),
            id="scheduled-work-scheduler-attached",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["scheduledWork"].update(
                {"channelDeliveryAttached": True}
            ),
            id="scheduled-work-channel-delivery-attached",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["operatorControls"].update(
                {"enabled": True}
            ),
            id="operator-controls-enabled",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["operatorControls"].update(
                {"pollingAttached": True}
            ),
            id="operator-controls-polling-attached",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["operatorControls"].update(
                {"idempotencyRequired": False}
            ),
            id="operator-controls-idempotency-not-required",
        ),
        pytest.param(
            lambda payload: payload["activationDefaults"]["persistentGoalLoop"].update(
                {"backgroundResumeEnabled": True}
            ),
            id="goal-loop-background-resume-enabled",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update(
                {"adkRunnerInvocationAuthority": True}
            ),
            id="runtime-authority-adk-runner",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update(
                {"toolHostLiveDispatchAuthority": True}
            ),
            id="runtime-authority-toolhost-live-dispatch",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update(
                {"routeApiDashboardProxyDeployAuthority": True}
            ),
            id="runtime-authority-route-api-dashboard-proxy-deploy",
        ),
    ),
)
def test_mission_lifecycle_fixture_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        MissionLifecycleContractFixture.model_validate(payload)


def test_mission_lifecycle_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = MissionLifecycleAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        missionStoreWritten=True,
        schedulerTickAttached=True,
        backgroundResumeAttached=True,
        channelDeliveryAttached=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"missionStoreWritten": True})


def test_mission_lifecycle_import_boundary_stays_runtime_free() -> None:
    module_name = "magi_agent.shadow.mission_lifecycle_contract"
    forbidden = (
        "google.adk",
        "magi_agent.adk_bridge",
        "magi_agent.tools.dispatcher",
        "magi_agent.plugins.agentmemory",
        "magi_agent.memory",
        "magi_agent.services.memory",
        "magi_agent.hipocampus",
        "magi_agent.qmd",
        "magi_agent.missions",
        "magi_agent.scheduler",
        "magi_agent.app",
        "magi_agent.transport.chat",
        "magi_agent.routes",
    )
    removed_modules: dict[str, object] = {}
    for loaded_name in tuple(sys.modules):
        if (
            loaded_name == "magi_agent"
            or loaded_name.startswith("magi_agent.")
            or loaded_name == "google.adk"
            or loaded_name.startswith("google.adk.")
        ):
            removed = sys.modules.pop(loaded_name, None)
            if removed is not None:
                removed_modules[loaded_name] = removed

    try:
        module = importlib.import_module(module_name)
        fixture = module.load_mission_lifecycle_contract_fixture(
            "policy_matrix.json",
            fixture_root=FIXTURES,
        )
        module.project_mission_lifecycle_contract_fixture(fixture)

        loaded = [
            loaded_name
            for loaded_name in sorted(sys.modules)
            for forbidden_name in forbidden
            if loaded_name == forbidden_name
            or loaded_name.startswith(f"{forbidden_name}.")
        ]
        assert loaded == []
    finally:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name == "magi_agent"
                or loaded_name.startswith("magi_agent.")
                or loaded_name == "google.adk"
                or loaded_name.startswith("google.adk.")
            ):
                sys.modules.pop(loaded_name, None)
        sys.modules.update(removed_modules)
