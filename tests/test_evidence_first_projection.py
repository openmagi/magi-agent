from __future__ import annotations

import json

from magi_agent.runtime.evidence_first_projection import (
    EvidenceFirstProjection,
    EvidenceFirstProjectionRequest,
)


def test_projection_exposes_sources_tools_validators_and_not_chain_of_thought() -> None:
    projection = EvidenceFirstProjection().project(
        EvidenceFirstProjectionRequest(
            openedSourceRefs=("source:web:src_1",),
            toolEvidenceRefs=("evidence:calculation:1",),
            validatorRefs=("validator:research:fact-grounding",),
            approvalRefs=("approval:tool:1",),
            hiddenReasoning="I secretly guessed the answer",
        )
    )

    public = projection.public_projection()
    assert public["openedSourceRefs"] == ["source:web:src_1"]
    assert public["toolEvidenceRefs"] == ["evidence:calculation:1"]
    assert "secretly guessed" not in str(public)
    assert "chainOfThought" not in public


def test_projection_exposes_files_pages_tests_calculations_and_uncertainty() -> None:
    projection = EvidenceFirstProjection().project(
        EvidenceFirstProjectionRequest(
            openedSourceRefs=("source:web:src_1",),
            openedFileRefs=("file:repo:README.md",),
            openedPageRefs=("page:browser:1",),
            toolEvidenceRefs=("evidence:tool:1",),
            testEvidenceRefs=("evidence:test:1",),
            calculationEvidenceRefs=("evidence:calc:1",),
            validatorRefs=("validator:research:fact-grounding",),
            validatorStatuses={"validator:research:fact-grounding": "passed"},
            approvalRefs=("approval:tool:1",),
            coverage="partial",
            confidenceLabel="medium",
            uncertaintyReason="missing_secondary_source",
        )
    )

    public = projection.public_projection()
    assert public["openedFileRefs"] == ["file:repo:README.md"]
    assert public["openedPageRefs"] == ["page:browser:1"]
    assert public["testEvidenceRefs"] == ["evidence:test:1"]
    assert public["calculationEvidenceRefs"] == ["evidence:calc:1"]
    assert public["validatorStatuses"] == {"validator:research:fact-grounding": "passed"}
    assert public["coverage"] == "partial"
    assert public["confidenceLabel"] == "medium"
    assert public["uncertaintyReason"] == "missing_secondary_source"


def test_projection_drops_raw_child_tool_browser_payloads_and_private_refs() -> None:
    projection = EvidenceFirstProjection().project(
        EvidenceFirstProjectionRequest(
            openedSourceRefs=(
                "source:web:src_1",
                "/Users/kevin/private/source",
                "source:web:github_pat_unsafeToken12345",
            ),
            toolEvidenceRefs=("evidence:tool:1", "Bearer raw-tool-ref"),
            rawToolLogs="Authorization: Bearer unsafe-token",
            rawChildTranscript="raw child transcript with hidden reasoning",
            rawBrowserSnapshot="<html>private browser snapshot</html>",
            hiddenReasoning="chain of thought",
        )
    )
    encoded = json.dumps(projection.public_projection(), sort_keys=True)

    assert "source:web:src_1" in encoded
    assert "/Users/kevin" not in encoded
    assert "github_pat_" not in encoded
    assert "unsafe-token" not in encoded
    assert "raw child transcript" not in encoded
    assert "private browser snapshot" not in encoded
    assert "chain of thought" not in encoded
    assert "rawToolLogs" not in encoded


def test_projection_drops_password_credential_api_key_home_and_kubelet_refs() -> None:
    projection = EvidenceFirstProjection().project(
        EvidenceFirstProjectionRequest(
            openedSourceRefs=(
                "source:web:src_1",
                "source:web:password123",
                "source:web:credential123",
                "source:web:api_key123",
                "/home/kevin/.ssh/id_rsa",
                "/var/lib/kubelet/pods/x",
            ),
            toolEvidenceRefs=("evidence:tool:1", "evidence:credential:unsafe"),
        )
    )
    public = projection.public_projection()
    encoded = json.dumps(public, sort_keys=True)

    assert public["openedSourceRefs"] == ["source:web:src_1"]
    assert public["toolEvidenceRefs"] == ["evidence:tool:1"]
    assert "password" not in encoded
    assert "credential:unsafe" not in encoded
    assert "api_key" not in encoded
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded
