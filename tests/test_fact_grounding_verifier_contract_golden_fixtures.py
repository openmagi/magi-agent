from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.fact_grounding_verifier_contract import (
    FactGroundingVerifierAttachmentFlags,
    FactGroundingVerifierFixture,
    load_fact_grounding_verifier_fixture,
    project_fact_grounding_verifier_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "fact_grounding_verifier"


def test_fact_grounding_verifier_fixture_projects_deterministic_mode_parity_metadata() -> None:
    fixture = load_fact_grounding_verifier_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_fact_grounding_verifier_fixture(fixture)

    assert projection.fixture_id == "fact_grounding_verifier_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.metadata_only is True
    assert projection.default_off is True
    assert projection.no_live_execution is True
    assert projection.future_adk_target == "ValidatorSet_or_AgentEvaluator_metadata"
    assert projection.case_order == (
        "mode_a_grounded_json_tool_values_match",
        "mode_a_distorted_identifier_mismatch",
        "mode_a_grounded_no_tool_results",
        "mode_a_grounded_general_knowledge_despite_tool_output",
        "mode_a_grounded_numbers_within_tolerance",
        "mode_a_distorted_significant_number_mismatch",
        "mode_a_low_confidence_grounded_values_not_referenced",
        "mode_b_fabricated_explicit_file_config_claim_without_read_tool",
        "mode_b_grounded_general_knowledge_no_tools",
        "mode_b_fabricated_english_config_claim_without_read_tool",
        "mode_b_fabricated_korean_script_reference_without_read_tool",
        "mode_b_grounded_honest_uncertainty",
        "mode_b_low_confidence_specific_details_without_file_claim_pattern",
    )
    assert projection.by_mode == {"A": 7, "B": 6}
    assert projection.by_verdict == {"GROUNDED": 8, "DISTORTED": 2, "FABRICATED": 3}
    assert projection.by_confidence == {"high": 10, "low": 3}
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}

    snapshots = projection.case_snapshots
    assert snapshots["mode_a_grounded_json_tool_values_match"] == {
        "caseId": "mode_a_grounded_json_tool_values_match",
        "mode": "A",
        "category": "grounded_json_tool_values_match",
        "verdict": "GROUNDED",
        "confidence": "high",
        "reasonCode": "all_tool_values_verified",
        "deterministicOnly": True,
        "metadataOnly": True,
        "defaultOff": True,
        "futureAdkTarget": "ValidatorSet_or_AgentEvaluator_metadata",
        "toolResultCount": 1,
        "assistantTextDigest": "assistant:mode-a-values-match",
        "toolOutputDigests": ("tool:config-json-values",),
    }
    assert snapshots["mode_a_distorted_identifier_mismatch"]["reasonCode"] == (
        "identifier_mismatch"
    )
    assert snapshots["mode_a_distorted_identifier_mismatch"]["verdict"] == "DISTORTED"
    assert snapshots["mode_a_grounded_no_tool_results"]["toolResultCount"] == 0
    assert snapshots["mode_a_grounded_no_tool_results"]["reasonCode"] == (
        "no_tool_results"
    )
    assert snapshots["mode_a_grounded_general_knowledge_despite_tool_output"][
        "reasonCode"
    ] == "values_not_referenced_needs_llm"
    assert snapshots["mode_a_grounded_general_knowledge_despite_tool_output"][
        "confidence"
    ] == "low"
    assert snapshots["mode_a_grounded_numbers_within_tolerance"]["reasonCode"] == (
        "numbers_within_tolerance"
    )
    assert snapshots["mode_a_distorted_significant_number_mismatch"]["reasonCode"] == (
        "significant_number_mismatch"
    )
    assert snapshots["mode_a_low_confidence_grounded_values_not_referenced"][
        "confidence"
    ] == "low"
    assert snapshots["mode_a_low_confidence_grounded_values_not_referenced"][
        "reasonCode"
    ] == "values_not_referenced_needs_llm"
    assert snapshots[
        "mode_b_fabricated_explicit_file_config_claim_without_read_tool"
    ]["reasonCode"] == "explicit_file_config_claim_without_read_tool"
    assert snapshots[
        "mode_b_fabricated_explicit_file_config_claim_without_read_tool"
    ]["verdict"] == "FABRICATED"
    assert snapshots["mode_b_grounded_general_knowledge_no_tools"] == {
        "caseId": "mode_b_grounded_general_knowledge_no_tools",
        "mode": "B",
        "category": "grounded_general_knowledge_no_tools",
        "verdict": "GROUNDED",
        "confidence": "high",
        "reasonCode": "general_knowledge_or_no_specific_claims",
        "deterministicOnly": True,
        "metadataOnly": True,
        "defaultOff": True,
        "futureAdkTarget": "ValidatorSet_or_AgentEvaluator_metadata",
        "toolResultCount": 0,
        "assistantTextDigest": "assistant:mode-b-general-knowledge-korean-react",
        "toolOutputDigests": (),
    }
    assert snapshots[
        "mode_b_fabricated_english_config_claim_without_read_tool"
    ]["reasonCode"] == "explicit_file_config_claim_without_read_tool"
    assert snapshots[
        "mode_b_fabricated_english_config_claim_without_read_tool"
    ]["verdict"] == "FABRICATED"
    assert snapshots[
        "mode_b_fabricated_korean_script_reference_without_read_tool"
    ]["reasonCode"] == "explicit_file_config_claim_without_read_tool"
    assert snapshots[
        "mode_b_fabricated_korean_script_reference_without_read_tool"
    ]["verdict"] == "FABRICATED"
    assert snapshots["mode_b_grounded_honest_uncertainty"]["reasonCode"] == (
        "honest_uncertainty"
    )
    assert snapshots[
        "mode_b_low_confidence_specific_details_without_file_claim_pattern"
    ]["confidence"] == "low"

    fixture_json = json.dumps(
        fixture.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    projection_json = json.dumps(
        projection.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    assert "assistantText" in fixture_json
    assert "toolOutputs" in fixture_json
    unsafe_fragments = (
        "config.json에 따르면",
        "React는 Virtual DOM",
        "The config uses",
        "gemini-2.5-pro",
        "GPT-4o",
        "temperature",
        "파일을 읽어보니",
        "스크립트에 따르면",
        "3단계로 구성됩니다",
        "총 1500개의",
        "Bearer unsafe",
        "ghp_factsecret",
        "sk-fact-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        '"llmJudgeCalled": true',
        '"promptMutated": true',
        '"hookAttached": true',
        '"blockModeEnabled": true',
        '"adkEvalAttached": true',
        '"adkRunnerInvoked": true',
        '"toolHostDispatched": true',
        '"transcriptRead": true',
        '"sourceFetched": true',
        '"browserExecuted": true',
        '"webSearchExecuted": true',
        '"providerCalled": true',
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"llmJudgeCalled": True}),
            id="fixture-llm-judge-called",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"promptMutated": True},
            ),
            id="case-prompt-mutated",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"adkRunnerInvoked": True},
            ),
            id="nested-adk-runner-invoked",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"toolHostDispatched": True},
            ),
            id="nested-toolhost-dispatched",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"sourceFetched": True},
            ),
            id="nested-source-fetched",
        ),
        pytest.param(
            lambda payload: payload.update({"metadataOnly": False}),
            id="fixture-not-metadata-only",
        ),
        pytest.param(
            lambda payload: payload.update({"defaultOff": False}),
            id="fixture-not-default-off",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"expectedVerdict": "FABRICATED"}),
            id="mode-a-verdict-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"expectedConfidence": "low"}),
            id="mode-a-confidence-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].update({"mode": "A"}),
            id="mode-b-case-in-wrong-mode",
        ),
        pytest.param(
            lambda payload: payload["cases"].pop(8),
            id="missing-mode-b-general-knowledge-case",
        ),
        pytest.param(
            lambda payload: payload["cases"].insert(9, payload["cases"].pop(8)),
            id="mode-b-case-order-changed",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["toolOutputs"][0].update(
                {"rawOutput": "Bearer unsafe"},
            ),
            id="unsafe-raw-tool-output",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"assistantText": "/data/bots/x"}),
            id="unsafe-assistant-text",
        ),
    ),
)
def test_fact_grounding_verifier_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        FactGroundingVerifierFixture.model_validate(payload)


def test_fact_grounding_verifier_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = FactGroundingVerifierAttachmentFlags.model_construct(
        llmJudgeCalled=True,
        promptMutated=True,
        hookAttached=True,
        blockModeEnabled=True,
        adkEvalAttached=True,
        adkRunnerInvoked=True,
        toolHostDispatched=True,
        transcriptRead=True,
        sourceFetched=True,
        browserExecuted=True,
        webSearchExecuted=True,
        providerCalled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"llmJudgeCalled": True})


def test_fact_grounding_verifier_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.fact_grounding_verifier_contract import (
    load_fact_grounding_verifier_fixture,
    project_fact_grounding_verifier_fixture,
)

fixture = load_fact_grounding_verifier_fixture(
    'policy_matrix.json',
    fixture_root=Path('tests/fixtures/fact_grounding_verifier'),
)
project_fact_grounding_verifier_fixture(fixture)

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
    'magi_agent.browser',
    'magi_agent.search',
    'magi_agent.fetch',
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
        / "fact_grounding_verifier_contract.py"
    ).read_text(encoding="utf-8")
    import_lines = "\n".join(
        line for line in source.splitlines() if re.match(r"^(from|import) ", line)
    )
    forbidden_import_fragments = (
        "google.adk",
        "magi_agent.adk_bridge",
        "magi_agent.shadow.toolhost_contract",
        "magi_agent.tools",
        "magi_agent.plugins.agentmemory",
        "magi_agent.memory",
        "magi_agent.hipocampus",
        "magi_agent.qmd",
        "magi_agent.browser",
        "magi_agent.search",
        "magi_agent.fetch",
        "magi_agent.transport.chat",
        "magi_agent.routes",
    )
    for fragment in forbidden_import_fragments:
        assert fragment not in import_lines
