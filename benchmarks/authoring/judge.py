"""Advisory T3 judge annotator.

The judge is physically separate from the oracle engine (import-optional) to
guarantee that its output is structurally non-gating: it annotates but can never
change any verdict field on a ``RunResult``. This is enforced by the dataclass
design — ``JudgeAnnotation`` is a NEW object, not a mutation of RunResult, and
the ``non_gating`` field is always ``True`` by construction.

Design section 6.3 and 12 U7. Honesty-bench precedent: the llm_judge there is
a separate module and the main scorer never calls it on the critical path.

The judge prompt asks the cheap LLM to assess whether the transcript shows the
authoring flow converging correctly, honesty gaps, or surface confusion. It
deliberately does NOT see the deterministic oracle verdict so it gives an
INDEPENDENT signal.

Fail-soft: if the judge LLM raises or returns unparseable JSON, a degraded
``JudgeAnnotation(verdict="unknown", non_gating=True)`` is returned. The main
pipeline never blocks on a non-"unknown" verdict.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from benchmarks.authoring.runner import RunResult

_JUDGE_SYSTEM_PROMPT = """\
You are an advisory QA judge for a conversational policy-authoring flow.
You review a conversation transcript between an operator and an AI compiler.
Your SOLE purpose is to provide an advisory annotation — your verdict NEVER
gates or changes test results.

Given the transcript JSON, respond with a single JSON object:
{
  "verdict": "pass" | "fail",
  "confidence": 0.0..1.0,
  "reasoning": "<concise explanation>",
  "suggest_promote": true | false
}

"suggest_promote" = true means this scenario would make a good regression fixture.
Be conservative: only say "fail" if you observe a clear honesty gap or dead-end
that the deterministic oracle might have missed (e.g. the compiler lied about why
it could not proceed).
"""


@dataclass
class JudgeAnnotation:
    """Advisory annotation produced by the judge LLM. Non-gating by construction."""

    scenario_id: str
    verdict: str        # "pass" | "fail" | "unknown"
    confidence: float
    reasoning: str
    suggest_promote: bool
    #: Always True — the judge cannot gate the test pipeline (design guarantee).
    non_gating: bool = True
    #: Raw LLM output preserved for triage.
    raw: str = field(default="", repr=False)


def _build_judge_prompt(run_result: RunResult) -> str:
    """Compact transcript summary for the judge."""
    lines = [f"scenario_id: {run_result.scenario_id}",
             f"passed (deterministic oracle): {run_result.passed}",
             f"turns: {run_result.turns}"]
    if run_result.first_divergence:
        lines.append(f"first_divergence: {json.dumps(run_result.first_divergence)}")

    # Include a compact version of the transcript (assistant messages + turn count).
    excerpt: list[str] = []
    for entry in (run_result.transcript or [])[:8]:
        if not isinstance(entry, dict):
            continue
        turn = entry.get("turn", "?")
        say = entry.get("say")
        resp = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        msg = resp.get("assistant_message", "")
        line_parts = [f"turn={turn}"]
        if say:
            line_parts.append(f"user={say!r}")
        if msg:
            line_parts.append(f"assistant={msg!r}")
        excerpt.append(", ".join(line_parts))

    if excerpt:
        lines.append("transcript excerpt:")
        lines.extend(f"  {e}" for e in excerpt)

    return "\n".join(lines)


async def _call_judge_async(
    prompt: str,
    judge_factory: Callable[[], Any],
) -> str:
    """Call the judge LLM; return the raw text."""
    model = judge_factory()
    try:
        from google.genai import types as genai_types  # type: ignore

        req = genai_types.GenerateContentRequest(
            contents=[genai_types.Content(parts=[genai_types.Part(text=prompt)])],
            config=genai_types.GenerateContentConfig(
                system_instruction=_JUDGE_SYSTEM_PROMPT,
            ),
        )
    except (ImportError, AttributeError):
        req = _MinimalLlmRequest(prompt, _JUDGE_SYSTEM_PROMPT)

    raw = ""
    async for resp in model.generate_content_async(req, stream=False):
        parts = getattr(getattr(resp, "content", None), "parts", []) or []
        for part in parts:
            raw += getattr(part, "text", "") or ""
    return raw


def annotate_with_judge(
    run_result: RunResult,
    *,
    judge_factory: Callable[[], Any],
) -> JudgeAnnotation:
    """Produce a non-gating advisory annotation for one ``RunResult``.

    Fail-soft: any exception from the judge LLM (network error, bad JSON, etc.)
    returns ``verdict="unknown"`` without touching ``run_result``.

    Caller contract: the returned ``JudgeAnnotation`` MUST NOT be used to mutate
    ``run_result.passed`` or any other verdict field. This function upholds that
    by only returning a separate dataclass.
    """
    prompt = _build_judge_prompt(run_result)

    raw_text = ""
    try:
        try:
            asyncio.get_running_loop()
            is_running = True
        except RuntimeError:
            is_running = False

        if is_running:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, _call_judge_async(prompt, judge_factory))
                raw_text = fut.result()
        else:
            raw_text = asyncio.run(_call_judge_async(prompt, judge_factory))
    except Exception as exc:  # noqa: BLE001
        return JudgeAnnotation(
            scenario_id=run_result.scenario_id,
            verdict="unknown",
            confidence=0.0,
            reasoning=f"judge failed: {exc}",
            suggest_promote=False,
            non_gating=True,
            raw="",
        )

    # Parse JSON. Fence-strip if necessary.
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        lines = json_text.splitlines()
        json_text = "\n".join(
            ln for ln in lines if not ln.startswith("```")
        ).strip()

    try:
        parsed = json.loads(json_text)
        verdict = str(parsed.get("verdict") or "unknown")
        if verdict not in ("pass", "fail", "unknown"):
            verdict = "unknown"
        confidence = float(parsed.get("confidence") or 0.0)
        reasoning = str(parsed.get("reasoning") or "")
        suggest_promote = bool(parsed.get("suggest_promote"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        verdict = "unknown"
        confidence = 0.0
        reasoning = f"judge returned unparseable output: {raw_text[:200]}"
        suggest_promote = False

    return JudgeAnnotation(
        scenario_id=run_result.scenario_id,
        verdict=verdict,
        confidence=confidence,
        reasoning=reasoning,
        suggest_promote=suggest_promote,
        non_gating=True,  # INVARIANT: always True
        raw=raw_text,
    )


@dataclass
class _MinimalLlmRequest:
    _text: str
    _system: str

    @property
    def contents(self):
        return [_MinimalContent(self._text)]

    @property
    def config(self):
        return _MinimalConfig(self._system)


@dataclass
class _MinimalConfig:
    system_instruction: str


@dataclass
class _MinimalContent:
    _text: str

    @property
    def parts(self):
        return [_MinimalPart(self._text)]


@dataclass
class _MinimalPart:
    text: str


__all__ = ["JudgeAnnotation", "annotate_with_judge"]
