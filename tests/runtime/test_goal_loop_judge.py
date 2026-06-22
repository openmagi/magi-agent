"""PR-C Layer 2: clean-break judge call evaluator.

The engine's clean-break branch (cli/engine.py:~2462) is the moment the agent
WOULD terminate the turn — model emitted text, no pending tool calls. PR-C
adds a small JSON-mode judge call at exactly that seam: if a goal-loop policy
is active for this turn (PR-B ContextVar), we ask a cheap judge model whether
the ORIGINAL objective is complete, then either terminate or re-invoke with
a generic continuation prompt.

This module owns:
  - the JSON-mode prompt template,
  - JSON-extraction parsing (permissive, brace-scan + json.loads),
  - the async ``evaluate_goal_completion`` driver,
  - the ``JudgeVerdict`` shape the engine acts on.

Hermetic: every test injects a fake ``judge_caller`` (``str -> str`` coroutine)
so no network call / litellm import is required.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from magi_agent.runtime.goal_loop_judge import (
    JUDGE_PROMPT_TEMPLATE,
    JudgeVerdict,
    evaluate_goal_completion,
    parse_judge_response,
)
from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_CONTINUATION_TEMPLATE,
    GoalLoopPolicy,
)


def _policy(objective: str = "do the thing") -> GoalLoopPolicy:
    return GoalLoopPolicy(
        enabled=True,
        objective=objective,
        max_turns=20,
        judge_provider=None,
        judge_model=None,
        judge_parse_failures_budget=2,
        continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
    )


JudgeCaller = Callable[[str], Awaitable[str]]


def _fake_caller(*, response: str) -> JudgeCaller:
    async def _call(_: str) -> str:
        return response

    return _call


# ---------------------------------------------------------------------------
# parse_judge_response — permissive JSON extraction (judge models add prose)
# ---------------------------------------------------------------------------


def test_parses_strict_json_complete_true() -> None:
    verdict = parse_judge_response('{"complete": true, "reason": "done"}')
    assert verdict.complete is True
    assert verdict.parse_succeeded is True
    assert "done" in verdict.reason


def test_parses_strict_json_complete_false() -> None:
    verdict = parse_judge_response('{"complete": false, "reason": "needs WebFetch"}')
    assert verdict.complete is False
    assert verdict.parse_succeeded is True


def test_extracts_json_from_prose_envelope() -> None:
    # Cheap models often wrap structured output in prose. The parser must
    # accept that without forcing strict-JSON behavior (which would burn the
    # parse-failure budget on a working answer).
    raw = (
        "Sure! Based on the work above, the objective is complete.\n"
        '{"complete": true, "reason": "answer produced"}\n'
        "Hope that helps!"
    )
    verdict = parse_judge_response(raw)
    assert verdict.complete is True
    assert verdict.parse_succeeded is True


def test_returns_parse_failed_on_unparsable_response() -> None:
    verdict = parse_judge_response("I think it is done, yes.")
    assert verdict.parse_succeeded is False
    # Fail-CLOSED-ish: on parse failure, the engine should treat the verdict
    # as NOT complete (so it loops once more) — but the parse-failure budget
    # caps how many times that can happen before terminating.
    assert verdict.complete is False


def test_returns_parse_failed_on_malformed_json_brace() -> None:
    verdict = parse_judge_response("{not really json}")
    assert verdict.parse_succeeded is False


def test_missing_complete_field_treated_as_not_complete() -> None:
    verdict = parse_judge_response('{"reason": "looks ok"}')
    # No explicit complete:true → fail-CLOSED (do not declare success).
    assert verdict.complete is False


def test_reason_is_length_bounded() -> None:
    huge = "x" * 5000
    verdict = parse_judge_response(f'{{"complete": false, "reason": "{huge}"}}')
    assert len(verdict.reason) <= 240
    assert verdict.parse_succeeded is True


# ---------------------------------------------------------------------------
# JUDGE_PROMPT_TEMPLATE — must name both objective and final_text
# ---------------------------------------------------------------------------


def test_prompt_template_carries_objective_and_final_text_placeholders() -> None:
    rendered = JUDGE_PROMPT_TEMPLATE.format(
        objective="Analyze Tesla 10-K",
        final_text="Refreshed Plan: I'll fetch the 10-K next.",
    )
    assert "Analyze Tesla 10-K" in rendered
    assert "Refreshed Plan: I'll fetch the 10-K next." in rendered
    # Must demand strict JSON to keep parse failures rare.
    assert "JSON" in rendered or "json" in rendered
    # Fail-CLOSED instruction so an unsure model doesn't keep agents looping.
    assert "unsure" in rendered.lower()


# ---------------------------------------------------------------------------
# evaluate_goal_completion — the async driver
# ---------------------------------------------------------------------------


def test_evaluate_returns_complete_when_judge_says_complete() -> None:
    verdict = asyncio.run(
        evaluate_goal_completion(
            policy=_policy("Tesla 10-K analysis"),
            final_text="Final analysis: revenue X, risks Y, conclusion Z.",
            judge_caller=_fake_caller(
                response='{"complete": true, "reason": "produced final analysis"}'
            ),
        )
    )
    assert verdict.complete is True
    assert verdict.parse_succeeded is True


def test_evaluate_returns_incomplete_when_judge_says_incomplete() -> None:
    verdict = asyncio.run(
        evaluate_goal_completion(
            policy=_policy("Tesla 10-K analysis"),
            final_text="Refreshed Plan: I'll fetch the 10-K next.",
            judge_caller=_fake_caller(
                response='{"complete": false, "reason": "plan only, no execution"}'
            ),
        )
    )
    assert verdict.complete is False
    assert verdict.parse_succeeded is True


def test_evaluate_surfaces_judge_exception_as_parse_failure() -> None:
    # A judge that raises (network error, bad provider config, etc.) must NOT
    # crash the engine — return a parse-failed verdict so the engine's
    # parse-failure budget can decide whether to terminate.
    async def _raising(_: str) -> str:
        raise RuntimeError("judge api down")

    verdict = asyncio.run(
        evaluate_goal_completion(
            policy=_policy(),
            final_text="anything",
            judge_caller=_raising,
        )
    )
    assert verdict.parse_succeeded is False
    assert verdict.complete is False


@pytest.mark.parametrize("empty", ["", "   "])
def test_evaluate_handles_empty_final_text(empty: str) -> None:
    # Empty turn output: still call the judge (with an "(empty)" sentinel) so
    # the engine can decide whether the goal is met or whether the model should
    # keep going. Don't auto-declare complete or incomplete.
    captured: dict[str, str] = {}

    async def _capturing(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"complete": false, "reason": "no output"}'

    verdict = asyncio.run(
        evaluate_goal_completion(
            policy=_policy(),
            final_text=empty,
            judge_caller=_capturing,
        )
    )
    assert verdict.parse_succeeded is True
    assert verdict.complete is False
    # The prompt MUST contain the empty-text sentinel so the judge can decide
    # rationally instead of confabulating completeness from a blank.
    assert "(empty)" in captured["prompt"]


def test_evaluate_calls_judge_exactly_once() -> None:
    # Latency budget is "1 cheap judge call per clean-break decision" — making
    # multiple calls per evaluation defeats the design's whole reason for being.
    call_count = 0

    async def _counting(_: str) -> str:
        nonlocal call_count
        call_count += 1
        return '{"complete": true, "reason": "ok"}'

    asyncio.run(
        evaluate_goal_completion(
            policy=_policy(),
            final_text="text",
            judge_caller=_counting,
        )
    )
    assert call_count == 1


# ---------------------------------------------------------------------------
# JudgeVerdict — minimal shape the engine consumes
# ---------------------------------------------------------------------------


def test_verdict_fields() -> None:
    v = JudgeVerdict(complete=True, reason="ok", parse_succeeded=True)
    assert v.complete is True
    assert v.reason == "ok"
    assert v.parse_succeeded is True
