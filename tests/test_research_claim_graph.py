from __future__ import annotations

import copy
import importlib
import inspect
import json

import pytest
from pydantic import ValidationError

from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimNode,
    ResearchClaimSupportRef,
    build_research_claim_node,
    project_research_claim_graph,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _support_ref(
    support_ref_id: str,
    source_ref_id: str,
    *,
    support_verdict: str = "supported",
    freshness_verdict: str = "current",
    relevance_verdict: str = "relevant",
    evidence_kind: str = "source_span",
    claim_value_digest: str | None = None,
    observed_value_digest: str | None = None,
    single_source_policy_digest: str | None = None,
    stale_support_policy: str = "block",
) -> ResearchClaimSupportRef:
    return ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-claim-support",
            scopes=("research_claim_support",),
        ),
        support_ref_id=support_ref_id,
        source_ref_id=source_ref_id,
        span_refs=(f"span:{support_ref_id}",),
        source_digest=_digest("b"),
        evidence_digest=_digest("c"),
        evidence_kind=evidence_kind,
        support_verdict=support_verdict,
        freshness_verdict=freshness_verdict,
        relevance_verdict=relevance_verdict,
        claim_value_digest=claim_value_digest,
        observed_value_digest=observed_value_digest,
        single_source_policy_digest=single_source_policy_digest,
        stale_support_policy=stale_support_policy,
        public_label="Inspected source metadata",
    )


def test_claim_support_ref_factory_requires_runtime_issue_authority() -> None:
    with pytest.raises(RuntimeError, match="runtime issue authority"):
        ResearchClaimSupportRef.issue_verified_support_ref(
            support_ref_id="support:pricing",
            source_ref_id="src_1",
            span_refs=("span:pricing",),
            source_digest=_digest("b"),
            evidence_digest=_digest("c"),
            evidence_kind="source_span",
            support_verdict="supported",
            freshness_verdict="current",
            relevance_verdict="relevant",
            public_label="Inspected source metadata",
        )


def test_numeric_mismatch_fails_and_cannot_project_as_fact() -> None:
    claim = build_research_claim_node(
        claim_id="claim:numeric-price",
        claim_text_digest=_digest("1"),
        claim_kind="numeric",
        claim_preview="The plan costs 29 USD.",
        support_refs=(
            _support_ref(
                "support:numeric-mismatch",
                "src_1",
                claim_value_digest=_digest("2"),
                observed_value_digest=_digest("3"),
            ),
        ),
    )

    assert claim.support_verdict == "contradicted"
    assert claim.projection_mode == "omitted"

    with pytest.raises(ValidationError):
        ResearchClaimNode(
            claimId="claim:numeric-price",
            claimTextDigest=_digest("1"),
            claimKind="numeric",
            claimPreview="The plan costs 29 USD.",
            supportRefs=claim.support_refs,
            supportVerdict="supported",
            projectionMode="fact",
        )


def test_comparative_claim_requires_two_relevant_sources_or_documented_policy() -> None:
    one_source = build_research_claim_node(
        claim_id="claim:comparative-one-source",
        claim_text_digest=_digest("4"),
        claim_kind="comparative",
        claim_preview="OpenMagi is cheaper than Vendor X.",
        support_refs=(_support_ref("support:one", "src_1"),),
    )

    assert one_source.support_verdict == "weak"
    assert one_source.projection_mode == "qualified"

    documented_policy = build_research_claim_node(
        claim_id="claim:comparative-policy",
        claim_text_digest=_digest("5"),
        claim_kind="comparative",
        claim_preview="OpenMagi is cheaper than Vendor X.",
        support_refs=(
            _support_ref(
                "support:policy",
                "src_1",
                single_source_policy_digest=_digest("6"),
            ),
        ),
    )
    two_sources = build_research_claim_node(
        claim_id="claim:comparative-two-sources",
        claim_text_digest=_digest("7"),
        claim_kind="comparative",
        claim_preview="OpenMagi is cheaper than Vendor X.",
        support_refs=(
            _support_ref("support:a", "src_1"),
            _support_ref("support:b", "src_2"),
        ),
    )

    assert documented_policy.support_verdict == "supported"
    assert documented_policy.projection_mode == "fact"
    assert two_sources.support_verdict == "supported"
    assert two_sources.projection_mode == "fact"


def test_comparative_value_mismatch_is_contradicted_even_with_two_sources() -> None:
    claim = build_research_claim_node(
        claim_id="claim:comparative-mismatch",
        claim_text_digest=_digest("0"),
        claim_kind="comparative",
        claim_preview="Vendor A is cheaper than Vendor B.",
        support_refs=(
            _support_ref(
                "support:vendor-a",
                "src_1",
                claim_value_digest=_digest("1"),
                observed_value_digest=_digest("2"),
            ),
            _support_ref("support:vendor-b", "src_2"),
        ),
    )

    assert claim.support_verdict == "contradicted"
    assert claim.projection_mode == "omitted"


def test_weak_inference_cannot_render_as_fact_even_with_preview() -> None:
    claim = build_research_claim_node(
        claim_id="claim:weak-inference",
        claim_text_digest=_digest("8"),
        claim_kind="inference",
        claim_preview="The product likely targets research teams.",
        support_refs=(
            _support_ref(
                "support:weak-inference",
                "src_1",
                support_verdict="weak",
            ),
        ),
    )
    graph = ResearchClaimGraph(claimGraphId="claim-graph:weak", claims=(claim,))

    projection = project_research_claim_graph(graph)

    assert claim.support_verdict == "weak"
    assert claim.projection_mode == "qualified"
    assert projection["claims"][0]["projectionMode"] == "qualified"
    assert projection["claims"][0]["renderAsFact"] is False


def test_contradicted_support_blocks_projection() -> None:
    claim = build_research_claim_node(
        claim_id="claim:contradicted",
        claim_text_digest=_digest("9"),
        claim_kind="factual",
        claim_preview="The service launched in 2026.",
        support_refs=(
            _support_ref(
                "support:contradicts",
                "src_1",
                support_verdict="contradicted",
            ),
        ),
    )
    projection = project_research_claim_graph(
        ResearchClaimGraph(claimGraphId="claim-graph:contradicted", claims=(claim,))
    )

    assert claim.support_verdict == "contradicted"
    assert claim.projection_mode == "omitted"
    assert projection["claims"][0]["claimPreview"] is None
    assert projection["claims"][0]["renderAsFact"] is False


def test_stale_support_blocks_or_downgrades_according_to_criteria_policy() -> None:
    blocked = build_research_claim_node(
        claim_id="claim:stale-blocked",
        claim_text_digest=_digest("a"),
        claim_kind="temporal",
        claim_preview="The pricing changed this week.",
        support_refs=(
            _support_ref(
                "support:stale-blocked",
                "src_1",
                freshness_verdict="stale",
                stale_support_policy="block",
            ),
        ),
    )
    downgraded = build_research_claim_node(
        claim_id="claim:stale-downgraded",
        claim_text_digest=_digest("b"),
        claim_kind="temporal",
        claim_preview="The pricing changed this week.",
        support_refs=(
            _support_ref(
                "support:stale-downgraded",
                "src_1",
                freshness_verdict="stale",
                stale_support_policy="downgrade",
            ),
        ),
    )

    assert blocked.support_verdict == "stale"
    assert blocked.projection_mode == "omitted"
    assert downgraded.support_verdict == "weak"
    assert downgraded.projection_mode == "qualified"


def test_public_projection_is_digest_safe_metadata_only_and_never_trusts_model_summaries() -> None:
    claim = build_research_claim_node(
        claim_id="claim:supported",
        claim_text_digest=_digest("d"),
        claim_kind="factual",
        claim_preview="The service has a public pricing page.",
        support_refs=(_support_ref("support:safe", "src_1"),),
    )

    projection = project_research_claim_graph(
        ResearchClaimGraph(claimGraphId="claim-graph:safe", claims=(claim,))
    )
    dumped = json.dumps(projection, sort_keys=True)

    assert projection["claims"][0]["renderAsFact"] is True
    assert "https://" not in dumped
    assert "/Users/" not in dumped
    assert "Bearer " not in dumped
    assert "rawSourceText" not in dumped

    with pytest.raises(ValidationError):
        _support_ref(
            "support:model-summary-kind",
            "src_1",
            evidence_kind="model_summary",
        )
    with pytest.raises(ValidationError):
        ResearchClaimSupportRef(
            supportRefId="support:model-summary-label",
            sourceRefId="src_1",
            spanRefs=("span:1",),
            sourceDigest=_digest("e"),
            evidenceDigest=_digest("f"),
            evidenceKind="source_span",
            supportVerdict="supported",
            freshnessVerdict="current",
            relevanceVerdict="relevant",
            publicLabel="model-generated summary from an LLM",
        )


def test_model_construct_and_model_copy_cannot_spoof_claim_graph_verdicts() -> None:
    with pytest.raises(TypeError):
        ResearchClaimSupportRef.model_construct(
            supportRefId="support:unsafe",
            sourceRefId="https://example.com/raw",
            spanRefs=("/Users/kevin/private.txt",),
            sourceDigest="not-a-digest",
            evidenceDigest="not-a-digest",
            evidenceKind="raw_source",
            supportVerdict="supported",
            freshnessVerdict="current",
            relevanceVerdict="relevant",
        )

    claim = build_research_claim_node(
        claim_id="claim:copy-spoof",
        claim_text_digest=_digest("a"),
        claim_kind="numeric",
        claim_preview="The plan costs 29 USD.",
        support_refs=(
            _support_ref(
                "support:copy-spoof",
                "src_1",
                claim_value_digest=_digest("1"),
                observed_value_digest=_digest("2"),
            ),
        ),
    )
    copied = claim.model_copy(
        update={"supportVerdict": "supported", "projectionMode": "fact"}
    )

    assert copied.support_verdict == "contradicted"
    assert copied.projection_mode == "omitted"


def test_forged_mapping_or_unissued_support_refs_cannot_render_as_fact() -> None:
    forged_payload = {
        "supportRefId": "support:forged",
        "sourceRefId": "src_1",
        "spanRefs": ("span:forged",),
        "sourceDigest": _digest("b"),
        "evidenceDigest": _digest("c"),
        "evidenceKind": "source_span",
        "supportVerdict": "supported",
        "freshnessVerdict": "current",
        "relevanceVerdict": "relevant",
    }
    unissued = ResearchClaimSupportRef.model_validate(forged_payload)
    issued = _support_ref("support:issued", "src_1")
    mutated = _support_ref("support:mutated-issued", "src_1")
    mutated.__dict__["support_verdict"] = "supported"
    mutated.__dict__["evidence_digest"] = _digest("d")

    with pytest.raises(TypeError, match="verifier-issued support ref objects"):
        build_research_claim_node(
            claim_id="claim:forged-mapping",
            claim_text_digest=_digest("e"),
            claim_kind="factual",
            support_refs=(forged_payload,),
            claim_preview="The service has pricing.",
        )

    for support_ref in (
        unissued,
        issued.model_copy(),
        copy.copy(issued),
        copy.deepcopy(issued),
        mutated,
    ):
        with pytest.raises(ValueError, match="claim support verifier"):
            build_research_claim_node(
                claim_id="claim:forged-object",
                claim_text_digest=_digest("f"),
                claim_kind="factual",
                support_refs=(support_ref,),
                claim_preview="The service has pricing.",
            )


def test_mutated_claim_graph_objects_are_revalidated_before_projection() -> None:
    claim = build_research_claim_node(
        claim_id="claim:mutated",
        claim_text_digest=_digest("a"),
        claim_kind="numeric",
        claim_preview="The plan costs 29 USD.",
        support_refs=(
            _support_ref(
                "support:mutated",
                "src_1",
                claim_value_digest=_digest("1"),
                observed_value_digest=_digest("2"),
            ),
        ),
    )
    graph = ResearchClaimGraph(claimGraphId="claim-graph:mutated", claims=(claim,))
    graph.claims[0].__dict__["support_verdict"] = "supported"
    graph.claims[0].__dict__["projection_mode"] = "fact"

    with pytest.raises(ValueError):
        project_research_claim_graph(graph)


def test_mutated_support_refs_are_revalidated_before_projection() -> None:
    weak_support = _support_ref(
        "support:mutated-ref",
        "src_1",
        support_verdict="weak",
    )
    claim = build_research_claim_node(
        claim_id="claim:mutated-ref",
        claim_text_digest=_digest("b"),
        claim_kind="factual",
        claim_preview="The service has a pricing page.",
        support_refs=(weak_support,),
    )
    graph = ResearchClaimGraph(claimGraphId="claim-graph:mutated-ref", claims=(claim,))
    graph.claims[0].support_refs[0].__dict__["support_verdict"] = "supported"

    with pytest.raises(ValueError):
        project_research_claim_graph(graph)


def test_claim_graph_stays_research_local_without_adk_or_generic_runtime_imports() -> None:
    module = importlib.import_module("magi_agent.research.claim_graph")
    source = inspect.getsource(module)
    source_for_layer_check = source.replace(
        "from magi_agent.evidence.runtime_issuance import (",
        "from allowed_domain_neutral_runtime_issuance import (",
    )

    assert module.__name__ == "magi_agent.research.claim_graph"
    graph = ResearchClaimGraph(claimGraphId="claim-graph:adk", claims=())
    assert "ArtifactService" in graph.adk_usage_notes
    assert "Evaluation" in graph.adk_usage_notes
    assert "runtime_issuance" in source
    assert ResearchClaimNode.__module__.startswith("magi_agent.research")
    assert ResearchClaimSupportRef.__module__.startswith("magi_agent.research")
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
