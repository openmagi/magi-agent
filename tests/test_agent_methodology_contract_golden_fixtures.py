from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.agent_methodology_contract import (
    AgentMethodologyAttachmentFlags,
    AgentMethodologyCase,
    AgentMethodologyFixture,
    SanitizedChildEnvelope,
    load_agent_methodology_fixture,
    project_agent_methodology_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "agent_methodology"


def test_agent_methodology_fixture_projects_default_off_recipe_contract() -> None:
    fixture = load_agent_methodology_fixture("policy_matrix.json", fixture_root=FIXTURES)
    projection = project_agent_methodology_fixture(fixture)

    assert projection.fixture_id == "agent_methodology_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.recipe_pack_ids == (
        "openmagi.agent-methodology",
        "openmagi.superpowers-compat",
    )
    assert projection.case_order == (
        "using_superpowers_onboarding_import",
        "brainstorming_design_refinement_checkpoint",
        "writing_and_executing_plans_checkpoints",
        "tdd_red_green_refactor_validator",
        "systematic_debugging_validator",
        "verification_before_completion_evidence",
        "code_review_request_receive_checkpoints",
        "subagent_driven_development_parent_context_isolation",
        "git_worktree_workflow_checkpoint",
        "finishing_development_branch_checkpoint",
        "plan_auto_trigger_recipe_hook",
        "live_behavior_approval_gate",
    )
    assert projection.by_category == {
        "onboarding": 1,
        "design_refinement": 1,
        "planning": 1,
        "tdd": 1,
        "debugging": 1,
        "verification": 1,
        "code_review": 1,
        "subagent_development": 1,
        "git_worktree": 1,
        "branch_finish": 1,
        "plan_auto_trigger": 1,
        "approval_gate": 1,
    }
    assert projection.no_live_execution is True
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert "ADK callbacks/plugins/evals/session primitives" in projection.future_live_surfaces
    assert "ADK Runner replacement" not in projection.future_live_surfaces
    assert "LongRunningFunctionTool mission orchestration" not in projection.future_live_surfaces

    plan_case = projection.case_snapshots["plan_auto_trigger_recipe_hook"]
    assert plan_case["modeledAs"] == ("recipe-hook", "checkpoint")
    assert plan_case["liveBehaviorApprovalGated"] is True
    assert plan_case["liveSlashRuntimeAttached"] is False
    assert plan_case["runnerRouteRefs"] == ()

    verification = projection.case_snapshots["verification_before_completion_evidence"]
    assert verification["modeledAs"] == ("validator", "evidence", "checkpoint")
    assert verification["validatorRefs"] == (
        "validator:agent-methodology:verification-before-completion",
    )
    assert verification["evidenceRefs"] == (
        "evidence:agent-methodology:test-run",
        "evidence:agent-methodology:git-diff",
    )

    subagent = projection.case_snapshots[
        "subagent_driven_development_parent_context_isolation"
    ]
    assert subagent["parentContextIsolation"]["rawChildTranscriptInjected"] is False
    assert subagent["parentContextIsolation"]["toolLogsInjected"] is False
    assert subagent["parentContextIsolation"]["hiddenReasoningInjected"] is False
    assert subagent["parentContextIsolation"]["sanitizedStructuredEnvelopeOnly"] is True
    assert subagent["childEnvelopeRefs"] == (
        "evidence:agent-methodology:sanitized-child-envelope",
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "raw child transcript",
        "tool log",
        "hidden reasoning",
        "Bearer unsafe",
        "sk-methodology-secret",
        "/data/bots",
        "/workspace",
        "adkRunnerInvoked\": true",
        "childExecutionAttached\": true",
        "liveSlashRuntimeAttached\": true",
        "toolHostDispatched\": true",
        "privateRefs",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="fixture-runner-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"liveSlashRuntimeAttached": True}
            ),
            id="case-live-slash-runtime-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][7]["parentContextIsolation"].update(
                {"rawChildTranscriptInjected": True}
            ),
            id="raw-child-transcript-injected",
        ),
        pytest.param(
            lambda payload: payload["cases"][7]["parentContextIsolation"].update(
                {"toolLogsInjected": True}
            ),
            id="tool-logs-injected",
        ),
        pytest.param(
            lambda payload: payload["cases"][7]["parentContextIsolation"].update(
                {"hiddenReasoningInjected": True}
            ),
            id="hidden-reasoning-injected",
        ),
        pytest.param(
            lambda payload: payload["cases"][7]["sanitizedChildEnvelope"].update(
                {"preview": "raw child transcript with sk-methodology-secret"}
            ),
            id="unsafe-child-envelope-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][10].update(
                {"modeledAs": ["route-branch", "session-controller"]}
            ),
            id="hidden-route-branch",
        ),
        pytest.param(
            lambda payload: payload["cases"][11].update(
                {"liveBehaviorApprovalGated": False}
            ),
            id="live-behavior-not-approval-gated",
        ),
        pytest.param(
            lambda payload: payload.update(
                {
                    "futureLiveSurfaces": [
                        "ADK LongRunningFunctionTool planning orchestration"
                    ]
                }
            ),
            id="long-running-function-tool-planning-variant",
        ),
    ),
)
def test_agent_methodology_fixture_rejects_live_or_raw_parent_context_claims(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text())
    mutation(payload)

    with pytest.raises(ValidationError):
        AgentMethodologyFixture.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload.update({"cases": payload["cases"][:-1]}),
            id="missing-required-category",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"capabilityRefs": ["using-superpowers"]}
            ),
            id="missing-required-capability",
        ),
    ),
)
def test_agent_methodology_fixture_rejects_incomplete_required_case_matrix(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text())
    mutation(payload)

    with pytest.raises(ValidationError):
        AgentMethodologyFixture.model_validate(payload)


def test_agent_methodology_attachment_flags_cannot_be_forged_by_construct() -> None:
    flags = AgentMethodologyAttachmentFlags.model_construct(
        adk_runner_invoked=True,
        child_execution_attached=True,
        live_slash_runtime_attached=True,
        tool_host_dispatched=True,
    )

    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_agent_methodology_attachment_flags_cannot_be_forged_by_model_copy() -> None:
    flags = AgentMethodologyAttachmentFlags().model_copy(
        update={
            "adk_runner_invoked": True,
            "child_execution_attached": True,
            "live_slash_runtime_attached": True,
            "tool_host_dispatched": True,
        }
    )

    assert flags.adk_runner_invoked is False
    assert flags.child_execution_attached is False
    assert flags.live_slash_runtime_attached is False
    assert flags.tool_host_dispatched is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_agent_methodology_projection_revalidates_forged_case_model_copy() -> None:
    fixture = load_agent_methodology_fixture("policy_matrix.json", fixture_root=FIXTURES)
    forged_case = fixture.cases[0].model_copy(update={"live_slash_runtime_attached": True})
    forged_fixture = fixture.model_copy(
        update={"cases": (forged_case, *fixture.cases[1:])}
    )

    assert isinstance(forged_case, AgentMethodologyCase)
    with pytest.raises(ValidationError):
        project_agent_methodology_fixture(forged_fixture)


def test_agent_methodology_projection_revalidates_forged_child_envelope_construct() -> None:
    fixture = load_agent_methodology_fixture("policy_matrix.json", fixture_root=FIXTURES)
    forged_envelope = SanitizedChildEnvelope.model_construct(
        envelope_ref="child-envelope:forged",
        status="done",
        preview="raw child transcript with sk-methodology-secret",
        evidence_refs=("evidence:agent-methodology:sanitized-child-envelope",),
        private_refs=("private:/workspace/tool-log",),
    )
    forged_cases = list(fixture.cases)
    forged_cases[7] = forged_cases[7].model_copy(
        update={"sanitized_child_envelope": forged_envelope}
    )
    forged_fixture = fixture.model_copy(update={"cases": tuple(forged_cases)})

    with pytest.raises(ValidationError):
        project_agent_methodology_fixture(forged_fixture)


def test_agent_methodology_projection_revalidates_forged_fixture_model_copy() -> None:
    fixture = load_agent_methodology_fixture("policy_matrix.json", fixture_root=FIXTURES)
    forged_fixture = fixture.model_copy(
        update={
            "future_live_surfaces": (
                "ADK LongRunningFunctionTool mission planning orchestration",
            )
        }
    )

    with pytest.raises(ValidationError):
        project_agent_methodology_fixture(forged_fixture)


def test_agent_methodology_contract_import_boundary_has_no_runtime_attachment() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.shadow.agent_methodology_contract")
assert hasattr(module, "AgentMethodologyFixture")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.memory",
    "magi_agent.workspace.mutation",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"agent methodology contract import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_shadow_package_lazy_export_for_agent_methodology_contract() -> None:
    shadow = importlib.import_module("magi_agent.shadow")

    assert shadow.AgentMethodologyFixture is AgentMethodologyFixture
