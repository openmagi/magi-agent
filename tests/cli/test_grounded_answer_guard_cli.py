"""CLI-surface tests for the grounded-answer guard.

- ``build_cli_instruction`` is a first-party prompt surface: the guard MUST NOT
  alter it under any flag (byte-identical assertion, flag OFF and ON).
- ``apply_guess_label`` is the NON-SCORED chat/CLI surface where a literal
  "GUESS:" prefix IS allowed (it never touches the GAIA scored answer path).
"""
from __future__ import annotations

import re

from magi_agent.cli.tool_runtime import build_cli_instruction
from magi_agent.research.grounded_answer_guard import (
    apply_guess_label,
    evaluate_answer_grounding,
)

_FLAG = "MAGI_GROUNDED_ANSWER_GUARD_ENABLED"


# The CLI instruction embeds the current wall-clock time, so two back-to-back
# builds can differ only in those timestamps. Normalize them so the comparison
# isolates flag-driven content changes (of which there must be none).
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z")
_CLOCK_RE = re.compile(r"\d{2}:\d{2}:\d{2}")


def _instruction(tmp_path) -> str:
    raw = build_cli_instruction(
        session_id="s1",
        workspace_root=str(tmp_path),
    )
    raw = _ISO_TS_RE.sub("<TS>", raw)
    return _CLOCK_RE.sub("<CLOCK>", raw)


def test_cli_instruction_byte_identical_flag_off(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    baseline = _instruction(tmp_path)
    assert "GUESS" not in baseline
    assert "grounded_answer_guard" not in baseline.lower()


def test_cli_instruction_byte_identical_flag_on(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    baseline = _instruction(tmp_path)
    monkeypatch.setenv(_FLAG, "1")
    with_flag = _instruction(tmp_path)
    # The guard is a metadata/post-answer mechanism, not a prompt edit: the CLI
    # instruction is byte-identical whether the flag is on or off.
    assert with_flag == baseline


# ---------------------------------------------------------------------------
# Non-scored surface: apply_guess_label may prepend a literal "GUESS:".
# ---------------------------------------------------------------------------


def test_apply_guess_label_prefixes_a_guess() -> None:
    verdict = evaluate_answer_grounding(answer="776665", tool_corpus=[])
    labelled = apply_guess_label("776665", verdict)
    assert labelled == "GUESS: 776665"


def test_apply_guess_label_noop_for_grounded() -> None:
    verdict = evaluate_answer_grounding(answer="776665", tool_corpus=["776665"])
    labelled = apply_guess_label("776665", verdict)
    assert labelled == "776665"


def test_apply_guess_label_does_not_double_prefix() -> None:
    verdict = evaluate_answer_grounding(answer="776665", tool_corpus=[])
    once = apply_guess_label("776665", verdict)
    twice = apply_guess_label(once, verdict)
    assert twice == "GUESS: 776665"
