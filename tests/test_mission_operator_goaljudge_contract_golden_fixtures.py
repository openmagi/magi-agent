from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.mission_operator_goaljudge_contract import (
    MissionOperatorGoalJudgeAttachmentFlags,
    MissionOperatorGoalJudgeContractFixture,
    load_mission_operator_goaljudge_contract_fixture,
    project_mission_operator_goaljudge_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "mission_operator_goaljudge"


def test_mission_operator_goaljudge_fixture_covers_public_metadata_surfaces() -> None:
    fixture = load_mission_operator_goaljudge_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_mission_operator_goaljudge_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "mission_operator_goaljudge_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.metadata_only is True
    assert projection.default_off is True
    assert projection.no_live_execution is True
    assert set(fixture.runtime_authority.model_dump(by_alias=True).values()) == {False}
    assert set(projection.runtime_authority.model_dump(by_alias=True).values()) == {False}
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.case_order == (
        "operator_comment_metadata",
        "operator_watch_metadata",
        "operator_stats_snapshot_metadata",
        "operator_tail_snapshot_metadata",
        "goaljudge_done_eval_metadata",
        "goaljudge_continue_eval_metadata",
        "goaljudge_blocked_eval_metadata",
        "goaljudge_needs_user_eval_metadata",
        "goaljudge_invalid_structured_output_blocks",
        "goaljudge_error_blocks",
    )
    assert projection.by_category == {
        "operator_surface": 4,
        "goaljudge_eval": 6,
    }
    assert projection.by_surface == {
        "comment": 1,
        "watch": 1,
        "stats": 1,
        "tail": 1,
        "goaljudge": 6,
    }
    assert projection.by_verdict == {
        "done": 1,
        "continue": 1,
        "blocked": 3,
        "needs_user": 1,
    }

    comment = cases["operator_comment_metadata"]
    assert comment.operator_surface is not None
    assert comment.operator_surface.surface == "comment"
    assert comment.operator_surface.public_projection["message"] == "Looks ready for review."
    assert comment.operator_surface.public_projection["operatorPollingAttached"] is False

    watch = cases["operator_watch_metadata"]
    assert watch.operator_surface is not None
    assert watch.operator_surface.surface == "watch"
    assert watch.operator_surface.public_projection["watchState"] == "enabled"
    assert watch.operator_surface.public_projection["subscriptionAttached"] is False

    stats = cases["operator_stats_snapshot_metadata"]
    assert stats.operator_surface is not None
    assert stats.operator_surface.public_projection["completedSteps"] == 3
    assert stats.operator_surface.public_projection["missionWritesAttached"] is False

    tail = cases["operator_tail_snapshot_metadata"]
    assert tail.operator_surface is not None
    assert tail.operator_surface.public_projection["lineCount"] == 2
    assert tail.operator_surface.public_projection["tailSnapshotAttached"] is False

    done = cases["goaljudge_done_eval_metadata"]
    assert done.goal_judge is not None
    assert done.goal_judge.verdict == "done"
    assert done.goal_judge.validator_contract == "structured_output_validator"
    assert done.goal_judge.adk_attachment_boundary == "future_eval_callback_only"
    assert done.goal_judge.public_projection["validatorResult"] == "valid"

    continuation = cases["goaljudge_continue_eval_metadata"]
    assert continuation.goal_judge is not None
    assert continuation.goal_judge.verdict == "continue"
    assert continuation.goal_judge.continuation_allowed is True

    blocked = cases["goaljudge_blocked_eval_metadata"]
    assert blocked.goal_judge is not None
    assert blocked.goal_judge.verdict == "blocked"
    assert blocked.goal_judge.continuation_allowed is False

    needs_user = cases["goaljudge_needs_user_eval_metadata"]
    assert needs_user.goal_judge is not None
    assert needs_user.goal_judge.verdict == "needs_user"
    assert needs_user.goal_judge.public_projection["requiresUserInput"] is True

    invalid = cases["goaljudge_invalid_structured_output_blocks"]
    assert invalid.goal_judge is not None
    assert invalid.goal_judge.input_shape == "invalid_structured_output"
    assert invalid.goal_judge.verdict == "blocked"
    assert invalid.goal_judge.reason_codes == ("invalid_structured_output",)

    judge_error = cases["goaljudge_error_blocks"]
    assert judge_error.goal_judge is not None
    assert judge_error.goal_judge.input_shape == "judge_error"
    assert judge_error.goal_judge.verdict == "blocked"
    assert judge_error.goal_judge.reason_codes == ("judge_error",)

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
        "rawStructuredOutput",
        "schedulerTickAttached\": true",
        "backgroundResumeAttached\": true",
        "operatorPollingAttached\": true",
        "operatorSubscriptionAttached\": true",
        "subscriptionAttached\": true",
        "missionWritesAttached\": true",
        "routeApiDashboardAttached\": true",
        "adkRunnerInvoked\": true",
        "modelCalled\": true",
        "toolHostDispatched\": true",
        "missionUsesLongRunningFunctionTool\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"schedulerTickAttached": True}),
            id="scheduler-tick-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"backgroundResumeAttached": True}),
            id="background-resume-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"operatorPollingAttached": True}),
            id="operator-polling-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"missionWritesAttached": True}),
            id="mission-writes-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"routeApiDashboardAttached": True}),
            id="route-api-dashboard-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="adk-runner-invoked",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"modelCalled": True}),
            id="model-called",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"toolHostDispatched": True}),
            id="toolhost-dispatched",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update({"operatorPollingAuthority": True}),
            id="runtime-operator-polling-authority",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update({"adkRunnerInvocationAuthority": True}),
            id="runtime-adk-runner-authority",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update({"modelCallAuthority": True}),
            id="runtime-model-call-authority",
        ),
        pytest.param(
            lambda payload: payload["runtimeAuthority"].update({"routeApiDashboardProxyDeployAuthority": True}),
            id="runtime-route-api-dashboard-authority",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["operatorSurface"].update(
                {"subscriptionAttached": True}
            ),
            id="operator-subscription-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["operatorSurface"][
                "publicProjection"
            ].update({"operatorSubscriptionAttached": True}),
            id="operator-public-projection-operator-subscription-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["operatorSurface"][
                "publicProjection"
            ].update({"liveFlags": {"modelCalled": True}}),
            id="operator-public-projection-forbidden-live-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][2]["operatorSurface"].update(
                {"missionWritesAttached": True}
            ),
            id="operator-mission-writes-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["goalJudge"].update(
                {"adkRunnerInvoked": True}
            ),
            id="goaljudge-adk-runner-invoked",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["goalJudge"].update(
                {"modelCalled": True}
            ),
            id="goaljudge-model-called",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["goalJudge"].update(
                {"missionUsesLongRunningFunctionTool": True}
            ),
            id="goaljudge-mission-long-running-tool",
        ),
        pytest.param(
            lambda payload: payload["cases"][8]["goalJudge"].update(
                {"verdict": "continue", "continuationAllowed": True}
            ),
            id="invalid-structured-output-must-block",
        ),
        pytest.param(
            lambda payload: payload["cases"][9]["goalJudge"].update(
                {"verdict": "continue", "continuationAllowed": True}
            ),
            id="judge-error-must-block",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["goalJudge"]["publicProjection"].update(
                {"rawStructuredOutput": {"secret": "sk-mission-secret"}}
            ),
            id="raw-structured-output-public-projection",
        ),
    ),
)
def test_mission_operator_goaljudge_fixture_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        MissionOperatorGoalJudgeContractFixture.model_validate(payload)


def test_mission_operator_goaljudge_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = MissionOperatorGoalJudgeAttachmentFlags.model_construct(
        schedulerTickAttached=True,
        backgroundResumeAttached=True,
        operatorPollingAttached=True,
        missionWritesAttached=True,
        adkRunnerInvoked=True,
        modelCalled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"modelCalled": True})


def test_mission_operator_goaljudge_import_boundary_stays_runtime_free() -> None:
    module_name = "magi_agent.shadow.mission_operator_goaljudge_contract"
    forbidden = (
        "google.adk",
        "magi_agent.adk_bridge",
        "magi_agent.tools.dispatcher",
        "magi_agent.memory",
        "magi_agent.missions",
        "magi_agent.scheduler",
        "magi_agent.app",
        "magi_agent.transport.chat",
        "magi_agent.routes",
        "magi_agent.runtime",
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
        fixture = module.load_mission_operator_goaljudge_contract_fixture(
            "policy_matrix.json",
            fixture_root=FIXTURES,
        )
        module.project_mission_operator_goaljudge_contract_fixture(fixture)

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
