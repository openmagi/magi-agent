from __future__ import annotations

import importlib
import inspect
import json

import pytest
from pydantic import ValidationError

from magi_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriterion,
    ResearchAcceptanceCriteriaSet,
    ResearchAcceptanceEvidenceRef,
    derive_research_acceptance_status,
    pricing_acceptance_criteria,
    project_research_acceptance_criteria_set,
    positioning_acceptance_criteria,
    recent_events_acceptance_criteria,
)


def _strong_current_ref(evidence_ref_id: str, evidence_type: str) -> ResearchAcceptanceEvidenceRef:
    return ResearchAcceptanceEvidenceRef(
        evidenceRefId=evidence_ref_id,
        evidenceType=evidence_type,
        supportVerdict="supports",
        freshnessVerdict="current",
        digest="sha256:" + "a" * 64,
        spanRefs=("span:1",),
        publicLabel="Public docs pricing table",
    )


def test_template_constructors_cover_pricing_positioning_and_recent_events() -> None:
    pricing = pricing_acceptance_criteria("openmagi")
    positioning = positioning_acceptance_criteria("openmagi")
    recent = recent_events_acceptance_criteria("openmagi")

    assert [criterion.criteria_id for criterion in pricing.criteria] == [
        "pricing.current_price_points",
        "pricing.billing_terms",
        "pricing.source_date",
    ]
    assert pricing.criteria[0].required_evidence_types == (
        "source_inspection",
        "pricing_page",
    )
    assert pricing.criteria[0].source_freshness_policy.max_age_days == 30

    assert [criterion.criteria_id for criterion in positioning.criteria] == [
        "positioning.official_description",
        "positioning.competitor_context",
    ]
    assert positioning.criteria[0].required_evidence_types == (
        "source_inspection",
        "official_source",
    )

    assert [criterion.criteria_id for criterion in recent.criteria] == [
        "recent_events.temporal_anchor",
        "recent_events.event_source",
        "recent_events.corroboration",
    ]
    assert recent.criteria[0].required_evidence_types == ("clock",)
    assert recent.criteria[1].source_freshness_policy.max_age_days == 14


def test_missing_required_evidence_is_not_satisfied() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="pricing.current_price_points",
        description="Current public pricing must be backed by inspected pricing source evidence.",
        requiredEvidenceTypes=("source_inspection", "pricing_page"),
        optionalEvidenceTypes=("archive_snapshot",),
        sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
        completionMode="required",
        evidenceRefs=(_strong_current_ref("ev:source", "source_inspection"),),
    )

    assert criterion.status == "partial"
    assert derive_research_acceptance_status(criterion) == "partial"


def test_weak_or_stale_evidence_does_not_satisfy_required_criterion() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="recent_events.event_source",
        description="Recent event claims require current supporting source inspection evidence.",
        requiredEvidenceTypes=("source_inspection",),
        optionalEvidenceTypes=(),
        sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 7},
        completionMode="required",
        evidenceRefs=(
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:weak",
                evidenceType="source_inspection",
                supportVerdict="weak",
                freshnessVerdict="current",
                digest="sha256:" + "b" * 64,
                spanRefs=("span:weak",),
                publicLabel="Search result snippet",
            ),
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:stale",
                evidenceType="source_inspection",
                supportVerdict="supports",
                freshnessVerdict="stale",
                digest="sha256:" + "c" * 64,
                spanRefs=("span:stale",),
                publicLabel="Archived article",
            ),
        ),
    )

    assert criterion.status == "partial"


def test_contradicted_required_evidence_blocks_even_with_supporting_ref() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="pricing.current_price_points",
        description="Current public pricing must be backed by non-contradicted pricing source evidence.",
        requiredEvidenceTypes=("source_inspection",),
        optionalEvidenceTypes=(),
        sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
        completionMode="required",
        evidenceRefs=(
            _strong_current_ref("ev:support", "source_inspection"),
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:contradicts",
                evidenceType="source_inspection",
                supportVerdict="contradicts",
                freshnessVerdict="current",
                digest="sha256:" + "3" * 64,
                spanRefs=("span:contradicts",),
                publicLabel="Conflicting pricing table",
            ),
        ),
    )

    assert criterion.status == "blocked"


def test_contradictory_optional_evidence_blocks_projection() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="pricing.current_price_points",
        description="Current public pricing must be backed by non-contradicted pricing source evidence.",
        requiredEvidenceTypes=("source_inspection",),
        optionalEvidenceTypes=("pricing_page",),
        sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
        completionMode="required",
        evidenceRefs=(
            _strong_current_ref("ev:support", "source_inspection"),
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:optional-contradicts",
                evidenceType="pricing_page",
                supportVerdict="contradicts",
                freshnessVerdict="current",
                digest="sha256:" + "5" * 64,
                spanRefs=("span:optional-contradicts",),
                publicLabel="Conflicting pricing page",
            ),
        ),
    )

    assert criterion.status == "blocked"


def test_undeclared_evidence_ref_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchAcceptanceCriterion(
            criteriaId="pricing.current_price_points",
            description="Current public pricing must be backed by declared evidence types.",
            requiredEvidenceTypes=("source_inspection",),
            optionalEvidenceTypes=("pricing_page",),
            sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
            completionMode="required",
            evidenceRefs=(_strong_current_ref("ev:extra", "archive_snapshot"),),
        )


def test_not_applicable_freshness_cannot_satisfy_freshness_bound_criteria() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="recent_events.event_source",
        description="Recent event claims require source freshness evidence.",
        requiredEvidenceTypes=("source_inspection",),
        optionalEvidenceTypes=(),
        sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 14},
        completionMode="required",
        evidenceRefs=(
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:no-freshness",
                evidenceType="source_inspection",
                supportVerdict="supports",
                freshnessVerdict="not_applicable",
                digest="sha256:" + "6" * 64,
                spanRefs=("span:no-freshness",),
                publicLabel="Unanchored source",
            ),
        ),
    )

    assert criterion.status == "partial"


def test_not_applicable_freshness_can_satisfy_policy_without_freshness_requirement() -> None:
    criterion = ResearchAcceptanceCriterion(
        criteriaId="positioning.stable_category",
        description="Stable positioning category can use non-temporal source support.",
        requiredEvidenceTypes=("official_source",),
        optionalEvidenceTypes=(),
        sourceFreshnessPolicy={"policy": "none"},
        completionMode="required",
        evidenceRefs=(
            ResearchAcceptanceEvidenceRef(
                evidenceRefId="ev:stable",
                evidenceType="official_source",
                supportVerdict="supports",
                freshnessVerdict="not_applicable",
                digest="sha256:" + "7" * 64,
                spanRefs=("span:stable",),
                publicLabel="Official docs",
            ),
        ),
    )

    assert criterion.status == "satisfied"


def test_not_applicable_completion_mode_cannot_hide_declared_or_contradictory_evidence() -> None:
    with pytest.raises(ValidationError):
        ResearchAcceptanceCriterion(
            criteriaId="pricing.not_applicable",
            description="Not applicable criteria cannot carry evidence requirements.",
            requiredEvidenceTypes=("source_inspection",),
            optionalEvidenceTypes=(),
            sourceFreshnessPolicy={"policy": "none"},
            completionMode="not_applicable",
            evidenceRefs=(
                ResearchAcceptanceEvidenceRef(
                    evidenceRefId="ev:contradicts",
                    evidenceType="source_inspection",
                    supportVerdict="contradicts",
                    freshnessVerdict="not_applicable",
                    digest="sha256:" + "8" * 64,
                    spanRefs=("span:contradicts",),
                    publicLabel="Contradictory source",
                ),
            ),
        )


def test_public_projection_is_deterministic_digest_safe_and_metadata_only() -> None:
    criteria_set = ResearchAcceptanceCriteriaSet(
        criteriaSetId="research-acceptance-pricing",
        targetLabel="OpenMagi pricing",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="pricing.current_price_points",
                description="Current public pricing must be backed by inspected pricing source evidence.",
                requiredEvidenceTypes=("source_inspection",),
                optionalEvidenceTypes=("pricing_page",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="required",
                evidenceRefs=(_strong_current_ref("ev:pricing", "source_inspection"),),
            ),
        ),
    )

    projection = project_research_acceptance_criteria_set(criteria_set)
    dumped = json.dumps(projection, sort_keys=True)

    assert criteria_set.criteria[0].status == "satisfied"
    assert projection["criteriaSetId"] == "research-acceptance-pricing"
    assert projection["criteria"][0]["evidenceRefs"][0] == {
        "evidenceRefId": "ev:pricing",
        "evidenceType": "source_inspection",
        "supportVerdict": "supports",
        "freshnessVerdict": "current",
        "digest": "sha256:" + "a" * 64,
        "spanRefs": ("span:1",),
        "publicLabel": "Public docs pricing table",
    }
    assert "raw" not in dumped.lower()
    assert "/Users/" not in dumped
    assert "Bearer " not in dumped


@pytest.mark.parametrize(
    "payload",
    (
        {
            "evidenceRefId": "ev:raw",
            "evidenceType": "source_inspection",
            "supportVerdict": "supports",
            "freshnessVerdict": "current",
            "digest": "sha256:" + "d" * 64,
            "spanRefs": ("span:1",),
            "rawSourceText": "private page body",
        },
        {
            "evidenceRefId": "ev:path",
            "evidenceType": "source_inspection",
            "supportVerdict": "supports",
            "freshnessVerdict": "current",
            "digest": "sha256:" + "e" * 64,
            "spanRefs": ("/Users/kevin/private.txt",),
        },
        {
            "evidenceRefId": "ev:secret",
            "evidenceType": "source_inspection",
            "supportVerdict": "supports",
            "freshnessVerdict": "current",
            "digest": "sha256:" + "f" * 64,
            "spanRefs": ("span:1",),
            "publicLabel": "Authorization: Bearer unsafe-token",
        },
        {
            "evidenceRefId": "ev:summary",
            "evidenceType": "source_inspection",
            "supportVerdict": "supports",
            "freshnessVerdict": "current",
            "digest": "sha256:" + "1" * 64,
            "spanRefs": ("span:1",),
            "modelGeneratedSummary": "The source says yes.",
        },
        {
            "evidenceRefId": "ev:summary-label",
            "evidenceType": "source_inspection",
            "supportVerdict": "supports",
            "freshnessVerdict": "current",
            "digest": "sha256:" + "2" * 64,
            "spanRefs": ("span:1",),
            "publicLabel": "model-generated summary from an LLM",
        },
    ),
)
def test_unsafe_raw_private_or_model_summary_evidence_ref_input_is_rejected(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResearchAcceptanceEvidenceRef.model_validate(payload)


@pytest.mark.parametrize(
    "evidence_type",
    (
        "model_summary",
        "raw_source",
        "tool_output",
        "api_key",
        "auth_token",
        "private_path",
        "modelsummary",
        "rawsource",
        "tooloutput",
        "apikey",
        "authtoken",
        "privatepath",
    ),
)
def test_forbidden_evidence_type_names_cannot_satisfy_criteria(
    evidence_type: str,
) -> None:
    with pytest.raises(ValidationError):
        ResearchAcceptanceCriterion(
            criteriaId="pricing.current_price_points",
            description="Current public pricing must be backed by inspected pricing source evidence.",
            requiredEvidenceTypes=(evidence_type,),
            optionalEvidenceTypes=(),
            sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
            completionMode="required",
            evidenceRefs=(
                ResearchAcceptanceEvidenceRef(
                    evidenceRefId="ev:forbidden",
                    evidenceType=evidence_type,
                    supportVerdict="supports",
                    freshnessVerdict="current",
                    digest="sha256:" + "4" * 64,
                    spanRefs=("span:1",),
                ),
            ),
        )


def test_research_contract_stays_in_research_layer_not_generic_core_policy() -> None:
    module = importlib.import_module("magi_agent.research.acceptance_criteria")
    source = inspect.getsource(module)

    assert module.__name__ == "magi_agent.research.acceptance_criteria"
    assert ResearchAcceptanceCriteriaSet.__module__.startswith("magi_agent.research")
    assert ResearchAcceptanceCriterion.__module__.startswith("magi_agent.research")
    assert not ResearchAcceptanceCriteriaSet.__module__.startswith(
        "magi_agent.evidence"
    )
    assert not ResearchAcceptanceCriteriaSet.__module__.startswith(
        "magi_agent.harness"
    )
    forbidden_imports = (
        "magi_agent.evidence",
        "magi_agent.harness",
        "magi_agent.runtime",
        "magi_agent.tools",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source


def test_blank_required_target_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchAcceptanceCriteriaSet(
            criteriaSetId="research-acceptance-pricing",
            targetLabel="   ",
            criteria=(
                ResearchAcceptanceCriterion(
                    criteriaId="pricing.current_price_points",
                    description="Current public pricing must be backed by inspected pricing source evidence.",
                    requiredEvidenceTypes=("source_inspection",),
                    optionalEvidenceTypes=(),
                    sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                    completionMode="required",
                    evidenceRefs=(_strong_current_ref("ev:pricing", "source_inspection"),),
                ),
            ),
        )


def test_default_off_local_only_fake_provider_semantics_are_projected() -> None:
    criteria_set = positioning_acceptance_criteria("openmagi")
    projection = project_research_acceptance_criteria_set(criteria_set)

    assert projection["executionPosture"] == {
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
        "liveExecutionAllowed": False,
        "providerCallsAllowed": False,
        "adkRunnerAttached": False,
        "functionToolAttached": False,
    }


def test_model_construct_and_model_copy_cannot_bypass_contract_validation() -> None:
    with pytest.raises(TypeError):
        ResearchAcceptanceEvidenceRef.model_construct(
            evidenceRefId="ev:unsafe",
            evidenceType="source_inspection",
            supportVerdict="supports",
            freshnessVerdict="current",
            digest="not-a-digest",
            spanRefs=("/Users/kevin/private.txt",),
        )

    safe = _strong_current_ref("ev:safe", "source_inspection")
    with pytest.raises(ValidationError):
        safe.model_copy(update={"digest": "not-a-digest"})
