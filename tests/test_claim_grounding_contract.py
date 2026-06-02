from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.evidence.claim_grounding import (
    AtomicClaim,
    CitationRef,
    SupportStatus,
    validate_claim_projection_eligibility,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def _citation() -> CitationRef:
    return CitationRef(
        sourceRef="source-1",
        snapshotRef="snapshot-1",
        contentDigest="sha256:" + "1" * 64,
        spanRef="source-1-span-2",
        quoteDigest="sha256:" + "2" * 64,
        openedProof=True,
        fetchedAt="2026-05-22T12:00:00Z",
        sourceDate="2026-05-20",
    )


def test_supported_atomic_numeric_claim_can_render_as_fact() -> None:
    claim = AtomicClaim(
        claimId="claim-001",
        text="Revenue grew 18 percent in 2025.",
        claimType="numeric_date",
        supportStatus="supported",
        citationRefs=(_citation(),),
    )

    result = validate_claim_projection_eligibility((claim,))
    assert result.ok is True


def test_weak_or_unverifiable_claim_cannot_render_as_fact() -> None:
    weak = AtomicClaim(
        claimId="claim-002",
        text="The company is probably the market leader.",
        claimType="comparison",
        supportStatus="weak",
        citationRefs=(_citation(),),
    )

    result = validate_claim_projection_eligibility((weak,))
    assert result.ok is False
    assert "unsupported_claim_not_renderable" in result.reason_codes


def test_url_only_citation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="snapshotRef"):
        CitationRef(
            sourceRef="https://example.com/report",
            snapshotRef="",
            contentDigest="sha256:" + "3" * 64,
            spanRef="",
            quoteDigest=None,
            openedProof=False,
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate=None,
        )


def test_compound_claim_requires_split_or_reject() -> None:
    compound = AtomicClaim(
        claimId="claim-003",
        text="Revenue grew 18 percent in 2025 and this caused margin expansion.",
        claimType="compound",
        supportStatus="not_checked",
        citationRefs=(_citation(),),
    )

    result = validate_claim_projection_eligibility((compound,))
    assert result.ok is False
    assert "compound_claim_must_split_or_reject" in result.reason_codes


def test_support_status_values_are_closed() -> None:
    assert set(SupportStatus.__args__) == {
        "supported",
        "weak",
        "unverifiable",
        "contradicted",
        "not_checked",
        "failed",
    }


def test_citation_rejects_coerced_opened_proof_and_unopened_sources() -> None:
    with pytest.raises(ValidationError, match="openedProof"):
        CitationRef(
            sourceRef="source-2",
            snapshotRef="snapshot-2",
            contentDigest="sha256:" + "4" * 64,
            spanRef="source-2-span-1",
            quoteDigest=None,
            openedProof="true",
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate=None,
        )

    claim = AtomicClaim(
        claimId="claim-004",
        text="A cited statement.",
        claimType="other",
        supportStatus="supported",
        citationRefs=(
            CitationRef(
                sourceRef="source-2",
                snapshotRef="snapshot-2",
                contentDigest="sha256:" + "5" * 64,
                spanRef="source-2-span-1",
                quoteDigest=None,
                openedProof=False,
                fetchedAt="2026-05-22T12:00:00Z",
                sourceDate=None,
            ),
        ),
    )

    result = validate_claim_projection_eligibility((claim,))
    assert result.ok is False
    assert "citation_source_not_opened" in result.reason_codes


def test_claim_and_citation_refs_reject_private_or_protected_values() -> None:
    with pytest.raises(ValidationError, match="sourceRef"):
        CitationRef(
            sourceRef="source-to-ken",
            snapshotRef="snapshot-1",
            contentDigest="sha256:" + "6" * 64,
            spanRef="source-1-span-2",
            quoteDigest=None,
            openedProof=True,
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate=None,
        )
    with pytest.raises(ValidationError, match="spanRef"):
        CitationRef(
            sourceRef="source-1",
            snapshotRef="snapshot-1",
            contentDigest="sha256:" + "6" * 64,
            spanRef="source-etc-passwd",
            quoteDigest=None,
            openedProof=True,
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate=None,
        )
    with pytest.raises(ValidationError, match="claimId"):
        AtomicClaim(
            claimId="claim-auth-header",
            text="Safe text.",
            claimType="other",
            supportStatus="supported",
            citationRefs=(_citation(),),
        )


def test_claim_text_and_citation_metadata_reject_private_paths_and_raw_markers() -> None:
    with pytest.raises(ValidationError, match="claim text"):
        AtomicClaim(
            claimId="claim-sensitive-text",
            text="Read /Users/example/.ssh/id_rsa for the key.",
            claimType="other",
            supportStatus="supported",
            citationRefs=(_citation(),),
        )
    with pytest.raises(ValidationError, match="claim text"):
        AtomicClaim(
            claimId="claim-workspace-text",
            text="Config is at /workspace/app/.env.",
            claimType="other",
            supportStatus="supported",
            citationRefs=(_citation(),),
        )
    with pytest.raises(ValidationError, match="fetchedAt"):
        CitationRef(
            sourceRef="source-1",
            snapshotRef="snapshot-1",
            contentDigest="sha256:" + "6" * 64,
            spanRef="source-1-span-2",
            quoteDigest=None,
            openedProof=True,
            fetchedAt="2026-05-22T12:00:00Z " + "to" + "ken=redacted",
            sourceDate=None,
        )
    with pytest.raises(ValidationError, match="sourceDate"):
        CitationRef(
            sourceRef="source-1",
            snapshotRef="snapshot-1",
            contentDigest="sha256:" + "6" * 64,
            spanRef="source-1-span-2",
            quoteDigest=None,
            openedProof=True,
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate="/Users/example/.ssh/id_rsa",
        )
    with pytest.raises(ValidationError, match="sourceDate"):
        CitationRef(
            sourceRef="source-1",
            snapshotRef="snapshot-1",
            contentDigest="sha256:" + "6" * 64,
            spanRef="source-1-span-2",
            quoteDigest=None,
            openedProof=True,
            fetchedAt="2026-05-22T12:00:00Z",
            sourceDate="hidden reasoning raw child log",
        )


def test_claim_projection_model_copy_update_is_disabled() -> None:
    claim = AtomicClaim(
        claimId="claim-005",
        text="Revenue grew 18 percent in 2025.",
        claimType="numeric_date",
        supportStatus="supported",
        citationRefs=(_citation(),),
    )
    result = validate_claim_projection_eligibility((claim,))

    with pytest.raises(ValueError, match="model_copy update"):
        claim.model_copy(update={"supportStatus": "weak"})
    with pytest.raises(ValueError, match="model_copy update"):
        result.model_copy(update={"ok": False})


def test_claim_grounding_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "claim_grounding.json").read_text())
    claims = tuple(AtomicClaim.model_validate(item) for item in payload["claims"])
    result = validate_claim_projection_eligibility(claims)

    assert result.model_dump(by_alias=True, mode="json") == payload["expectedEligibility"]
    encoded_values = " ".join(_string_values(payload)).lower()
    forbidden_fragments = (
        "pro" + "mpt",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
        "/users/",
        ".env",
    )
    assert all(fragment not in encoded_values for fragment in forbidden_fragments)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
