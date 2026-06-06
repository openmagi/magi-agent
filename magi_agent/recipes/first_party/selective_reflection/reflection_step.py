"""Hidden LLM critique pass for the selective reflection gate.

``run_reflection_step()`` is the core execution unit.  It accepts an injected
``model_caller`` so the entire step can be tested against a fake model without
any real provider traffic — following the pattern established by
``MagiEditRetryReflectionPlugin`` (fake_provider_only=True in tests).

Critique prompt
---------------
The prompt is deliberately concise (< 500 tokens) to minimise the cost and
latency of each pass.  It asks the model to produce a verdict of exactly one of:
    VERDICT: PASS
    VERDICT: ISSUES_FOUND

followed by a short issues summary (one paragraph maximum).  Parsing is
deterministic (regex, no secondary LLM call).

A ``verdict="pass"`` result means the draft is committed as-is; the
``hidden_user_message`` field is empty.  A ``verdict="issues_found"`` result
injects the ``hidden_user_message`` via the same channel used by
``MagiEditRetryReflectionPlugin`` — a ``role="user"`` function_response Part
that the model sees as a tool result but that is never shown to the end user.

``max_depth`` enforcement
--------------------------
When ``depth >= max_depth``, ``run_reflection_step()`` returns immediately with
``verdict="pass"`` (fail-open) regardless of actual critique content.  The depth
counter is maintained by the caller (the hook) across successive passes within a
single commit attempt.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from magi_agent.recipes.first_party.selective_reflection.complexity_signal import (
    ComplexityBand,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_policy import (
    ReflectionPolicy,
)


CritiqueVerdict = Literal["pass", "issues_found"]

# Maximum characters to include from the issues summary in the hidden message.
_ISSUES_MAX_CHARS = 1_200

# Regex to parse the model's verdict line.  Accepts minor formatting noise.
_VERDICT_RE = re.compile(
    r"VERDICT\s*:\s*(PASS|ISSUES[_ ]FOUND)",
    re.IGNORECASE,
)
_ISSUES_RE = re.compile(
    r"(?:ISSUES?\s*[:|-]?\s*)(.*?)(?:\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# ---------------------------------------------------------------------------
# Critique prompt template
# ---------------------------------------------------------------------------
_CRITIQUE_PROMPT_TEMPLATE = """\
You are a self-critique reviewer.  The agent has produced the draft answer below.
Your task: identify *concrete* errors, missing citations, or logical inconsistencies.

Criteria for issuing a PASS:
- All factual claims are supported by the evidence the agent gathered.
- No step in the reasoning chain is logically inconsistent.
- The answer directly addresses the question asked.
- Vague uncertainty (e.g. "I am not sure") is NOT a concrete error — return PASS.

Criteria for issuing ISSUES_FOUND:
- A specific factual claim contradicts the evidence.
- A necessary reasoning step is missing or inverted.
- The answer does not address the question asked.

You MUST begin your response with exactly one of:
    VERDICT: PASS
    VERDICT: ISSUES_FOUND

If ISSUES_FOUND, follow with at most one paragraph (< 200 words) explaining the
specific errors.  No elaboration beyond that.

--- TOOL CALL SUMMARY (what the agent did) ---
{tool_call_summary}

--- DRAFT ANSWER ---
{draft_answer}
"""


def build_reflection_critique_prompt(
    *,
    draft_answer: str,
    tool_call_summary: str,
) -> str:
    """Build the critique prompt sent to the hidden LLM call.

    The combined prompt is capped at ~500 tokens of template overhead plus the
    supplied inputs.  Callers should truncate ``draft_answer`` and
    ``tool_call_summary`` if they are very long.
    """
    return _CRITIQUE_PROMPT_TEMPLATE.format(
        draft_answer=draft_answer[:3_000],
        tool_call_summary=tool_call_summary[:2_000],
    )


def parse_critique_response(raw: str) -> tuple[CritiqueVerdict, str]:
    """Parse the model's critique response.

    Returns a ``(verdict, issues_summary)`` pair.  If no verdict line is found,
    defaults to ``("pass", "")`` — a parse failure is treated as clean (fail-open
    approach to avoid false-positive blocking).

    Parameters
    ----------
    raw:
        The full text returned by the model_caller.

    Returns
    -------
    verdict:
        ``"pass"`` or ``"issues_found"``.
    issues_summary:
        Non-empty only when ``verdict == "issues_found"``.  Truncated to
        ``_ISSUES_MAX_CHARS`` characters.
    """
    m = _VERDICT_RE.search(raw)
    if m is None:
        # Parse failure → fail-open: treat as PASS.
        return "pass", ""

    verdict_text = m.group(1).upper().replace(" ", "_")
    if verdict_text == "PASS":
        return "pass", ""

    # Extract issues paragraph that follows the VERDICT line.
    rest = raw[m.end():].strip()
    # Take at most one paragraph.
    paragraph = rest.split("\n\n")[0].strip() if rest else ""
    issues = paragraph[:_ISSUES_MAX_CHARS] if paragraph else ""
    if not issues:
        # ISSUES_FOUND but no actual issues → treat as PASS (over-conservative).
        return "pass", ""
    return "issues_found", issues


@dataclass(frozen=True)
class ReflectionResult:
    """Outcome of a single reflection pass.

    Attributes
    ----------
    verdict:
        Whether the critique found issues.
    depth:
        Which pass this was (1-indexed).
    max_depth:
        The configured maximum for this invocation.
    issues_summary:
        Empty when ``verdict == "pass"``.
    hidden_user_message:
        Non-empty when ``verdict == "issues_found"``; injected into the next
        model turn via the ``hidden_user_message`` channel.
    """

    verdict: CritiqueVerdict
    depth: int
    max_depth: int
    issues_summary: str
    hidden_user_message: str


def _build_hidden_message(issues_summary: str) -> str:
    return "\n\n".join(
        (
            "Your draft answer was reviewed by a self-critique pass.",
            f"Issues identified:\n{issues_summary}",
            "Produce a corrected draft that addresses the specific issues above.",
            "Do not add new claims beyond what your evidence already supports.",
            "Keep the answer concise and directly responsive to the original question.",
        )
    )


async def run_reflection_step(
    *,
    draft_answer: str,
    tool_call_summary: str,
    policy: ReflectionPolicy,
    band: ComplexityBand,
    depth: int,
    model_caller: Callable[..., Awaitable[str]],
) -> ReflectionResult:
    """Run one reflection pass and return the result.

    This function is async to allow the ``model_caller`` to be an async function
    (e.g. wrapping a real LLM call).  In tests, pass a coroutine-returning fake.

    Parameters
    ----------
    draft_answer:
        The agent's current best answer text.
    tool_call_summary:
        A compressed summary of what the agent did (tool names + key results).
        Should *not* include raw transcripts — only a concise digest.
    policy:
        The active ``ReflectionPolicy``.
    band:
        The ``ComplexityBand`` for the current task.
    depth:
        The current pass number (1-indexed).  The caller increments this before
        each call; ``depth=1`` means this is the first critique pass.
    model_caller:
        An injected async callable ``(prompt: str) -> str`` that calls the LLM.
        The function signature matches a simple wrapper around the provider SDK.

    Returns
    -------
    ``ReflectionResult`` with the parsed verdict and, if issues were found, a
    ``hidden_user_message`` ready for injection.
    """
    decision = policy.decide(band)
    effective_max = policy.effective_max_depth(decision)

    # Depth guard: after max_depth passes, commit as-is (fail-open).
    if depth > effective_max or decision == "skip":
        return ReflectionResult(
            verdict="pass",
            depth=depth,
            max_depth=effective_max,
            issues_summary="",
            hidden_user_message="",
        )

    prompt = build_reflection_critique_prompt(
        draft_answer=draft_answer,
        tool_call_summary=tool_call_summary,
    )
    raw_response = await model_caller(prompt)
    verdict, issues_summary = parse_critique_response(raw_response)

    hidden_message = _build_hidden_message(issues_summary) if verdict == "issues_found" else ""

    return ReflectionResult(
        verdict=verdict,
        depth=depth,
        max_depth=effective_max,
        issues_summary=issues_summary,
        hidden_user_message=hidden_message,
    )


__all__ = [
    "CritiqueVerdict",
    "ReflectionResult",
    "build_reflection_critique_prompt",
    "parse_critique_response",
    "run_reflection_step",
]
