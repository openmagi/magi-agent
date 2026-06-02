from __future__ import annotations

import copy
import importlib
import inspect
import json

import pytest
from pydantic import ValidationError

from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.research.action_claims import (
    ResearchActionClaim,
    ResearchActionProofReceiptRef,
    ResearchActionProofRequirement,
    ResearchActionProofVerdict,
    detect_research_action_claims,
    project_research_action_proof_verdicts,
    verify_research_action_claims,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _receipt(
    receipt_id: str,
    action_verb: str,
    *,
    receipt_kind: str = "toolhost_receipt",
    tool_id: str = "tool:web-search",
    source_id: str = "source:official-docs",
    observed_at: str = "2026-05-26T12:00:00Z",
    public_label: str = "Official docs receipt",
) -> ResearchActionProofReceiptRef:
    return ResearchActionProofReceiptRef.issue_runtime_receipt(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-action-proof",
            scopes=("research_action_proof",),
        ),
        receipt_id=receipt_id,
        action_verb=action_verb,
        receipt_kind=receipt_kind,
        tool_id=tool_id,
        source_id=source_id,
        observed_at=observed_at,
        public_label=public_label,
    )


def test_runtime_action_receipt_factory_requires_runtime_issue_authority() -> None:
    with pytest.raises(RuntimeError, match="runtime issue authority"):
        ResearchActionProofReceiptRef.issue_runtime_receipt(
            receipt_id="receipt:searched:1",
            action_verb="searched",
            receipt_kind="toolhost_receipt",
            tool_id="tool:web-search",
            source_id="source:official-docs",
            observed_at="2026-05-26T12:00:00Z",
            public_label="Official docs receipt",
        )


@pytest.mark.parametrize(
    ("verb", "text"),
    (
        ("searched", "I searched the official docs before writing the summary."),
        ("read", "We read the pricing page and then drafted the answer."),
        ("reviewed", "I reviewed the public release notes for the timeline."),
        ("compared", "After comparing the two public pages, the date changed."),
        ("checked", "We checked the source metadata for freshness."),
        ("confirmed", "I confirmed the quoted number against a receipt."),
        ("verified", "We verified the current plan name from the source."),
        ("analyzed", "I analyzed the table before summarizing it."),
        ("summarized", "The agent summarized the inspected source metadata."),
    ),
)
def test_detector_finds_supported_explicit_action_claim_verbs(
    verb: str,
    text: str,
) -> None:
    claims = detect_research_action_claims(text)

    assert len(claims) == 1
    assert claims[0].action_verb == verb
    assert claims[0].claim_text == text
    assert claims[0].claim_id == f"claim:1:{verb}"


def test_detector_supports_agent_inspected_form_and_avoids_plain_factual_sentences() -> None:
    text = (
        "OpenMagi has a public pricing page. "
        "The agent inspected the pricing source metadata. "
        "The release happened on May 1."
    )

    claims = detect_research_action_claims(text)

    assert [claim.action_verb for claim in claims] == ["inspected"]
    assert claims[0].claim_text == "The agent inspected the pricing source metadata."


def test_detector_emits_each_coordinated_action_claim_in_a_sentence() -> None:
    claims = detect_research_action_claims(
        "I searched and reviewed the official docs. "
        "We checked and confirmed the metadata."
    )

    assert [claim.action_verb for claim in claims] == [
        "searched",
        "reviewed",
        "checked",
        "confirmed",
    ]
    assert [claim.claim_id for claim in claims] == [
        "claim:1:searched",
        "claim:2:reviewed",
        "claim:3:checked",
        "claim:4:confirmed",
    ]


def test_absent_matching_receipts_deny_and_downgrade_action_claims() -> None:
    claims = detect_research_action_claims("I reviewed the docs. We checked the source.")

    verdicts = verify_research_action_claims(claims, receipts=())
    projection = project_research_action_proof_verdicts(verdicts)

    assert [verdict.verdict for verdict in verdicts] == ["denied", "denied"]
    assert [verdict.projected_text for verdict in verdicts] == [
        "not verified: reviewed",
        "not verified: checked",
    ]
    assert all(verdict.matched_receipt_refs == () for verdict in verdicts)
    assert json.dumps(projection, sort_keys=True).count("not verified:") == 2


def test_matching_receipt_with_correct_tool_source_and_time_allows_claim() -> None:
    claim = detect_research_action_claims("I checked the pricing source.")[0]
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="checked",
        requiredReceiptKinds=("toolhost_receipt", "source_receipt"),
        requiredToolIds=("tool:web-search",),
        requiredSourceIds=("source:pricing-page",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )
    receipt = _receipt(
        "receipt:checked-1",
        "checked",
        source_id="source:pricing-page",
        observed_at="2026-05-26T12:00:00Z",
    )

    verdicts = verify_research_action_claims(
        (claim,),
        (receipt,),
        requirements=(requirement,),
    )

    assert len(verdicts) == 1
    assert verdicts[0].verdict == "allowed"
    assert verdicts[0].projected_text == "verified: checked"
    assert verdicts[0].matched_receipt_refs == ("receipt:checked-1",)


def test_receipt_without_strict_tool_source_time_requirement_is_not_enough() -> None:
    claim = detect_research_action_claims("I reviewed the official docs.")[0]
    receipt = _receipt("receipt:reviewed-default", "reviewed")

    verdicts = verify_research_action_claims((claim,), (receipt,))

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "receipt_mismatch"
    assert verdicts[0].projected_text == "not verified: reviewed"


def test_stale_or_future_receipt_requires_explicit_time_window_match() -> None:
    claim = detect_research_action_claims("We checked the source metadata.")[0]
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="checked",
        requiredReceiptKinds=("toolhost_receipt",),
        requiredToolIds=("tool:web-search",),
        requiredSourceIds=("source:official-docs",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )

    verdicts = verify_research_action_claims(
        (claim,),
        (
            _receipt(
                "receipt:stale",
                "checked",
                observed_at="2026-05-25T12:00:00Z",
            ),
            _receipt(
                "receipt:future",
                "checked",
                observed_at="2026-05-27T12:00:00Z",
            ),
        ),
        requirements=(requirement,),
    )

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "receipt_mismatch"


def test_wrong_tool_source_or_time_does_not_satisfy_action_claim_proof() -> None:
    claim = detect_research_action_claims("We verified the source.")[0]
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="verified",
        requiredReceiptKinds=("toolhost_receipt",),
        requiredToolIds=("tool:browser",),
        requiredSourceIds=("source:official",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )

    verdicts = verify_research_action_claims(
        (claim,),
        (
            _receipt(
                "receipt:wrong-tool",
                "verified",
                tool_id="tool:web-search",
                source_id="source:official",
            ),
            _receipt(
                "receipt:wrong-source",
                "verified",
                tool_id="tool:browser",
                source_id="source:blog",
            ),
            _receipt(
                "receipt:wrong-time",
                "verified",
                tool_id="tool:browser",
                source_id="source:official",
                observed_at="2026-05-26T09:59:59Z",
            ),
        ),
        requirements=(requirement,),
    )

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].matched_receipt_refs == ()
    assert verdicts[0].projected_text == "not verified: verified"


def test_mismatched_requirement_denies_without_allowing_receipt_substitution() -> None:
    claim = detect_research_action_claims("I searched the official docs.")[0]
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="read",
        requiredReceiptKinds=("toolhost_receipt",),
    )
    receipt = _receipt("receipt:searched-1", "searched")

    verdicts = verify_research_action_claims(
        (claim,),
        (receipt,),
        requirements=(requirement,),
    )

    assert verdicts[0].verdict == "denied"
    assert verdicts[0].reason_code == "requirement_mismatch"
    assert verdicts[0].matched_receipt_refs == ()
    assert verdicts[0].projected_text == "not verified: searched"


def test_unknown_requirement_claim_id_is_rejected() -> None:
    claim = detect_research_action_claims("I searched the official docs.")[0]
    requirement = ResearchActionProofRequirement(
        claimId="claim:other:read",
        requiredActionVerb="read",
        requiredReceiptKinds=("toolhost_receipt",),
    )

    with pytest.raises(ValueError, match="unknown claimId"):
        verify_research_action_claims((claim,), receipts=(), requirements=(requirement,))


def test_verifier_rejects_mapping_or_non_runtime_issued_receipts() -> None:
    claim = detect_research_action_claims("I checked the pricing source.")[0]
    receipt = _receipt("receipt:checked-runtime", "checked")
    receipt_payload = receipt.model_dump(by_alias=True, mode="python", warnings=False)
    copied_receipt = receipt.model_copy(update={"publicLabel": "Copied receipt"})
    shallow_copy_receipt = copy.copy(receipt)
    deep_copy_receipt = copy.deepcopy(receipt)

    with pytest.raises(TypeError, match="runtime-issued receipt objects"):
        verify_research_action_claims((claim,), (receipt_payload,))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_action_claims((claim,), (copied_receipt,))

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_action_claims((claim,), (shallow_copy_receipt,))

    with pytest.raises(ValueError, match="runtime boundary"):
        verify_research_action_claims((claim,), (deep_copy_receipt,))


def test_verifier_rejects_runtime_receipt_mutated_after_issuance() -> None:
    claim = detect_research_action_claims("I checked the pricing source.")[0]
    receipt = _receipt(
        "receipt:checked-mutated",
        "checked",
        tool_id="tool:other",
        source_id="source:other",
        observed_at="2026-05-27T09:00:00Z",
    )
    receipt.__dict__["tool_id"] = "tool:web-search"
    receipt.__dict__["source_id"] = "source:pricing-page"
    receipt.__dict__["observed_at"] = "2026-05-27T12:00:00Z"
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="checked",
        requiredReceiptKinds=("toolhost_receipt",),
        requiredToolIds=("tool:web-search",),
        requiredSourceIds=("source:pricing-page",),
        notBefore="2026-05-27T10:00:00Z",
        notAfter="2026-05-27T13:00:00Z",
    )

    with pytest.raises(ValueError, match="modified after runtime issuance"):
        verify_research_action_claims((claim,), (receipt,), requirements=(requirement,))


def test_verdict_model_rejects_inconsistent_reason_codes() -> None:
    requirement = ResearchActionProofRequirement(
        claimId="claim:1:searched",
        requiredActionVerb="searched",
        requiredReceiptKinds=("toolhost_receipt",),
    )

    with pytest.raises(ValidationError):
        ResearchActionProofVerdict(
            claimId="claim:1:searched",
            actionVerb="searched",
            verdict="allowed",
            reasonCode="receipt_mismatch",
            matchedReceiptRefs=("receipt:searched-1",),
            projectedText="verified: searched",
            requirement=requirement,
        )

    with pytest.raises(ValidationError):
        ResearchActionProofVerdict(
            claimId="claim:1:searched",
            actionVerb="searched",
            verdict="denied",
            reasonCode="receipt_match",
            matchedReceiptRefs=(),
            projectedText="not verified: searched",
            requirement=requirement,
        )

    with pytest.raises(ValidationError):
        ResearchActionProofVerdict(
            claimId="claim:1:searched",
            actionVerb="searched",
            verdict="denied",
            reasonCode="requirement_mismatch",
            matchedReceiptRefs=(),
            projectedText="not verified: searched",
            requirement=requirement,
        )


def test_projection_rejects_mapping_or_externally_constructed_allowed_verdict() -> None:
    requirement = ResearchActionProofRequirement(
        claimId="claim:1:searched",
        requiredActionVerb="searched",
        requiredReceiptKinds=("toolhost_receipt",),
    )
    forged_verdict = ResearchActionProofVerdict(
        claimId="claim:1:searched",
        actionVerb="searched",
        verdict="allowed",
        reasonCode="receipt_match",
        matchedReceiptRefs=("receipt:searched-1",),
        projectedText="verified: searched",
        requirement=requirement,
    )
    verified_verdict = verify_research_action_claims(
        (
            ResearchActionClaim(
                claimId="claim:2:searched",
                actionVerb="searched",
                claimText="I searched the public docs.",
                sentenceIndex=0,
            ),
        ),
        (
            _receipt(
                "receipt:searched-1",
                "searched",
            ),
        ),
        requirements=(
            ResearchActionProofRequirement(
                claimId="claim:2:searched",
                requiredActionVerb="searched",
                requiredReceiptKinds=("toolhost_receipt",),
                requiredToolIds=("tool:web-search",),
                requiredSourceIds=("source:official-docs",),
                notBefore="2026-05-26T10:00:00Z",
                notAfter="2026-05-26T13:00:00Z",
            ),
        ),
    )[0]

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_action_proof_verdicts((forged_verdict,))

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_action_proof_verdicts((copy.copy(verified_verdict),))

    with pytest.raises(ValueError, match="issued by the verifier"):
        project_research_action_proof_verdicts((copy.deepcopy(verified_verdict),))

    with pytest.raises(TypeError, match="verifier-issued verdict objects"):
        project_research_action_proof_verdicts(
            (
                forged_verdict.model_dump(
                    by_alias=True,
                    mode="python",
                    warnings=False,
                ),
            )
        )  # type: ignore[arg-type]


def test_projection_is_metadata_only_and_does_not_leak_raw_prompt_output_source_data() -> None:
    claim = ResearchActionClaim(
        claimId="claim:1:reviewed",
        actionVerb="reviewed",
        claimText="I reviewed the public docs.",
        sentenceIndex=0,
    )
    receipt = _receipt(
        "receipt:reviewed-1",
        "reviewed",
        public_label="Public documentation receipt",
    )
    requirement = ResearchActionProofRequirement(
        claimId=claim.claim_id,
        requiredActionVerb="reviewed",
        requiredReceiptKinds=("toolhost_receipt",),
        requiredToolIds=("tool:web-search",),
        requiredSourceIds=("source:official-docs",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )
    verdicts = verify_research_action_claims((claim,), (receipt,), requirements=(requirement,))
    projection = project_research_action_proof_verdicts(verdicts)
    dumped = json.dumps(projection, sort_keys=True)

    assert "I reviewed the public docs" not in dumped
    assert "raw" not in dumped.lower()
    assert "prompt" not in dumped.lower()
    assert "output" not in dumped.lower()
    assert "source data" not in dumped.lower()
    assert "/Users/" not in dumped
    assert "Bearer " not in dumped
    assert projection[0]["matchedReceiptRefs"] == ("receipt:reviewed-1",)
    assert projection[0]["projectedText"] == "verified: reviewed"


@pytest.mark.parametrize(
    "payload",
    (
        {
            "receiptId": "receipt:raw-field",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "source:docs",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("b"),
            "rawSourceText": "private body",
        },
        {
            "receiptId": "receipt:private-path",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "/Users/kevin/private.txt",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("c"),
        },
        {
            "receiptId": "receipt:secret",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "source:docs",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("d"),
            "publicLabel": "Authorization: Bearer unsafe-token",
        },
        {
            "receiptId": "receipt:model-summary",
            "actionVerb": "read",
            "receiptKind": "model_summary",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "source:docs",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("e"),
        },
        {
            "receiptId": "receipt:unsafe-action",
            "actionVerb": "browse_live_web",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "source:docs",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("f"),
        },
    ),
)
def test_receipt_refs_reject_raw_private_auth_model_summary_and_unsafe_action_inputs(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResearchActionProofReceiptRef.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "receiptId": "receipt:github-pat",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "source:docs",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("1"),
            "publicLabel": "github_pat_unsafeunsafeunsafe",
        },
        {
            "receiptId": "receipt:aws-key",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "AKIA" + "IOSFODNN7EXAMPLE",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("2"),
        },
        {
            "receiptId": "receipt:google-key",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "AIzaSyDUMMYDUMMYDUMMY",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("3"),
        },
        {
            "receiptId": "receipt:home-path",
            "actionVerb": "read",
            "receiptKind": "toolhost_receipt",
            "runtimeIssued": True,
            "receiptAuthority": "openmagi_runtime_boundary",
            "toolHostMediated": True,
            "toolId": "tool:web-search",
            "sourceId": "/home/service/private.txt",
            "observedAt": "2026-05-26T12:00:00Z",
            "digest": _digest("4"),
        },
    ),
)
def test_receipt_refs_reject_common_private_auth_tokens_and_paths(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResearchActionProofReceiptRef.model_validate(payload)


@pytest.mark.parametrize(
    "update",
    (
        {"runtimeIssued": False},
        {"receiptAuthority": "model_written_receipt"},
        {"toolHostMediated": False},
        {"observedAt": "2026-05-26T12:00:00"},
    ),
)
def test_receipt_refs_require_runtime_boundary_metadata_and_timezone(
    update: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "receiptId": "receipt:runtime-boundary",
        "actionVerb": "read",
        "receiptKind": "toolhost_receipt",
        "runtimeIssued": True,
        "receiptAuthority": "openmagi_runtime_boundary",
        "toolHostMediated": True,
        "toolId": "tool:web-search",
        "sourceId": "source:docs",
        "observedAt": "2026-05-26T12:00:00Z",
        "digest": _digest("9"),
    }
    payload.update(update)

    with pytest.raises(ValidationError):
        ResearchActionProofReceiptRef.model_validate(payload)


def test_receipt_refs_reject_missing_runtime_boundary_metadata() -> None:
    with pytest.raises(ValidationError):
        ResearchActionProofReceiptRef.model_validate(
            {
                "receiptId": "receipt:missing-runtime-boundary",
                "actionVerb": "read",
                "receiptKind": "toolhost_receipt",
                "toolId": "tool:web-search",
                "sourceId": "source:docs",
                "observedAt": "2026-05-26T12:00:00Z",
                "digest": _digest("8"),
            }
        )


def test_model_construct_and_model_copy_cannot_bypass_receipt_validation() -> None:
    with pytest.raises(TypeError):
        ResearchActionProofReceiptRef.model_construct(
            receiptId="receipt:unsafe",
            actionVerb="read",
            receiptKind="toolhost_receipt",
            toolId="tool:web-search",
            sourceId="/Users/kevin/private.txt",
            observedAt="2026-05-26T12:00:00Z",
            digest="not-a-digest",
        )

    safe = _receipt("receipt:safe", "read")
    with pytest.raises(ValidationError):
        safe.model_copy(update={"digest": "not-a-digest"})


def test_research_action_claims_stay_in_research_layer_without_generic_core_policy_imports() -> None:
    module = importlib.import_module("magi_agent.research.action_claims")
    source = inspect.getsource(module)
    source_for_layer_check = source.replace(
        "from magi_agent.evidence.runtime_issuance import (",
        "from allowed_domain_neutral_runtime_issuance import (",
    )

    assert module.__name__ == "magi_agent.research.action_claims"
    assert ResearchActionClaim.__module__.startswith("magi_agent.research")
    assert "runtime_issuance" in source
    forbidden_imports = (
        "from magi_agent.evidence",
        "import magi_agent.evidence",
        "from magi_agent.harness",
        "import magi_agent.harness",
        "from magi_agent.runtime",
        "import magi_agent.runtime",
        "from magi_agent.tools",
        "import magi_agent.tools",
        "from google.adk",
        "import google.adk",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source_for_layer_check


def test_default_off_local_only_fake_provider_no_live_semantics_are_projected() -> None:
    claim = detect_research_action_claims("I searched the public docs.")[0]
    verdict = verify_research_action_claims((claim,), receipts=())[0]
    projection = project_research_action_proof_verdicts((verdict,))

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
        "Metadata only; no ADK Runner or FunctionTool is attached."
    )
