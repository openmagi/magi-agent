"""Unit tests for magi_agent.solving.templates (U1.5 T11 — templates)."""
from __future__ import annotations

import pytest

from magi_agent.solving.templates import (
    DOMAIN_TEMPLATES,
    DomainTemplate,
    get_template,
)


DOMAIN_VALUES: list[str] = [
    "competitive_programming",
    "math_proof",
    "general_analysis",
]

STAGE_VALUES: list[str] = [
    "solver",
    "improver",
    "verifier",
    "adjudicator",
]


def test_all_domain_stage_pairs_present() -> None:
    """Every (domain x stage) pair must be present and non-empty."""
    for domain in DOMAIN_VALUES:
        for stage in STAGE_VALUES:
            template = get_template(domain, stage)  # type: ignore[arg-type]
            assert template, f"Template missing or empty for domain={domain!r} stage={stage!r}"
            assert len(template.strip()) > 0, f"Template empty for domain={domain!r} stage={stage!r}"


def test_verifier_templates_contain_json_schema_instruction() -> None:
    """Verifier templates must demand a fenced-JSON findings block."""
    for domain in DOMAIN_VALUES:
        template = get_template(domain, "verifier")  # type: ignore[arg-type]
        # Must mention structured/JSON findings output
        assert "```" in template or "json" in template.lower() or "JSON" in template, (
            f"Verifier template for {domain!r} does not reference JSON output"
        )


def test_verifier_templates_contain_find_dont_fix_clause() -> None:
    """Verifier templates must state the find-don't-fix rule."""
    keywords_kr = ["수정", "고치", "fix", "찾아", "찾기"]
    for domain in DOMAIN_VALUES:
        template = get_template(domain, "verifier")  # type: ignore[arg-type]
        lower = template.lower()
        # Must contain find/don't-fix language
        assert (
            "fix" in lower
            or "수정" in lower
            or "find" in lower
            or "찾" in lower
        ), f"Verifier template for {domain!r} missing find-don't-fix clause"


def test_adjudicator_templates_non_empty() -> None:
    """Adjudicator templates must exist and be non-trivial."""
    for domain in DOMAIN_VALUES:
        template = get_template(domain, "adjudicator")  # type: ignore[arg-type]
        assert len(template.strip()) > 50, f"Adjudicator template for {domain!r} seems too short"


def test_competitive_programming_verifier_mentions_categories() -> None:
    """Competitive programming verifier must reference domain-specific categories."""
    template = get_template("competitive_programming", "verifier")
    # Must mention at least some of the domain categories
    assert any(
        cat in template
        for cat in ["critical_logic", "complexity_exceeded", "implementation_bug", "missed_edge_case",
                    "critical", "complexity", "implementation", "edge_case"]
    ), "Competitive programming verifier does not mention domain categories"


def test_math_proof_verifier_mentions_categories() -> None:
    """Math proof verifier must reference domain-specific categories."""
    template = get_template("math_proof", "verifier")
    assert any(
        cat in template
        for cat in ["critical_error", "justification_gap", "critical", "justification", "gap"]
    ), "Math proof verifier does not mention domain categories"


def test_domain_templates_dict_structure() -> None:
    """DOMAIN_TEMPLATES must have expected structure."""
    assert isinstance(DOMAIN_TEMPLATES, dict)
    for domain in DOMAIN_VALUES:
        assert domain in DOMAIN_TEMPLATES, f"Domain {domain!r} missing from DOMAIN_TEMPLATES"
        domain_dict = DOMAIN_TEMPLATES[domain]
        assert isinstance(domain_dict, dict)
        for stage in STAGE_VALUES:
            assert stage in domain_dict, f"Stage {stage!r} missing from DOMAIN_TEMPLATES[{domain!r}]"


def test_get_template_returns_string() -> None:
    """get_template always returns a string."""
    for domain in DOMAIN_VALUES:
        for stage in STAGE_VALUES:
            result = get_template(domain, stage)  # type: ignore[arg-type]
            assert isinstance(result, str)


def test_solver_templates_mention_rigor() -> None:
    """Solver templates must carry rigor/correctness header language."""
    for domain in DOMAIN_VALUES:
        template = get_template(domain, "solver")  # type: ignore[arg-type]
        lower = template.lower()
        assert any(
            word in lower
            for word in ["rigor", "correct", "정확", "엄밀", "honesty", "honest", "partial"]
        ), f"Solver template for {domain!r} missing rigor/correctness language"
