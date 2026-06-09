"""Cross-verifier for deep web research: verifies ≥2 independent sources agree.

Integrates with ``magi_agent.research.source_proof`` and
``magi_agent.research.claim_graph`` via the existing sealed seams.
No network calls; all facts must be pre-extracted by ``FactExtractor``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from magi_agent.evidence.runtime_issuance import RuntimeIssueAuthority
from magi_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimSupportRef,
    build_research_claim_node,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)
from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
from magi_agent.web_acquisition.page_navigator import ExtractedFact
from magi_agent.web_acquisition.policy import content_digest

CrossVerifyVerdict = Literal["supported", "weak", "contradicted", "insufficient", "not_evaluated"]

_CLAIM_GRAPH_ID = "deep-web-research-cross-verify"
_ADK_NOTES = "cross-verified web research claim"

# Normalisation helpers
_COMMA_RE = re.compile(r",")
_WS_RE = re.compile(r"\s+")


def _normalise_value(raw: str) -> str:
    """Normalise a fact value for comparison.

    - Strip commas from numbers (1,234 → 1234)
    - Round floats to 1 decimal place for comparison
    - Normalise whitespace
    - Casefold
    """
    v = _COMMA_RE.sub("", raw.strip())
    try:
        f = float(v)
        # Use one decimal for comparison to tolerate minor rounding diff
        return f"{f:.1f}"
    except ValueError:
        pass
    return _WS_RE.sub(" ", v).casefold()


def _domain_of_url_ref(url_ref: str) -> str:
    """Extract a domain identifier from a url_ref or raw URL.

    url_refs are already redacted (``url:<digest>``), so we use the raw value
    if it looks like a real URL, otherwise treat the whole ref as the domain.
    """
    if url_ref.startswith(("http://", "https://")):
        try:
            host = urlsplit(url_ref).hostname or url_ref
            return host.casefold()
        except Exception:
            return url_ref
    # Already a digest ref like "url:abc123" — treat as unique source identity
    return url_ref


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossVerifyResult:
    """Result of a cross-verification run."""

    verdict: CrossVerifyVerdict
    top_candidate: str | None            # The most-supported normalised value
    source_count: int                    # Number of independent sources agreeing
    total_facts: int                     # Total facts evaluated
    claim_graph: ResearchClaimGraph | None
    source_verdicts: tuple[object, ...]  # ResearchSourceProofVerdict objects
    diagnostic: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CrossVerifier
# ---------------------------------------------------------------------------


class CrossVerifier:
    """Verifies extracted facts across ≥2 independent sources.

    Algorithm:
    1. Normalise all fact values.
    2. De-duplicate by domain (same domain = 1 source).
    3. Group by normalised value, find the plurality candidate.
    4. Check whether the plurality candidate has ≥ min_sources support.
    5. Build source_proof receipts and verify them.
    6. Build claim_graph with the verified support refs.
    7. Return CrossVerifyResult with verdict.
    """

    def verify(
        self,
        facts: list[ExtractedFact],
        config: DeepResearchConfig,
        runtime_authority: RuntimeIssueAuthority,
    ) -> CrossVerifyResult:
        if not facts:
            return CrossVerifyResult(
                verdict="not_evaluated",
                top_candidate=None,
                source_count=0,
                total_facts=0,
                claim_graph=None,
                source_verdicts=(),
                diagnostic={"reason": "no_facts"},
            )

        # 1. Normalise and deduplicate by domain
        domain_facts: dict[str, list[ExtractedFact]] = {}
        for fact in facts:
            domain = _domain_of_url_ref(fact.source_url_ref)
            domain_facts.setdefault(domain, []).append(fact)

        # One best-confidence fact per domain
        domain_best: dict[str, ExtractedFact] = {}
        for domain, domain_fact_list in domain_facts.items():
            domain_best[domain] = max(domain_fact_list, key=lambda f: f.confidence)

        deduped_facts = list(domain_best.values())

        # 2. Group by normalised value → count independent source domains
        value_to_domains: dict[str, list[str]] = {}
        value_to_facts: dict[str, list[tuple[str, ExtractedFact]]] = {}
        for domain, fact in domain_best.items():
            norm = _normalise_value(fact.value)
            value_to_domains.setdefault(norm, []).append(domain)
            value_to_facts.setdefault(norm, []).append((domain, fact))

        # 3. Find plurality candidate
        if not value_to_domains:
            return CrossVerifyResult(
                verdict="not_evaluated",
                top_candidate=None,
                source_count=0,
                total_facts=len(facts),
                claim_graph=None,
                source_verdicts=(),
                diagnostic={"reason": "no_deduped_facts"},
            )

        top_norm = max(value_to_domains, key=lambda v: len(value_to_domains[v]))
        top_domains = value_to_domains[top_norm]
        top_source_count = len(top_domains)
        top_raw_value = value_to_facts[top_norm][0][1].value  # raw value of first agreeing fact

        # 4. Check multi-value contradiction: if second-most-supported is close
        sorted_norms = sorted(value_to_domains, key=lambda v: len(value_to_domains[v]), reverse=True)
        is_contradicted = (
            len(sorted_norms) >= 2
            and len(value_to_domains[sorted_norms[1]]) > 0
            and len(top_domains) == len(value_to_domains[sorted_norms[1]])
        )

        # 5. Build source receipts for the top-candidate domains
        inspected_at = datetime.now(timezone.utc).isoformat()
        source_refs: list[ResearchSourceOpenReceiptRef] = []
        span_to_source: dict[str, int] = {}  # span_ref → source index (1-based)

        top_facts_for_proof = value_to_facts[top_norm]
        for src_idx, (domain, fact) in enumerate(top_facts_for_proof, start=1):
            source_ref_id = f"src_{src_idx}"
            span_to_source[fact.span_ref] = src_idx
            page_digest = content_digest(fact.context_snippet)
            source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
                runtime_authority=runtime_authority,
                source_ref_id=source_ref_id,
                source_kind="web_fetch",
                receipt_kind="opened_snapshot",
                opened=True,
                content_digest=page_digest,
                inspected_at=inspected_at,
                span_refs=(fact.span_ref,),
                redaction_status="redacted",
            )
            source_refs.append(source_ref)

        # 6. Verify source proofs
        requirements = [
            ResearchSourceProofRequirement(
                sourceRefId=f"src_{i + 1}",
                allowedSourceKinds=("web_fetch", "web_search"),
                requiredReceiptKinds=("opened_snapshot",),
                requiredSpanRefs=(top_facts_for_proof[i][1].span_ref,),
                allowedRedactionStatuses=("redacted", "metadata_only"),
            )
            for i in range(len(top_facts_for_proof))
        ]
        source_verdicts = verify_research_source_proof(requirements, source_refs)
        allowed_verdicts = tuple(v for v in source_verdicts if v.verdict == "allowed")

        # 7. Build claim support refs and claim graph
        support_refs: list[ResearchClaimSupportRef] = []
        value_digest = content_digest(top_raw_value)
        claim_text_digest = content_digest(f"deep_web_research:{top_raw_value}")

        for allowed_verdict in allowed_verdicts:
            src_idx_str = allowed_verdict.source_ref_id  # e.g. "src_1"
            src_idx_num = int(src_idx_str.split("_")[1])
            domain, fact = top_facts_for_proof[src_idx_num - 1]
            support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
                runtime_authority=runtime_authority,
                support_ref_id=f"support-deep-{src_idx_str}",
                source_ref_id=src_idx_str,
                span_refs=(fact.span_ref,),
                source_digest=allowed_verdict.content_digest or content_digest(""),
                evidence_digest=content_digest(fact.value),
                evidence_kind="web.numeric.extraction",
                support_verdict="supported",
                freshness_verdict="current",
                relevance_verdict="relevant",
                claim_value_digest=value_digest,
                observed_value_digest=content_digest(fact.value),
            )
            support_refs.append(support_ref)

        # Determine final verdict using claim_graph logic
        if is_contradicted:
            final_verdict: CrossVerifyVerdict = "contradicted"
            claim_graph = None
        elif len(support_refs) >= config.min_sources_for_cross_verify:
            # Build claim node using "comparative" kind which requires ≥2 sources
            claim_node = build_research_claim_node(
                claim_id="deep-web-claim-1",
                claim_text_digest=claim_text_digest,
                claim_kind="comparative",
                support_refs=support_refs,
                claim_preview=f"Verified value: {top_raw_value}",
            )
            claim_graph = ResearchClaimGraph(
                claimGraphId=_CLAIM_GRAPH_ID,
                claims=(claim_node,),
                adkUsageNotes=_ADK_NOTES,
            )
            final_verdict = "supported"
        elif len(support_refs) > 0:
            # Single source — build "factual" (not "comparative") to avoid auto-weak from claim_graph
            # Then report verdict=weak from our own logic (single source < min)
            claim_node = build_research_claim_node(
                claim_id="deep-web-claim-1",
                claim_text_digest=claim_text_digest,
                claim_kind="factual",
                support_refs=support_refs,
                claim_preview=f"Single-source value: {top_raw_value}",
            )
            claim_graph = ResearchClaimGraph(
                claimGraphId=_CLAIM_GRAPH_ID,
                claims=(claim_node,),
                adkUsageNotes=_ADK_NOTES,
            )
            final_verdict = "weak"
        else:
            final_verdict = "insufficient"
            claim_graph = None

        return CrossVerifyResult(
            verdict=final_verdict,
            top_candidate=top_raw_value,
            source_count=top_source_count,
            total_facts=len(facts),
            claim_graph=claim_graph,
            source_verdicts=tuple(source_verdicts),
            diagnostic={
                "deduped_source_count": len(deduped_facts),
                "unique_values": len(value_to_domains),
                "top_norm": top_norm,
                "is_contradicted": is_contradicted,
            },
        )


__all__ = [
    "CrossVerifier",
    "CrossVerifyResult",
    "CrossVerifyVerdict",
]
