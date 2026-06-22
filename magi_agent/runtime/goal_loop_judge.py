"""PR-C: clean-break judge call for the goal loop.

When the engine reaches its "clean break" point (the model emitted text and
has no further tool calls — historically the natural end-of-turn) AND a
:class:`GoalLoopPolicy` is active for this turn (published by PR-B's
ContextVar), this module's evaluator asks a small judge model whether the
ORIGINAL objective has been completed by the work above. The engine then
either:

  - terminates the turn normally (complete=true);
  - re-invokes ``run_async`` with the policy's generic continuation prompt
    (complete=false); or
  - terminates on the parse-failure budget (fail-CLOSED so a broken judge
    can't loop forever).

Hermetic by construction: ``evaluate_goal_completion`` takes a
``judge_caller: str -> Awaitable[str]`` so tests inject a fake callable and
no network / litellm dependency is pulled at import time.

Design reference (host repo):
  docs/plans/2026-06-21-magi-goal-loop-clean-break-judge-design.md
  Section 4.2.3 (judge prompt shape) + Section 4.2.5 (termination).
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from magi_agent.runtime.goal_loop_policy import GoalLoopPolicy

#: Minimal, model-agnostic judge prompt. Anchored on the ORIGINAL objective
#: (not paraphrased) and the agent's final-text emission for this turn. The
#: "unsure → false" instruction is fail-CLOSED — an uncertain judge keeps the
#: agent moving rather than declaring premature completion.
JUDGE_PROMPT_TEMPLATE = (
    "ORIGINAL OBJECTIVE:\n"
    "{objective}\n"
    "\n"
    "THE AGENT JUST PRODUCED THIS FINAL TEXT WITHOUT FURTHER TOOL CALLS:\n"
    "{final_text}\n"
    "\n"
    "Has the original objective been fully completed by the work above?\n"
    "Answer ONLY with a JSON object: "
    '{{"complete": true|false, "reason": "<short>"}}.\n'
    "If unsure, answer false."
)

#: Bound the verdict's free-form ``reason`` field so a verbose judge cannot
#: balloon the engine's status-event payload.
_REASON_MAX_CHARS = 240


JudgeCaller = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class JudgeVerdict:
    """Minimal verdict the engine acts on.

    ``parse_succeeded`` distinguishes a real "not complete" vote from a
    parse failure so the engine can count parse failures against the
    policy's ``judge_parse_failures_budget`` (fail-CLOSED ceiling) without
    conflating the two.
    """

    complete: bool
    reason: str
    parse_succeeded: bool


def parse_judge_response(raw: str) -> JudgeVerdict:
    """Permissive JSON extraction.

    Cheap-tier judge models routinely wrap structured output in a prose
    envelope ("Sure, here is the answer:\\n{...}"). Forcing strict JSON
    would burn the per-turn parse-failure budget on otherwise-working
    answers, so we extract the FIRST balanced ``{...}`` block and parse
    that. Any parse failure surfaces as ``parse_succeeded=False`` with a
    fail-CLOSED ``complete=False``.
    """
    if not isinstance(raw, str):
        return JudgeVerdict(complete=False, reason="non_string_response", parse_succeeded=False)
    match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    if not match:
        return JudgeVerdict(complete=False, reason="no_json_found", parse_succeeded=False)
    try:
        payload = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return JudgeVerdict(complete=False, reason="json_parse_failed", parse_succeeded=False)
    if not isinstance(payload, dict):
        return JudgeVerdict(complete=False, reason="non_object_json", parse_succeeded=False)
    # Fail-CLOSED: anything but an explicit boolean ``true`` for ``complete``
    # is treated as not-complete. The missing-field case (no "complete" key)
    # therefore correctly stays incomplete instead of silently terminating.
    complete = payload.get("complete") is True
    reason_raw = payload.get("reason", "")
    if not isinstance(reason_raw, str):
        reason_raw = ""
    reason = reason_raw.strip()[:_REASON_MAX_CHARS]
    return JudgeVerdict(complete=complete, reason=reason, parse_succeeded=True)


async def evaluate_goal_completion(
    *,
    policy: GoalLoopPolicy,
    final_text: str,
    judge_caller: JudgeCaller,
) -> JudgeVerdict:
    """Ask the judge whether *policy.objective* is complete given *final_text*.

    Exactly one judge call per evaluation — this is the design's latency
    budget. The judge is invoked even on empty *final_text* (with an
    "(empty)" sentinel) so the engine can decide rationally instead of
    confabulating completeness from a blank turn.

    Never raises: any exception in *judge_caller* becomes a parse-failed
    verdict (the engine counts that against the policy's parse-failure
    budget — see Section 4.2.5 of the design doc).
    """
    rendered = JUDGE_PROMPT_TEMPLATE.format(
        objective=policy.objective,
        final_text=(final_text or "").strip() or "(empty)",
    )
    try:
        raw = await judge_caller(rendered)
    except Exception:  # noqa: BLE001 — judge failure must not crash the turn.
        return JudgeVerdict(
            complete=False,
            reason="judge_call_failed",
            parse_succeeded=False,
        )
    return parse_judge_response(raw)


__all__ = [
    "JUDGE_PROMPT_TEMPLATE",
    "JudgeCaller",
    "JudgeVerdict",
    "evaluate_goal_completion",
    "parse_judge_response",
]
