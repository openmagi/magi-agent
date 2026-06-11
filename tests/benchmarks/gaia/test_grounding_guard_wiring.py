"""GAIA-layer wiring tests for the grounded-answer guard.

CRITICAL invariants verified here (from the review):
- Flag OFF => byte-identical: no metadata, scored string untouched, prompt
  surfaces (GAIA_SYSTEM_PROMPT, extract_final_answer, build_cli_instruction)
  unchanged.
- Flag ON in the GAIA arm => the guard verdict is recorded as out-of-band
  metadata (verifierEvidenceStatus). It MUST NOT mutate the string returned by
  extract_final_answer / scored by question_scorer.
- The guard runs AFTER a committed answer exists (operates on the committed
  answer + collected corpus) and never re-triggers abstention or empties it.
"""
from __future__ import annotations

from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer
from benchmarks.gaia.grounding import gaia_grounding_metadata
from benchmarks.gaia.scorer import question_scorer

_FLAG = "MAGI_GROUNDED_ANSWER_GUARD_ENABLED"


# ---------------------------------------------------------------------------
# Flag OFF => byte-identical / no metadata.
# ---------------------------------------------------------------------------


def test_metadata_empty_when_flag_off() -> None:
    meta = gaia_grounding_metadata(
        answer="776665",
        tool_corpus=["VideoFrames only works on local files"],
        env={},
    )
    assert meta == {}


def test_metadata_empty_when_flag_explicitly_off() -> None:
    meta = gaia_grounding_metadata(
        answer="776665",
        tool_corpus=[],
        env={_FLAG: "0"},
    )
    assert meta == {}


def test_gaia_system_prompt_is_byte_identical_constant() -> None:
    # The guard adds NO text to the scored GAIA prompt under any flag — the
    # honesty mechanism is metadata-only for the scored arm.
    assert "GUESS" not in GAIA_SYSTEM_PROMPT
    assert "grounded_answer_guard" not in GAIA_SYSTEM_PROMPT
    assert GAIA_SYSTEM_PROMPT.endswith(
        "Apply these rules to each element of a list."
    )


def test_extract_final_answer_unaffected() -> None:
    # extract_final_answer never prepends a GUESS: label.
    text = "reasoning\nFINAL ANSWER: 776665"
    assert extract_final_answer(text) == "776665"


# ---------------------------------------------------------------------------
# Flag ON => metadata only, scored string untouched + correctly scored.
# ---------------------------------------------------------------------------


def test_guess_recorded_as_metadata_only_flag_on() -> None:
    answer = "776665"
    meta = gaia_grounding_metadata(
        answer=answer,
        tool_corpus=["VideoFrames is local-file only; the page was unreachable"],
        env={_FLAG: "1"},
    )
    assert meta["verifierEvidenceStatus"] == "guess"
    assert meta["extractedValue"] == "776665"
    # The answer string the metadata describes is unchanged.
    assert answer == "776665"


def test_correct_guess_still_scores_correct_flag_on() -> None:
    # The core regression guard: a coincidentally-correct best guess must STILL
    # score 1 when the guard labels it GUESS. The scorer sees the bare answer,
    # never a "GUESS: 776665" mutation.
    answer = "776665"
    meta = gaia_grounding_metadata(answer=answer, tool_corpus=[], env={_FLAG: "1"})
    assert meta["verifierEvidenceStatus"] == "guess"
    # Gold answer == the (correct) guess -> still scored correct.
    assert question_scorer(answer, "776665") is True
    # Defensive: a "GUESS:"-mutated string would NOT score correct.
    assert question_scorer("GUESS: 776665", "776665") is False


def test_grounded_answer_recorded_grounded_flag_on() -> None:
    meta = gaia_grounding_metadata(
        answer="776665",
        tool_corpus=["the counter shows 776,665 views"],
        env={_FLAG: "1"},
    )
    assert meta["verifierEvidenceStatus"] == "grounded"


def test_guard_does_not_empty_or_abstain_committed_answer() -> None:
    # Whatever the verdict, gaia_grounding_metadata returns ONLY metadata and
    # the caller's committed answer is never altered by this call.
    answer = "unable to determine"  # an abstention-looking committed string
    meta = gaia_grounding_metadata(answer=answer, tool_corpus=[], env={_FLAG: "1"})
    # No specific value -> grounded noop; metadata never carries a replacement.
    assert meta.get("verifierEvidenceStatus") == "grounded"
    assert "answer" not in meta
