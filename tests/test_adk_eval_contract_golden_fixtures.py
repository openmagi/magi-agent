from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.adk_eval_fixture_contract import (
    AdkEvalFixtureAttachmentFlags,
    AdkEvalFixtureContract,
    load_adk_eval_fixture_contract,
    project_adk_eval_fixture_contract,
)


FIXTURES = Path(__file__).parent / "fixtures" / "adk_eval_contract"
ALL_FIXTURES = Path(__file__).parent / "fixtures"
RESEARCH_FIXTURE = ALL_FIXTURES / "research_source_evidence" / "policy_matrix.json"
CODING_FIXTURE = ALL_FIXTURES / "coding_verification_evidence" / "policy_matrix.json"
FACT_GROUNDING_FIXTURE = ALL_FIXTURES / "fact_grounding_verifier" / "policy_matrix.json"

ATTACHMENT_FLAGS = (
    "evaluationAttached",
    "adkRunnerInvoked",
    "modelCalled",
    "toolHostDispatched",
    "liveToolDispatched",
    "trafficAttached",
    "productionAuthority",
    "routeOrApiAttached",
    "memoryProviderCalled",
    "chatTransportAttached",
)


def _source_case_ids(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(str(case["caseId"]) for case in payload["cases"])


def _assert_attachment_flags_false(flags: dict[str, object]) -> None:
    assert tuple(flags) == ATTACHMENT_FLAGS
    assert set(flags.values()) == {False}


def test_adk_eval_fixture_projects_agent_evaluator_metadata_without_verdict_logic() -> None:
    research_case_ids = _source_case_ids(RESEARCH_FIXTURE)
    coding_case_ids = _source_case_ids(CODING_FIXTURE)
    fact_grounding_case_ids = _source_case_ids(FACT_GROUNDING_FIXTURE)

    fixture = load_adk_eval_fixture_contract(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_adk_eval_fixture_contract(
        fixture,
        reference_fixture_root=ALL_FIXTURES,
    )
    payload = fixture.model_dump(by_alias=True, mode="json", warnings=False)
    suites = {suite["sourceFixtureKind"]: suite for suite in payload["suites"]}

    assert projection.fixture_id == "adk_eval_fixture_contract_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.metadata_only is True
    assert projection.default_off is True
    assert projection.no_live_execution is True
    assert projection.suite_order == (
        "research_source_evidence_agent_evaluator_metadata",
        "coding_verification_evidence_agent_evaluator_metadata",
        "fact_grounding_verifier_agent_evaluator_metadata",
    )
    assert projection.by_source_fixture == {
        "research_source_evidence": len(research_case_ids),
        "coding_verification_evidence": len(coding_case_ids),
        "fact_grounding_verifier": len(fact_grounding_case_ids),
    }
    assert projection.by_task_type == {
        "research": len(research_case_ids),
        "coding": len(coding_case_ids),
        "fact_grounding": len(fact_grounding_case_ids),
    }
    assert projection.source_case_ids_by_suite[
        "research_source_evidence_agent_evaluator_metadata"
    ] == research_case_ids
    assert projection.source_case_ids_by_suite[
        "coding_verification_evidence_agent_evaluator_metadata"
    ] == coding_case_ids
    assert projection.source_case_ids_by_suite[
        "fact_grounding_verifier_agent_evaluator_metadata"
    ] == fact_grounding_case_ids
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    _assert_attachment_flags_false(payload["attachmentFlags"])

    research_suite = suites["research_source_evidence"]
    coding_suite = suites["coding_verification_evidence"]
    fact_grounding_suite = suites["fact_grounding_verifier"]
    assert tuple(case["sourceCaseId"] for case in research_suite["cases"]) == research_case_ids
    assert tuple(case["sourceCaseId"] for case in coding_suite["cases"]) == coding_case_ids
    assert (
        tuple(case["sourceCaseId"] for case in fact_grounding_suite["cases"])
        == fact_grounding_case_ids
    )
    assert research_suite["futureAdkPrimitive"] == "AgentEvaluator"
    assert coding_suite["futureAdkPrimitive"] == "AgentEvaluator"
    assert fact_grounding_suite["futureAdkPrimitive"] == "AgentEvaluator"
    assert research_suite["openMagiEvidenceSemantics"] == "product_contract_reference"
    assert coding_suite["openMagiEvidenceSemantics"] == "product_contract_reference"
    assert (
        fact_grounding_suite["openMagiEvidenceSemantics"]
        == "product_contract_reference"
    )
    assert research_suite["verdictSource"] == "existing_openmagi_fixture_projection"
    assert coding_suite["verdictSource"] == "existing_openmagi_fixture_projection"
    assert fact_grounding_suite["verdictSource"] == "existing_openmagi_fixture_projection"
    _assert_attachment_flags_false(research_suite["attachmentFlags"])
    _assert_attachment_flags_false(coding_suite["attachmentFlags"])
    _assert_attachment_flags_false(fact_grounding_suite["attachmentFlags"])

    research_snapshot = projection.case_snapshots["web_search_and_source_inspection_pass"]
    assert research_snapshot == {
        "caseId": "web_search_and_source_inspection_pass",
        "sourceFixtureKind": "research_source_evidence",
        "sourceFixtureId": "research_source_evidence_matrix_0001",
        "sourceCaseId": "web_search_and_source_inspection_pass",
        "taskType": "research",
        "agentRole": "research",
        "futureAdkPrimitive": "AgentEvaluator",
        "futureAdkEvalCaseType": "metadata_only_eval_case",
        "openMagiEvidenceSemantics": "product_contract_reference",
        "verdictSource": "existing_openmagi_fixture_projection",
        "localDiagnostic": True,
        "metadataOnly": True,
        "defaultOff": True,
        "evaluationAttached": False,
        "adkRunnerInvoked": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "liveToolDispatched": False,
        "trafficAttached": False,
        "productionAuthority": False,
    }
    coding_snapshot = projection.case_snapshots["post_edit_gitdiff_and_testrun_pass"]
    assert coding_snapshot["sourceFixtureKind"] == "coding_verification_evidence"
    assert coding_snapshot["taskType"] == "coding"
    assert coding_snapshot["futureAdkPrimitive"] == "AgentEvaluator"
    assert coding_snapshot["evaluationAttached"] is False
    assert coding_snapshot["adkRunnerInvoked"] is False
    assert coding_snapshot["modelCalled"] is False
    assert coding_snapshot["toolHostDispatched"] is False
    assert coding_snapshot["liveToolDispatched"] is False
    assert coding_snapshot["trafficAttached"] is False
    assert coding_snapshot["productionAuthority"] is False
    fact_grounding_snapshot = projection.case_snapshots[
        "mode_a_grounded_json_tool_values_match"
    ]
    assert fact_grounding_snapshot["sourceFixtureKind"] == "fact_grounding_verifier"
    assert fact_grounding_snapshot["sourceCaseId"] == "mode_a_grounded_json_tool_values_match"
    assert fact_grounding_snapshot["taskType"] == "fact_grounding"
    assert fact_grounding_snapshot["agentRole"] == "verifier"
    assert fact_grounding_snapshot["futureAdkPrimitive"] == "AgentEvaluator"
    assert fact_grounding_snapshot["evaluationAttached"] is False
    assert fact_grounding_snapshot["adkRunnerInvoked"] is False
    assert fact_grounding_snapshot["modelCalled"] is False
    assert fact_grounding_snapshot["toolHostDispatched"] is False
    assert fact_grounding_snapshot["liveToolDispatched"] is False
    assert fact_grounding_snapshot["trafficAttached"] is False
    assert fact_grounding_snapshot["productionAuthority"] is False

    fixture_json = json.dumps(payload, sort_keys=True)
    projection_json = json.dumps(
        projection.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    forbidden_verdict_fragments = (
        '"expectedOk"',
        '"expectedVerdictState"',
        '"expectedMissingTypes"',
        '"expectedMatchedTypes"',
        '"expectedFailureCodes"',
        '"records"',
        '"contract"',
        '"verdictState"',
        '"failureCodes"',
        '"matchedEvidenceTypes"',
    )
    for fragment in forbidden_verdict_fragments:
        assert fragment not in fixture_json
    for fragment in forbidden_verdict_fragments:
        assert fragment not in projection_json

    forbidden_runtime_fragments = (
        '"evaluationAttached": true',
        '"adkRunnerInvoked": true',
        '"modelCalled": true',
        '"toolHostDispatched": true',
        '"liveToolDispatched": true',
        '"trafficAttached": true',
        '"productionAuthority": true',
        "google.adk.evaluation",
        "Runner.run",
        "ToolHost.execute",
    )
    for fragment in forbidden_runtime_fragments:
        assert fragment not in fixture_json
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"evaluationAttached": True}),
            id="fixture-evaluation-attached",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="fixture-runner-invoked",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["attachmentFlags"].update(
                {"modelCalled": True}
            ),
            id="suite-model-called",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0]["attachmentFlags"].update(
                {"toolHostDispatched": True}
            ),
            id="case-toolhost-dispatched",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0]["attachmentFlags"].update(
                {"liveToolDispatched": True}
            ),
            id="case-live-tool-dispatched",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0]["attachmentFlags"].update(
                {"trafficAttached": True}
            ),
            id="case-traffic-attached",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0]["attachmentFlags"].update(
                {"productionAuthority": True}
            ),
            id="case-production-authority",
        ),
        pytest.param(
            lambda payload: payload.update({"metadataOnly": False}),
            id="fixture-not-metadata-only",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update(
                {"evaluationAttached": True}
            ),
            id="case-inline-evaluation-flag",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update({"expectedOk": True}),
            id="case-inline-expected-ok",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update(
                {"expectedVerdictState": "pass"}
            ),
            id="case-inline-verdict-state",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update({"records": []}),
            id="case-inline-records",
        ),
    ),
)
def test_adk_eval_fixture_rejects_live_flags_and_inline_verdict_logic(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        AdkEvalFixtureContract.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["suites"][0].update(
                {"sourceFixtureId": "coding_verification_evidence_matrix_0001"}
            ),
            id="suite-fixture-id-kind-mismatch",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update(
                {"sourceCaseId": "not_a_real_research_case"}
            ),
            id="unknown-source-case-id",
        ),
        pytest.param(
            lambda payload: payload["suites"][1]["cases"].pop(),
            id="missing-coding-source-case-reference",
        ),
        pytest.param(
            lambda payload: payload["suites"][2]["cases"].pop(),
            id="missing-fact-grounding-source-case-reference",
        ),
        pytest.param(
            lambda payload: payload["suites"][0]["cases"][0].update(
                {"sourceFixtureKind": "coding_verification_evidence"}
            ),
            id="case-source-kind-mismatch",
        ),
    ),
)
def test_adk_eval_projection_rejects_unknown_or_mismatched_source_references(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises((ValidationError, ValueError)):
        fixture = AdkEvalFixtureContract.model_validate(payload)
        project_adk_eval_fixture_contract(
            fixture,
            reference_fixture_root=ALL_FIXTURES,
        )


def test_adk_eval_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = AdkEvalFixtureAttachmentFlags.model_construct(
        evaluationAttached=True,
        adkRunnerInvoked=True,
        modelCalled=True,
        toolHostDispatched=True,
        liveToolDispatched=True,
        trafficAttached=True,
        productionAuthority=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"modelCalled": True})


def test_adk_eval_contract_import_boundary_stays_eval_runner_toolhost_route_memory_chat_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.adk_eval_fixture_contract import (
    load_adk_eval_fixture_contract,
    project_adk_eval_fixture_contract,
)

fixture = load_adk_eval_fixture_contract(
    'policy_matrix.json',
    fixture_root=Path('tests/fixtures/adk_eval_contract'),
)
project_adk_eval_fixture_contract(
    fixture,
    reference_fixture_root=Path('tests/fixtures'),
)

forbidden = (
    'google.adk.evaluation',
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.shadow.toolhost_contract',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
    'magi_agent.services.memory',
    'magi_agent.hipocampus',
    'magi_agent.qmd',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
)
loaded = [
    module_name
    for module_name in sorted(sys.modules)
    for forbidden_name in forbidden
    if module_name == forbidden_name or module_name.startswith(f'{forbidden_name}.')
]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr

    source = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "shadow"
        / "adk_eval_fixture_contract.py"
    ).read_text(encoding="utf-8")
    import_lines = "\n".join(
        line for line in source.splitlines() if re.match(r"^(from|import) ", line)
    )
    forbidden_import_fragments = (
        "google.adk.evaluation",
        "google.adk.runners",
        "magi_agent.adk_bridge",
        "magi_agent.shadow.toolhost_contract",
        "magi_agent.memory",
        "magi_agent.transport.chat",
        "magi_agent.routes",
    )
    for fragment in forbidden_import_fragments:
        assert fragment not in import_lines
