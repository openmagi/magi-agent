# magi_agent/recipes/first_party/legal/output_parser.py
from __future__ import annotations

import re


def parse_answer(raw: str, *, labels: tuple[str, ...]) -> str | None:
    text = raw.strip()
    # Take the FIRST standalone label. With a label-first answer format (induced
    # by few-shot exemplars or an answer-only instruction) the model states its
    # conclusion first, then elaborates. Empirically more faithful than scanning
    # from the end, which grabbed labels out of trailing reasoning and biased
    # scoring against verbose (e.g. zero-shot) outputs. Limitation: if a prompt
    # is unconstrained and the model echoes the option list ("Yes or No? No"),
    # first-match returns the echoed option — induce label-only output to avoid.
    best: tuple[int, str] | None = None
    for label in labels:
        for match in re.finditer(
            rf"(?<!\w){re.escape(label)}(?!\w)", text, flags=re.IGNORECASE
        ):
            pos = match.start()
            if best is None or pos < best[0] or (pos == best[0] and len(label) > len(best[1])):
                best = (pos, label)
    return best[1] if best else None
