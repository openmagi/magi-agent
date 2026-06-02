from __future__ import annotations

import copy
import importlib
import inspect
import json

import pytest
from pydantic import ValidationError

from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    ResearchSourceProofVerdict,
    project_research_source_proof_verdicts,
    verify_research_source_proof,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _source_ref(
    source_ref_id: str = "src_1",
    *,
    source_kind: str = "web_fetch",
    receipt_kind: str = "opened_snapshot",
    opened: bool = True,
    content_digest: str | None = None,
    inspected_at: str = "2026-05-26T12:00:00Z",
    span_refs: tuple[str, ...] = ("span:pricing",),
    redaction_status: str = "redacted",
    public_label: str = "Example docs",
) -> ResearchSourceOpenReceiptRef:
    return ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-source-proof",
            scopes=("research_source_proof",),
        ),
        source_ref_id=source_ref_id,
        source_kind=source_kind,
        receipt_kind=receipt_kind,
        opened=opened,
        content_digest=content_digest or _digest(),
        inspected_at=inspected_at,
        span_refs=span_refs,
        redaction_status=redaction_status,
        public_label=public_label,
    )


def test_runtime_source_ref_factory_requires_runtime_issue_authority() -> None:
    with pytest.raises(RuntimeError, match="runtime issue authority"):
        ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
            source_ref_id="src_1",
            source_kind="web_fetch",
            receipt_kind="opened_snapshot",
            opened=True,
            content_digest=_digest(),
            inspected_at="2026-05-26T12:00:00Z",
            span_refs=("span:pricing",),
            redaction_status="redacted",
            public_label="Example docs",
        )


def _requirement(
    source_ref_id: str = "src_1",
    *,
    allowed_source_kinds: tuple[str, ...] = ("web_fetch",),
    required_receipt_kinds: tuple[str, ...] = ("opened_snapshot",),
    required_span_refs: tuple[str, ...] = ("span:pricing",),
    not_before: str | None = "2026-05-26T10:00:00Z",
    not_after: str | None = "2026-05-26T13:00:00Z",
) -> ResearchSourceProofRequirement:
    return ResearchSourceProofRequirement(
        sourceRefId=source_ref_id,
        allowedSourceKinds=allowed_source_kinds,
        requiredReceiptKinds=required_receipt_kinds,
        requiredSpanRefs=required_span_refs,
        notBefore=not_before,
        notAfter=not_after,
    )


@pytest.mark.parametrize(
    ("source_kind", "receipt_kind", "span_refs"),
    (
        ("web_fetch", "opened_snapshot", ("span:pricing", "span:changed")),
        ("file", "local_document_read", ("span:local-report",)),
    ),
)
def test_opened_snapshot_or_local_document_read_satisfies_source_proof(
    source_kind: str,
    receipt_kind: str,
    span_refs: tuple[str, ...],
) -> None:
    receipt = _source_ref(
        source_kind=source_kind,
        receipt_kind=receipt_kind,
        span_refs=span_refs,
    )
    requirement = _requirement(
        allowed_source_kinds=(source_kind,),
        required_receipt_kinds=(receipt_kind,),
        required_span_refs=span_refs,
    )

    verdicts = verify_research_source_proof((requirement,), (receipt,))
    projection = project_research_source_proof_verdicts(verdicts)
    dumped = json.dumps(projection, sort_keys=True)

    assert verdicts[0].verdict == "allowed"
    assert verdicts[0].reason_code == "source_match"
    assert verdicts[0].matched_source_refs == ("src_1",)
    assert projection[0]["projectedText"] == "source verified: src_1"
    assert projection[0]["sourceKind"] == source_kind
    assert projection[0]["contentDigest"] == receipt.content_digest
    assert "raw" not in dumped.lower()
    assert "Authorization" not in dumped
    assert "Bearer " not in dumped


@pytest.mark.parametrize(
    "source_ref_id",
    (
        "https://example.test/pricing",
        "Competitor Pricing Page",
        "src_0",
        "source:pricing",
    ),
)
def test_url_only_and_model_invented_source_names_are_rejected(
    source_ref_id: str,
) -> None:
    with pytest.raises(ValidationError):
        _requirement(source_ref_id=source_ref_id)


def test_unopened_source_ref_is_denied() -> None:
    receipt = _source_ref(receipt_kind="discovered_source", opened=False)
    verdicts = verify_research_source_proof((_requirement(),), (receipt,))

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "unopened_source"
    assert verdicts[0].matched_source_refs == ()
    assert verdicts[0].projected_text == "source not verified: src_1"


def test_stale_snapshot_beyond_freshness_policy_is_denied() -> None:
    receipt = _source_ref(inspected_at="2026-05-25T12:00:00Z")

    verdicts = verify_research_source_proof((_requirement(),), (receipt,))

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "stale_source"


def test_missing_freshness_window_allows_source_but_marks_freshness_unchecked() -> None:
    receipt = _source_ref()
    requirement = _requirement(not_before=None, not_after=None)

    verdicts = verify_research_source_proof((requirement,), (receipt,))
    projection = project_research_source_proof_verdicts(verdicts)

    assert verdicts[0].verdict == "allowed"
    assert verdicts[0].freshness_verdict == "not_checked"
    assert projection[0]["freshnessVerdict"] == "not_checked"


def test_source_ref_collision_or_spoofing_is_denied() -> None:
    receipt = _source_ref(content_digest=_digest("a"))
    spoofed = _source_ref(content_digest=_digest("b"))

    verdicts = verify_research_source_proof((_requirement(),), (receipt, spoofed))

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "source_ref_collision"
    assert verdicts[0].matched_source_refs == ()


@pytest.mark.parametrize(
    "payload",
    (
        {
            "sourceRefId": "src_1",
            "sourceKind": "web_fetch",
            "receiptKind": "opened_snapshot",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "opened": True,
            "contentDigest": _digest("b"),
            "inspectedAt": "2026-05-26T12:00:00Z",
            "spanRefs": ("span:pricing",),
            "redactionStatus": "redacted",
            "sourceBody": "private source body",
            "digest": _digest("c"),
        },
        {
            "sourceRefId": "src_1",
            "sourceKind": "web_fetch",
            "receiptKind": "opened_snapshot",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "opened": True,
            "contentDigest": _digest("d"),
            "inspectedAt": "2026-05-26T12:00:00Z",
            "spanRefs": ("span:pricing",),
            "redactionStatus": "redacted",
            "publicLabel": "Authorization: Bearer " + "unsafe-token",
            "digest": _digest("e"),
        },
        {
            "sourceRefId": "src_1",
            "sourceKind": "web_fetch",
            "receiptKind": "opened_snapshot",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "opened": True,
            "contentDigest": _digest("f"),
            "inspectedAt": "2026-05-26T12:00:00Z",
            "spanRefs": ("span:pricing",),
            "redactionStatus": "raw",
            "digest": _digest("9"),
        },
    ),
)
def test_raw_source_private_auth_or_unredacted_status_is_rejected(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResearchSourceOpenReceiptRef.model_validate(payload)


def test_public_projection_does_not_include_raw_source_or_private_data() -> None:
    receipt = _source_ref(public_label="Public docs")
    verdicts = verify_research_source_proof((_requirement(),), (receipt,))
    projection = project_research_source_proof_verdicts(verdicts)
    dumped = json.dumps(projection, sort_keys=True)

    assert "Public docs" not in dumped
    assert "raw" not in dumped.lower()
    assert "source body" not in dumped
    assert "http" not in dumped.lower()
    assert "/Users/" not in dumped
    assert "Bearer " not in dumped
    assert projection[0]["matchedSourceRefs"] == ("src_1",)


def test_mapping_or_copied_source_refs_cannot_verify() -> None:
    receipt = _source_ref()
    payload = receipt.model_dump(by_alias=True, mode="python", warnings=False)
    mutated_receipt = _source_ref()
    mutated_receipt.__dict__["content_digest"] = _digest("b")

    with pytest.raises(TypeError, match="runtime-issued source ref objects"):
        verify_research_source_proof((_requirement(),), (payload,))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_source_proof((_requirement(),), (receipt.model_copy(),))

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_source_proof((_requirement(),), (copy.copy(receipt),))

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_source_proof((_requirement(),), (copy.deepcopy(receipt),))

    with pytest.raises(ValueError, match="modified after runtime issuance"):
        verify_research_source_proof((_requirement(),), (mutated_receipt,))


def test_projection_rejects_mapping_or_externally_constructed_allowed_verdict() -> None:
    requirement = _requirement()
    forged = ResearchSourceProofVerdict(
        sourceRefId="src_1",
        sourceKind="web_fetch",
        verdict="allowed",
        reasonCode="source_match",
        freshnessVerdict="current",
        matchedSourceRefs=("src_1",),
        contentDigest=_digest(),
        spanRefs=("span:pricing",),
        projectedText="source verified: src_1",
        requirement=requirement,
    )
    verified = verify_research_source_proof((requirement,), (_source_ref(),))[0]
    mutated_verdict = verify_research_source_proof((requirement,), (_source_ref(),))[0]
    mutated_verdict.__dict__["projected_text"] = (
        "Authorization: Bearer " + "unsafe-token"
    )

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_source_proof_verdicts((forged,))

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_source_proof_verdicts((copy.copy(verified),))

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_source_proof_verdicts((copy.deepcopy(verified),))

    with pytest.raises(ValueError, match="modified after verifier issuance"):
        project_research_source_proof_verdicts((mutated_verdict,))

    with pytest.raises(TypeError, match="verifier-issued source verdict objects"):
        project_research_source_proof_verdicts(
            (forged.model_dump(by_alias=True, mode="python", warnings=False),)
        )  # type: ignore[arg-type]


def test_default_off_local_only_fake_provider_posture_is_projected() -> None:
    verdict = verify_research_source_proof((_requirement(),), ())[0]
    projection = project_research_source_proof_verdicts((verdict,))

    assert projection[0]["executionPosture"] == {
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
        "liveExecutionAllowed": False,
        "providerCallsAllowed": False,
        "browserExecutionAllowed": False,
        "toolExecutionAllowed": False,
        "channelDeliveryAllowed": False,
        "userVisiblePythonActivation": False,
    }
    assert projection[0]["adkUsageNotes"] == (
        "Metadata only; no ADK Runner, ArtifactService, or FunctionTool is attached."
    )


def test_research_source_proof_stays_in_research_layer_without_live_adk_imports() -> None:
    module = importlib.import_module("magi_agent.research.source_proof")
    source = inspect.getsource(module)

    assert module.__name__ == "magi_agent.research.source_proof"
    assert ResearchSourceOpenReceiptRef.__module__.startswith("magi_agent.research")
    forbidden_imports = (
        "from google.adk",
        "import google.adk",
        "from magi_agent.runtime",
        "import magi_agent.runtime",
        "from magi_agent.tools",
        "import magi_agent.tools",
        "from magi_agent.transport",
        "import magi_agent.transport",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source
