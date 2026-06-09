"""Tests for PageNavigator + FactExtractor — PR2 (TDD: written first)."""

from __future__ import annotations

from magi_agent.web_acquisition.page_navigator import (
    ExtractedFact,
    ExtractedSection,
    FactExtractor,
    PageNavigator,
)

# ---------------------------------------------------------------------------
# Sample data (mirrors the spec examples)
# ---------------------------------------------------------------------------

SAMPLE_BOX_OFFICE = """
## Worldwide Box Office 2020

| Rank | Title | Gross |
|------|-------|-------|
| 1 | Bad Boys for Life | $206M |
| 2 | Sonic the Hedgehog | $148M |
| 3 | The Invisible Man | $143M |
| 4 | Onward | $141M |
| 5 | Tenet | $363M |
| 6 | The Personal History... | $48M |
"""

SAMPLE_YEAR_CONTEXT = "In 2018, Apple stock first crossed above the $200 mark."

SAMPLE_SECTIONS = """
## Introduction

Some intro text that is not very relevant.

## Historical Data

Apple stock crossed the $100 threshold multiple times.
In 2014 it split 7:1.

## Apple Stock Above 200

In 2018, Apple stock first crossed above the $200 mark.
This was a significant milestone.

## Unrelated Section

Nothing interesting here.
"""

SAMPLE_ORDERED_LIST = """
Some text.

1. Bad Boys for Life ($206M)
2. Sonic the Hedgehog ($148M)
3. The Invisible Man ($143M)
4. Onward ($141M)
5. Tenet ($363M)
6. The Personal History of David Copperfield ($48M)

More text.
"""


# ---------------------------------------------------------------------------
# PageNavigator tests
# ---------------------------------------------------------------------------


def test_navigator_returns_extracted_section() -> None:
    nav = PageNavigator()
    result = nav.extract_target(SAMPLE_BOX_OFFICE, "how many top 10 films 2020")
    assert isinstance(result, ExtractedSection)


def test_navigator_extracts_table() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_BOX_OFFICE, "how many top 10 films 2020")
    assert "Bad Boys" in section.content
    # table extraction should capture all rows, not just the first
    assert "Tenet" in section.content


def test_navigator_extracts_all_table_rows() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_BOX_OFFICE, "films 2020 box office gross")
    assert "Bad Boys" in section.content
    assert "Tenet" in section.content
    assert "Personal History" in section.content


def test_navigator_table_section_kind() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_BOX_OFFICE, "films 2020 box office")
    assert section.section_kind == "table"


def test_navigator_extracts_year_context() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_YEAR_CONTEXT, "Apple stock first year above 200")
    assert "2018" in section.content


def test_navigator_extracts_best_section() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_SECTIONS, "Apple stock first year above 200")
    # Should prefer the section that contains 2018 + Apple + 200
    assert "2018" in section.content


def test_navigator_section_kind_on_headers() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_SECTIONS, "Apple stock 2018 above 200")
    assert section.section_kind in {"section", "year_context", "table"}


def test_navigator_extracts_ordered_list() -> None:
    nav = PageNavigator()
    # Use a question whose words appear in the list content ("Bad Boys", "Tenet")
    section = nav.extract_target(SAMPLE_ORDERED_LIST, "Bad Boys Tenet Sonic top films")
    assert "Bad Boys" in section.content
    assert "Tenet" in section.content
    assert section.section_kind == "list"


def test_navigator_empty_markdown_returns_empty_section() -> None:
    nav = PageNavigator()
    section = nav.extract_target("", "some question")
    assert section.content == ""
    assert section.section_kind == "empty"
    assert section.confidence == 0.0


def test_navigator_max_chars_respected() -> None:
    nav = PageNavigator()
    long_md = "\n".join(f"| col1 | col{i} |" for i in range(500))
    section = nav.extract_target(long_md, "col", max_chars=100)
    assert len(section.content) <= 100


def test_navigator_confidence_positive() -> None:
    nav = PageNavigator()
    section = nav.extract_target(SAMPLE_BOX_OFFICE, "films 2020")
    assert 0.0 <= section.confidence <= 1.0


# ---------------------------------------------------------------------------
# FactExtractor tests
# ---------------------------------------------------------------------------


def test_fact_extractor_returns_list_of_facts() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(
        content="In 2018, Apple stock crossed $200.",
        section_kind="year_context",
    )
    facts = extractor.extract(section, source_url_ref="url:abc123", span_ref_prefix="span")
    assert isinstance(facts, list)
    assert all(isinstance(f, ExtractedFact) for f in facts)


def test_fact_extractor_finds_year() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="In 2018, Apple stock was $200.", section_kind="year_context")
    facts = extractor.extract(section, source_url_ref="url:abc123")
    values = [f.value for f in facts]
    assert "2018" in values


def test_fact_extractor_finds_numeric_value() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="Average works: 26.4 per researcher.", section_kind="section")
    facts = extractor.extract(section, source_url_ref="url:abc123")
    values = [f.value for f in facts]
    assert "26.4" in values


def test_fact_extractor_normalises_commas() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="Total: 1,234,567 downloads.", section_kind="section")
    facts = extractor.extract(section, source_url_ref="url:abc123")
    values = [f.value for f in facts]
    assert "1234567" in values


def test_fact_extractor_source_url_ref_preserved() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="Count: 42.", section_kind="section")
    facts = extractor.extract(section, source_url_ref="url:xyz789")
    assert all(f.source_url_ref == "url:xyz789" for f in facts)


def test_fact_extractor_span_ref_contains_prefix() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="Value: 99.", section_kind="section")
    facts = extractor.extract(section, source_url_ref="url:x", span_ref_prefix="myspan")
    assert all(f.span_ref.startswith("myspan") for f in facts)


def test_fact_extractor_no_duplicates() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="The answer is 42. The answer is 42.", section_kind="section")
    facts = extractor.extract(section, source_url_ref="url:abc")
    values = [f.value for f in facts]
    assert values.count("42") == 1


def test_fact_extractor_empty_section() -> None:
    extractor = FactExtractor()
    section = ExtractedSection(content="", section_kind="empty", confidence=0.0)
    facts = extractor.extract(section, source_url_ref="url:abc")
    assert facts == []
