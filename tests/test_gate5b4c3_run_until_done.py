"""Serve prompt drives run-until-done by trusting the tool loop (OpenCode-style).

We do NOT re-invoke the model after it ends a turn — that would override the
model's "done" signal and re-run completed work (and no structural heuristic can
tell a genuine final answer from a deferral). Instead the serve system prompt is
the lever: it must tell the model to perform each step with tools and keep
working until the whole task is complete, rather than stopping to describe a
plan. The old "complete the requested work in this turn" instruction conflicted
with multi-step tasks and is removed.
"""

from pathlib import Path

_RUNNER_INPUT_ADAPTER = (
    Path(__file__).resolve().parent.parent
    / "magi_agent"
    / "shadow"
    / "gate5b4c3_runner_input_adapter.py"
)


def test_serve_prompt_requires_multi_step_execution_not_single_turn() -> None:
    src = _RUNNER_INPUT_ADAPTER.read_text(encoding="utf-8")
    assert "complete the requested work in this turn" not in src
    assert "keep working until the whole task is actually" in src
    assert "never end by only describing a plan" in src
