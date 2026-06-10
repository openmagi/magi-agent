"""GAIA cross-verified precision pass — C1 + C2: web-fact and numeric re-check.

Default-OFF via ``MAGI_GAIA_PRECISION`` (off | audit | enforce).

This module lives in the benchmark layer (``benchmarks/gaia/``) and reuses the
general web seams via injected callables — no coupling to any specific provider.

Design principles (from the learnings doc, v4 run):
- P4 latency: at most 1 extra search + 1 extra fetch per question.
- P5 audit-first / guarded: correction fires ONLY on a conflict signal grounded
  in fetched evidence — never a free re-guess.
- P8 evidence: no free reguess; all corrections must be evidenced.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

__all__ = ["cross_verify_fact", "recompute_numeric"]

_logger = logging.getLogger(__name__)

# Short-fact heuristic: if draft is ≤ this many chars it may be a web fact.
_MAX_SHORT_FACT_CHARS = 50

# Regex for numeric draft detection: optional sign, digits, optional decimal
_NUMERIC_RE = re.compile(r"^\s*[+-]?\d+(?:[.,]\d+)?\s*$")


def _is_numeric(value: str) -> bool:
    """Return True if *value* looks like a bare number (int or float)."""
    return bool(_NUMERIC_RE.match(value.strip()))


def _extract_verdict_and_value(model_output: str) -> tuple[str, str | None, str | None]:
    """Parse model output for VERDICT / ADOPTED_VALUE / SOURCE_URL lines.

    Returns (verdict, adopted_value, source_url).
    verdict is one of 'AGREE', 'CONFLICT', 'UNVERIFIABLE', or '' (unknown).
    """
    verdict = ""
    adopted_value: str | None = None
    source_url: str | None = None

    for line in model_output.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped.split(":", 1)[1].strip().upper()
        elif stripped.upper().startswith("ADOPTED_VALUE:"):
            adopted_value = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("VALUE:"):
            if not adopted_value:
                adopted_value = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("SOURCE_URL:"):
            source_url = stripped.split(":", 1)[1].strip()

    return verdict, adopted_value, source_url


# ---------------------------------------------------------------------------
# C1: cross_verify_fact
# ---------------------------------------------------------------------------


def cross_verify_fact(
    question: str,
    draft: str,
    *,
    search_fn: Callable[[str], str],
    fetch_fn: Callable[[str], str],
    model: Callable[[str], str],
    max_extra_searches: int = 1,
    max_extra_fetches: int = 1,
) -> str:
    """Cross-verify a factual draft answer with one additional search.

    Parameters
    ----------
    question:
        The original GAIA question.
    draft:
        The agent's draft answer string.
    search_fn:
        ``(query: str) -> str`` — returns raw search result text.
    fetch_fn:
        ``(url: str) -> str`` — returns fetched page text.
    model:
        ``(prompt: str) -> str`` — LLM callable for agreement judgment.
    max_extra_searches:
        Cap on extra searches (default 1). Pass 0 to disable.
    max_extra_fetches:
        Cap on extra fetches on conflict (default 1). Pass 0 to disable.

    Returns
    -------
    str
        Corrected answer if a grounded conflict was resolved; otherwise draft.
        Never raises.
    """
    try:
        return _cross_verify_fact_impl(
            question,
            draft,
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            model=model,
            max_extra_searches=max_extra_searches,
            max_extra_fetches=max_extra_fetches,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("cross_verify_fact: fail-open on exception: %s", exc)
        return draft


def _cross_verify_fact_impl(
    question: str,
    draft: str,
    *,
    search_fn: Callable[[str], str],
    fetch_fn: Callable[[str], str],
    model: Callable[[str], str],
    max_extra_searches: int,
    max_extra_fetches: int,
) -> str:
    if max_extra_searches < 1:
        return draft

    # Issue one extra targeted search with a rephrased query
    search_query = f"verify {question}"
    search_result = search_fn(search_query)

    # Ask the model whether the search evidence agrees or conflicts with draft
    judge_prompt = (
        f"Question: {question}\n"
        f"Draft answer: {draft}\n"
        f"New evidence from search:\n{search_result}\n\n"
        f"Does the new evidence AGREE with the draft answer, CONFLICT with it, "
        f"or is it UNVERIFIABLE?\n"
        f"Reply with exactly:\n"
        f"VERDICT: AGREE | CONFLICT | UNVERIFIABLE\n"
        f"If CONFLICT, also include:\n"
        f"ADOPTED_VALUE: <value supported by evidence>\n"
        f"SOURCE_URL: <most authoritative URL if available, else 'none'>"
    )
    model_output = model(judge_prompt)
    verdict, adopted_value, source_url = _extract_verdict_and_value(model_output)

    if verdict != "CONFLICT":
        # AGREE, UNVERIFIABLE, or unknown → return draft unchanged
        return draft

    # Conflict detected — fetch the primary source if allowed
    if max_extra_fetches < 1 or not adopted_value:
        return draft

    # Fetch the most authoritative URL
    fetch_url = source_url if (source_url and source_url != "none") else "https://example.com"
    page_text = fetch_fn(fetch_url)

    # Ask model to confirm: given the fetched primary source, is adopted_value correct?
    confirm_prompt = (
        f"Question: {question}\n"
        f"Draft answer: {draft}\n"
        f"Candidate correction: {adopted_value}\n"
        f"Primary source page text (excerpt):\n{page_text[:4000]}\n\n"
        f"Is the candidate correction supported by the primary source?\n"
        f"Reply with:\n"
        f"VERDICT: AGREE | CONFLICT | UNVERIFIABLE\n"
        f"ADOPTED_VALUE: <final value to use>"
    )
    confirm_output = model(confirm_prompt)
    confirm_verdict, confirm_value, _ = _extract_verdict_and_value(confirm_output)

    # Adopt if primary source confirms the correction
    if confirm_verdict == "CONFLICT" and confirm_value:
        _logger.info(
            "cross_verify_fact: corrected %r → %r (evidence grounded)",
            draft,
            confirm_value,
        )
        return confirm_value

    # Also accept AGREE when we have a confirmed adopted value
    if confirm_verdict == "AGREE" and adopted_value:
        _logger.info(
            "cross_verify_fact: corrected %r → %r (conflict confirmed by fetch)",
            draft,
            adopted_value,
        )
        return adopted_value

    return draft


# ---------------------------------------------------------------------------
# C2: recompute_numeric
# ---------------------------------------------------------------------------

# Regex to extract a Python code block from model output
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_code_block(text: str) -> str | None:
    """Extract the first Python code block from *text*, or None."""
    match = _CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def recompute_numeric(
    question: str,
    draft: str,
    evidence: str,
    *,
    exec_fn: Callable[[str], str],
    model: Callable[[str], str],
) -> str:
    """Re-derive a numeric draft answer via code execution.

    Parameters
    ----------
    question:
        The original GAIA question.
    draft:
        The agent's draft answer (must look numeric to trigger).
    evidence:
        Evidence / context text already gathered by the agent.
    exec_fn:
        ``(code: str) -> str`` — executes Python code, returns stdout/result.
    model:
        ``(prompt: str) -> str`` — LLM callable that emits Python code.

    Returns
    -------
    str
        Code result if it disagrees with draft; otherwise draft unchanged.
        Never raises.
    """
    try:
        return _recompute_numeric_impl(
            question, draft, evidence, exec_fn=exec_fn, model=model
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("recompute_numeric: fail-open on exception: %s", exc)
        return draft


def _recompute_numeric_impl(
    question: str,
    draft: str,
    evidence: str,
    *,
    exec_fn: Callable[[str], str],
    model: Callable[[str], str],
) -> str:
    if not _is_numeric(draft):
        return draft

    # Ask model to emit Python that re-derives the answer
    code_prompt = (
        f"Question: {question}\n"
        f"Evidence / stated quantities:\n{evidence}\n\n"
        f"Write a short Python snippet (≤10 lines) that re-derives the numeric "
        f"answer from the stated quantities. Store the final answer in a variable "
        f"named `result`. Return ONLY a Python code block, no explanation.\n"
        f"Example format:\n"
        f"```python\nresult = 3 + 4\n```"
    )
    model_output = model(code_prompt)
    code = _extract_code_block(model_output)
    if not code:
        return draft

    exec_result = exec_fn(code)

    # Normalize: strip whitespace
    exec_result_stripped = exec_result.strip()
    draft_stripped = draft.strip()

    if exec_result_stripped == "ERROR" or not exec_result_stripped:
        return draft

    # Compare numerically when possible, else string compare
    try:
        exec_num = float(exec_result_stripped.replace(",", "."))
        draft_num = float(draft_stripped.replace(",", "."))
        if abs(exec_num - draft_num) < 1e-9:
            return draft  # agrees — keep draft
    except ValueError:
        if exec_result_stripped == draft_stripped:
            return draft  # string equality — keep draft

    # Disagrees → adopt code result
    _logger.info(
        "recompute_numeric: corrected %r → %r (code execution)",
        draft,
        exec_result_stripped,
    )
    return exec_result_stripped
