from __future__ import annotations

from benchmarks.gaia.answer import (
    GAIA_FORMAT_ADHERENCE_NOTE,
    GAIA_SYSTEM_PROMPT,
    extract_final_answer,
)


def test_extracts_last_final_answer() -> None:
    text = "thinking...\nFINAL ANSWER: 42\nnoise\nFINAL ANSWER: egalitarian"
    assert extract_final_answer(text) == "egalitarian"


def test_strips_trailing_period_and_space() -> None:
    assert extract_final_answer("FINAL ANSWER:  Paris . ") == "Paris"


def test_returns_empty_when_absent() -> None:
    assert extract_final_answer("no answer here") == ""


def test_strips_surrounding_markdown_emphasis() -> None:
    # "**FINAL ANSWER:** 6" leaves a leading "**" on the tail after the regex
    # match; the closing bold marker must not pollute the scored answer.
    assert extract_final_answer("**FINAL ANSWER:** 6") == "6"
    assert extract_final_answer("FINAL ANSWER: **42**") == "42"
    assert extract_final_answer("FINAL ANSWER: `egalitarian`") == "egalitarian"


def test_compute_reminder_defers_units_to_question() -> None:
    from benchmarks.gaia.answer import gaia_system_prompt

    prompt = gaia_system_prompt({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"})
    # The compute directive must tell the agent the raw tool value is intermediate
    # and the question's requested units/scale take precedence (conflict fix).
    assert "thousand" in prompt.lower()
    assert "precedence" in prompt.lower()


def test_prompt_mentions_final_answer_contract() -> None:
    assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tool-advertising tests — RED until the prompt is updated
# ---------------------------------------------------------------------------


class TestToolAdvertising:
    """The prompt must name each newly-added tool so the agent knows to call it."""

    def test_prompt_advertises_document_search(self) -> None:
        assert "DocumentSearch" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_archive_extract(self) -> None:
        assert "ArchiveExtract" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_xlsx_info(self) -> None:
        assert "XLSXInfo" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_xlsx_read_cell_range(self) -> None:
        # XLSXRead with cellRange should be called out explicitly
        assert "XLSXRead" in GAIA_SYSTEM_PROMPT
        assert "cellRange" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_image_understand_structured(self) -> None:
        # ImageUnderstand must be mentioned together with structured mode
        assert "ImageUnderstand" in GAIA_SYSTEM_PROMPT
        assert "structured" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_web_search(self) -> None:
        assert "web_search" in GAIA_SYSTEM_PROMPT

    def test_prompt_advertises_web_fetch(self) -> None:
        assert "web_fetch" in GAIA_SYSTEM_PROMPT

    # Regression: existing rules must survive
    def test_prompt_still_has_final_answer(self) -> None:
        assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT

    def test_prompt_still_forbids_abstention(self) -> None:
        lowered = GAIA_SYSTEM_PROMPT.lower()
        assert "never" in lowered or "must not" in lowered or "do not" in lowered

    def test_prompt_still_has_best_guess(self) -> None:
        lowered = GAIA_SYSTEM_PROMPT.lower()
        assert "best" in lowered and "guess" in lowered


# ---------------------------------------------------------------------------
# Format-adherence advertisement note (benchmark prompt layer).
# The note is imported by harness.py by name and advertises the general
# output-format-adherence capability to the GAIA agent before it finalizes.
# ---------------------------------------------------------------------------


class TestFormatAdherenceNote:
    def test_note_exists_and_nonempty(self) -> None:
        assert isinstance(GAIA_FORMAT_ADHERENCE_NOTE, str)
        assert GAIA_FORMAT_ADHERENCE_NOTE.strip()

    def test_note_covers_units_scale_rounding_name_format(self) -> None:
        lowered = GAIA_FORMAT_ADHERENCE_NOTE.lower()
        assert "unit" in lowered and "scale" in lowered
        assert "round" in lowered
        assert "name" in lowered and "format" in lowered

    def test_note_forbids_unrequested_units(self) -> None:
        lowered = GAIA_FORMAT_ADHERENCE_NOTE.lower()
        assert "do not add" in lowered
