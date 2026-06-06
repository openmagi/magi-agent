"""GAIA system prompt and FINAL ANSWER extraction."""
from __future__ import annotations

import re

GAIA_SYSTEM_PROMPT = (
    "You are a general AI assistant solving a GAIA benchmark question. "
    "Use the available tools (web search/fetch, file reading, shell/python) to "
    "research and compute the answer. Report your reasoning, then finish with "
    "exactly one line:\n"
    "FINAL ANSWER: <answer>\n"
    "YOUR FINAL ANSWER should be a number OR as few words as possible OR a "
    "comma separated list of numbers and/or strings. If asked for a number, do "
    "not use commas or units (e.g. $ or %) unless specified. If asked for a "
    "string, do not use articles or abbreviations, and write digits in plain "
    "text unless specified. Apply these rules to each element of a list."
)


def extract_final_answer(text: str) -> str:
    matches = list(re.finditer(r"final answer\s*:", text, re.IGNORECASE))
    if not matches:
        return ""
    tail = text[matches[-1].end():]
    lines = tail.splitlines()
    answer = lines[0] if lines else ""
    return answer.strip().rstrip(".").strip()


__all__ = ["GAIA_SYSTEM_PROMPT", "extract_final_answer"]
