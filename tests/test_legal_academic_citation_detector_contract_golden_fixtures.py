from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.legal_academic_citation_detector_contract import (
    LegalAcademicCitationDetectorAttachmentFlags,
    LegalAcademicCitationDetectorFixture,
    load_legal_academic_citation_detector_fixture,
    project_legal_academic_citation_detector_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "legal_academic_citation_detector"


def test_legal_academic_citation_detector_fixture_projects_ts_signal_parity_metadata() -> None:
    fixture = load_legal_academic_citation_detector_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_legal_academic_citation_detector_fixture(fixture)

    assert projection.fixture_id == "legal_academic_citation_detector_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.metadata_only is True
    assert projection.no_live_execution is True
    assert projection.case_order == (
        "kr_case_number_detects_legal",
        "kr_legal_cue_detects_legal",
        "statute_article_detects_legal",
        "doi_detects_academic",
        "arxiv_detects_academic",
        "kr_case_and_doi_detects_mixed",
        "plain_text_detects_none",
    )
    assert projection.by_classification == {
        "legal": 3,
        "academic": 2,
        "mixed": 1,
        "none": 1,
    }
    assert projection.by_signal == {
        "kr_case_number": 2,
        "kr_legal_cue": 1,
        "statute_article": 1,
        "doi": 2,
        "arxiv": 1,
    }
    assert projection.by_required_source_family == {
        "korean_court": 3,
        "korean_statute": 1,
        "doi": 2,
        "arxiv": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}

    snapshots = projection.case_snapshots
    assert snapshots["kr_case_number_detects_legal"] == {
        "caseId": "kr_case_number_detects_legal",
        "category": "kr_case_number",
        "classification": "legal",
        "detectedSignals": ("kr_case_number",),
        "requiredSourceFamilies": ("korean_court",),
        "metadataOnly": True,
        "localDiagnostic": True,
    }
    assert snapshots["kr_legal_cue_detects_legal"]["detectedSignals"] == (
        "kr_legal_cue",
    )
    assert snapshots["statute_article_detects_legal"]["requiredSourceFamilies"] == (
        "korean_statute",
    )
    assert snapshots["doi_detects_academic"]["detectedSignals"] == ("doi",)
    assert snapshots["arxiv_detects_academic"]["requiredSourceFamilies"] == ("arxiv",)
    assert snapshots["kr_case_and_doi_detects_mixed"]["classification"] == "mixed"
    assert snapshots["kr_case_and_doi_detects_mixed"]["detectedSignals"] == (
        "kr_case_number",
        "doi",
    )
    assert snapshots["plain_text_detects_none"]["detectedSignals"] == ()
    assert snapshots["plain_text_detects_none"]["requiredSourceFamilies"] == ()

    fixture_json = json.dumps(
        fixture.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    projection_json = json.dumps(
        projection.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    assert "promptText" in fixture_json
    unsafe_fragments = (
        "이 문장을 사용자에게 그대로 보여주지 마세요",
        "Bearer unsafe",
        "ghp_citationsecret",
        "sk-citation-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        '"webSearchExecuted": true',
        '"sourceFetched": true',
        '"browserExecuted": true',
        '"toolHostDispatched": true',
        '"evaluationAttached": true',
        '"promptGateAttached": true',
        '"evidenceBlockEnabled": true',
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"webSearchExecuted": True}),
            id="fixture-web-search-executed",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"sourceFetched": True}
            ),
            id="case-source-fetched",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"browserExecuted": True}),
            id="inline-browser-executed",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"toolHostDispatched": True}
            ),
            id="nested-toolhost-dispatched",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"evaluationAttached": True}
            ),
            id="nested-evaluation-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"adkRunnerAttached": True}
            ),
            id="nested-adk-runner-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"agentEvaluatorAttached": True}
            ),
            id="nested-agent-evaluator-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"toolAttached": True}
            ),
            id="nested-tool-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"providerAttached": True}
            ),
            id="nested-provider-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"browserAttached": True}
            ),
            id="nested-browser-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"searchAttached": True}
            ),
            id="nested-search-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"memoryProviderAttached": True}
            ),
            id="nested-memory-provider-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"promptGateAttached": True}
            ),
            id="nested-prompt-gate-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["metadata"].update(
                {"evidenceBlockEnabled": True}
            ),
            id="nested-evidence-block-enabled",
        ),
        pytest.param(
            lambda payload: payload.update({"metadataOnly": False}),
            id="fixture-not-metadata-only",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"classification": "academic"}),
            id="classification-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"expectedSignals": ["doi"]}),
            id="signal-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"requiredSourceFamilies": ["doi"]}
            ),
            id="source-family-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"inputText": "Bearer unsafe"}),
            id="unsafe-secret-text",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"inputText": "/data/bots/x"}),
            id="unsafe-production-path",
        ),
    ),
)
def test_legal_academic_citation_detector_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        LegalAcademicCitationDetectorFixture.model_validate(payload)


def test_legal_academic_citation_detector_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = LegalAcademicCitationDetectorAttachmentFlags.model_construct(
        webSearchExecuted=True,
        sourceFetched=True,
        browserExecuted=True,
        toolHostDispatched=True,
        evaluationAttached=True,
        promptGateAttached=True,
        evidenceBlockEnabled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"webSearchExecuted": True})


def test_legal_academic_citation_detector_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.legal_academic_citation_detector_contract import (
    load_legal_academic_citation_detector_fixture,
    project_legal_academic_citation_detector_fixture,
)

fixture = load_legal_academic_citation_detector_fixture(
    'policy_matrix.json',
    fixture_root=Path('tests/fixtures/legal_academic_citation_detector'),
)
project_legal_academic_citation_detector_fixture(fixture)

forbidden = (
    'google.adk.evaluation',
    'google.adk.runners',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.adk_bridge.tool_adapter',
    'openmagi_core_agent.shadow.toolhost_contract',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.plugins.agentmemory',
    'openmagi_core_agent.memory',
    'openmagi_core_agent.services.memory',
    'openmagi_core_agent.hipocampus',
    'openmagi_core_agent.qmd',
    'openmagi_core_agent.browser',
    'openmagi_core_agent.search',
    'openmagi_core_agent.fetch',
    'openmagi_core_agent.app',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.routes',
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
        / "openmagi_core_agent"
        / "shadow"
        / "legal_academic_citation_detector_contract.py"
    ).read_text(encoding="utf-8")
    import_lines = "\n".join(
        line for line in source.splitlines() if re.match(r"^(from|import) ", line)
    )
    forbidden_import_fragments = (
        "google.adk",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.shadow.toolhost_contract",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.plugins.agentmemory",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.hipocampus",
        "openmagi_core_agent.qmd",
        "openmagi_core_agent.browser",
        "openmagi_core_agent.search",
        "openmagi_core_agent.fetch",
        "openmagi_core_agent.transport.chat",
        "openmagi_core_agent.routes",
    )
    for fragment in forbidden_import_fragments:
        assert fragment not in import_lines
