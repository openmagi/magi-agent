from __future__ import annotations

from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer


def test_extracts_last_final_answer() -> None:
    text = "thinking...\nFINAL ANSWER: 42\nnoise\nFINAL ANSWER: egalitarian"
    assert extract_final_answer(text) == "egalitarian"


def test_strips_trailing_period_and_space() -> None:
    assert extract_final_answer("FINAL ANSWER:  Paris . ") == "Paris"


def test_returns_empty_when_absent() -> None:
    assert extract_final_answer("no answer here") == ""


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
