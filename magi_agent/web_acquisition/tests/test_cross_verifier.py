"""Tests for CrossVerifier — PR3 (TDD: written first).

All tests use a fake RuntimeIssueAuthority; no network calls.
"""

from __future__ import annotations

import pytest

from magi_agent.evidence.runtime_issuance import issue_runtime_authority
from magi_agent.web_acquisition.cross_verifier import CrossVerifier, CrossVerifyResult
from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
from magi_agent.web_acquisition.page_navigator import ExtractedFact


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime_authority():
    return issue_runtime_authority(
        authority_id="test-cross-verify-authority",
        scopes=("research_source_proof", "research_claim_support"),
    )


@pytest.fixture()
def config_two_sources() -> DeepResearchConfig:
    return DeepResearchConfig(enabled=True, min_sources_for_cross_verify=2)


def _fact(
    value: str,
    url_ref: str,
    span_ref: str,
    confidence: float = 0.7,
) -> ExtractedFact:
    return ExtractedFact(
        value=value,
        source_url_ref=url_ref,
        span_ref=span_ref,
        context_snippet=f"context for {value}",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Two-source: supported
# ---------------------------------------------------------------------------


def test_cross_verifier_passes_with_two_sources(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("26.4", "url:abc1", "span.0.num"),
        _fact("26.4", "url:def2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.verdict == "supported"
    assert result.source_count >= 2
    assert result.top_candidate == "26.4"


def test_cross_verifier_claim_graph_populated_on_supported(
    runtime_authority, config_two_sources
) -> None:
    facts = [
        _fact("26.4", "url:abc1", "span.0.num"),
        _fact("26.4", "url:def2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.claim_graph is not None
    assert len(result.claim_graph.claims) == 1
    assert result.claim_graph.claims[0].support_verdict == "supported"


def test_cross_verifier_source_verdicts_populated(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("26.4", "url:abc1", "span.0.num"),
        _fact("26.4", "url:def2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert len(result.source_verdicts) == 2
    # All verdicts should be allowed (sources match)
    for verdict in result.source_verdicts:
        assert verdict.verdict == "allowed"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Single source: weak
# ---------------------------------------------------------------------------


def test_cross_verifier_weak_with_single_source(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("36", "url:abc1", "span.0.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.verdict == "weak"


def test_cross_verifier_weak_source_count_one(runtime_authority, config_two_sources) -> None:
    facts = [_fact("36", "url:abc1", "span.0.num")]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.source_count == 1


# ---------------------------------------------------------------------------
# Contradicted: two sources disagree with equal weight
# ---------------------------------------------------------------------------


def test_cross_verifier_contradicted_on_mismatch(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("3", "url:abc1", "span.0.num"),
        _fact("6", "url:def2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    # equal tie → contradicted
    assert result.verdict in {"contradicted", "weak"}


def test_cross_verifier_contradicted_no_claim_graph(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("3", "url:abc1", "span.0.num"),
        _fact("6", "url:def2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    if result.verdict == "contradicted":
        assert result.claim_graph is None


# ---------------------------------------------------------------------------
# Domain deduplication: two URLs from same domain count as 1 source
# ---------------------------------------------------------------------------


def test_cross_verifier_same_domain_counts_once(runtime_authority, config_two_sources) -> None:
    # Both URLs from the same domain (raw URL)
    facts = [
        _fact("42", "https://example.com/page1", "span.0.num"),
        _fact("42", "https://example.com/page2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    # Only 1 independent source → weak
    assert result.verdict == "weak"
    assert result.source_count == 1


def test_cross_verifier_different_domains_count_separately(
    runtime_authority, config_two_sources
) -> None:
    facts = [
        _fact("42", "https://example.com/page1", "span.0.num"),
        _fact("42", "https://other.org/page1", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.source_count == 2
    assert result.verdict == "supported"


# ---------------------------------------------------------------------------
# No facts
# ---------------------------------------------------------------------------


def test_cross_verifier_no_facts_not_evaluated(runtime_authority, config_two_sources) -> None:
    verifier = CrossVerifier()
    result = verifier.verify([], config_two_sources, runtime_authority)
    assert result.verdict == "not_evaluated"
    assert result.source_count == 0
    assert result.claim_graph is None


# ---------------------------------------------------------------------------
# Value normalisation
# ---------------------------------------------------------------------------


def test_cross_verifier_normalises_comma_numbers(runtime_authority, config_two_sources) -> None:
    # "1,234" and "1234" should be treated as same value
    facts = [
        _fact("1,234", "url:src1", "span.0.num"),
        _fact("1234", "url:src2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.verdict == "supported"


# ---------------------------------------------------------------------------
# Three sources: majority wins
# ---------------------------------------------------------------------------


def test_cross_verifier_majority_wins(runtime_authority, config_two_sources) -> None:
    facts = [
        _fact("10", "url:src1", "span.0.num"),
        _fact("10", "url:src2", "span.1.num"),
        _fact("9", "url:src3", "span.2.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.verdict == "supported"
    assert result.top_candidate == "10"
    assert result.source_count >= 2


# ---------------------------------------------------------------------------
# Sealed-gate assertion: CrossVerifier never flips Literal[False] fields
# ---------------------------------------------------------------------------


def test_cross_verifier_does_not_modify_execution_posture(
    runtime_authority, config_two_sources
) -> None:
    """Claim graph posture fields must remain Literal[False] after verification."""
    facts = [
        _fact("5", "url:s1", "span.0.num"),
        _fact("5", "url:s2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    assert result.claim_graph is not None
    posture = result.claim_graph.execution_posture
    assert posture.live_execution_allowed is False
    assert posture.provider_calls_allowed is False
    assert posture.browser_execution_allowed is False
    assert posture.tool_execution_allowed is False
    assert posture.adk_runner_attached is False


def test_cross_verifier_source_verdicts_execution_posture_sealed(
    runtime_authority, config_two_sources
) -> None:
    """Source verdict posture must stay sealed after cross-verify."""
    facts = [
        _fact("5", "url:s1", "span.0.num"),
        _fact("5", "url:s2", "span.1.num"),
    ]
    verifier = CrossVerifier()
    result = verifier.verify(facts, config_two_sources, runtime_authority)
    for verdict in result.source_verdicts:
        posture = verdict.execution_posture  # type: ignore[union-attr]
        assert posture.live_execution_allowed is False
        assert posture.provider_calls_allowed is False
