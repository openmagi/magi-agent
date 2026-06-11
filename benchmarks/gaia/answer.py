"""GAIA system prompt and FINAL ANSWER extraction."""
from __future__ import annotations

import re
from collections.abc import Mapping

GAIA_SYSTEM_PROMPT = (
    "You are a general AI assistant solving a GAIA benchmark question. "
    "Use the available tools (web search/fetch, file reading, shell/python) to "
    "research and compute the answer. "
    "\n\n"
    "Available tools and when to use them:\n"
    "- PDF page/footnote lookups → use DocumentSearch (targeted term search "
    "returning page + snippet; prefer over reading the whole document).\n"
    "- .zip attachments → use ArchiveExtract first to list and read files "
    "inside the archive before trying anything else.\n"
    "- Spreadsheet structure → use XLSXInfo to inspect sheet names/columns, "
    "then XLSXRead with cellRange to fetch only the relevant cells.\n"
    "- Numbers, tables, or coordinates in an image → use ImageUnderstand with "
    "structured extraction (not prose) to read the exact values, then run any "
    "arithmetic on them with the Bash/Calculation tool.\n"
    "- Web facts → use web_search for a fast Brave search, then web_fetch the "
    "best-matching URL for full page content.\n"
    "\n"
    "If an attachment file is present in the working directory, use the "
    "appropriate file tool to read it: ImageUnderstand for images "
    "(.png/.jpg/.jpeg/.gif/.webp/.bmp), DocumentRead for documents "
    "(.pdf/.docx/.pptx/.xml/.csv/.txt), or XLSXRead for spreadsheets "
    "(.xlsx/.xls). "
    "If a tool returns an error, try Bash or Python as an alternative before "
    "giving up. "
    "IMPORTANT — YOU MUST ALWAYS COMMIT TO AN ANSWER: "
    "Never say 'unable to determine', 'cannot determine', 'I do not know', "
    "or leave the answer blank. "
    "Never abstain. "
    "In GAIA, an abstention scores 0 — the same as a wrong answer — so you "
    "must always provide your single best guess even when uncertain. "
    "If you are not certain, reason through the most probable answer and commit "
    "to that best guess. "
    "Report your reasoning, then finish with exactly one line:\n"
    "FINAL ANSWER: <answer>\n"
    "YOUR FINAL ANSWER should be a number OR as few words as possible OR a "
    "comma separated list of numbers and/or strings. If asked for a number, do "
    "not use commas or units (e.g. $ or %) unless specified. If asked for a "
    "string, do not use articles or abbreviations, and write digits in plain "
    "text unless specified. Apply these rules to each element of a list."
)


_COMPUTE_VIA_CODE_REMINDER = (
    "\n\n"
    "COMPUTE NUMERIC ANSWERS WITH CODE: for any arithmetic, unit conversion, "
    "statistics (mean/median/sum/average), or checksum/validation step, write "
    "and run code with the Bash or Calculation tool and report the value the "
    "tool returned — never compute it in your head. This applies to NUMERIC "
    "computation only; it does NOT change how you read inputs from an image — "
    "still use ImageUnderstand with structured extraction to obtain the exact "
    "values first, then run the arithmetic on them with code."
)


# Output-format-adherence advertisement (benchmark prompt layer). This is the
# GAIA-facing wording of the general output-format-adherence capability
# implemented in magi_agent/cli/tool_runtime.output_format_adherence_block.
# Keeping the GAIA-specific phrasing HERE (not in first-party logic) preserves
# the anti-overfit boundary: the general mechanism is benchmark-agnostic; only
# this advertisement names GAIA's exact answer conventions.
GAIA_FORMAT_ADHERENCE_NOTE = (
    "FORMAT ADHERENCE — before you write the FINAL ANSWER line, re-read the "
    "question's exact output requirements and conform to them:\n"
    "- Units & scale: answer in the units and at the scale asked for "
    "(e.g. '17 thousand' vs 17000); convert when the question specifies a unit "
    "or scale, and omit units when none are requested.\n"
    "- Rounding precision: round to the precision the question requests "
    "(e.g. 'to the nearest picometer' -> 1.456, not 1.46); do not over- or "
    "under-round.\n"
    "- Name & format: use the canonical name or exact format requested (full "
    "name vs abbreviation, the exact symbol/character asked for such as a grave "
    "accent rather than a backtick, the requested ordering/separators).\n"
    "- Do not add units, articles, words, or explanation the question did not "
    "request; the FINAL ANSWER must be exactly what was asked and nothing more."
)


def gaia_system_prompt(env: Mapping[str, str] | None = None) -> str:
    """Return the GAIA system prompt, optionally with the compute-via-code
    reminder appended.

    When ``MAGI_COMPUTE_VIA_CODE_ENABLED`` is falsy (default), returns
    :data:`GAIA_SYSTEM_PROMPT` byte-identically. When the flag is on, appends a
    scoped reminder telling the agent to compute numeric results with code (and
    explicitly NOT to override image value extraction) — the benchmark-layer
    advertisement for the general first-party directive. Imported lazily to keep
    the module's import surface cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import compute_via_code_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not compute_via_code_enabled(source):
        return GAIA_SYSTEM_PROMPT
    return GAIA_SYSTEM_PROMPT + _COMPUTE_VIA_CODE_REMINDER


def extract_final_answer(text: str) -> str:
    matches = list(re.finditer(r"final answer\s*:", text, re.IGNORECASE))
    if not matches:
        return ""
    tail = text[matches[-1].end():]
    lines = tail.splitlines()
    answer = lines[0] if lines else ""
    return answer.strip().rstrip(".").strip()


__all__ = [
    "GAIA_FORMAT_ADHERENCE_NOTE",
    "GAIA_SYSTEM_PROMPT",
    "gaia_system_prompt",
    "extract_final_answer",
]
