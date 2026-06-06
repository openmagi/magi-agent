# magi_agent/recipes/first_party/legal/output_parser.py
from __future__ import annotations

import re


def parse_answer(raw: str, *, labels: tuple[str, ...]) -> str | None:
    text = raw.strip()
    # Scan from the end: the model's final standalone label token is the answer.
    best: tuple[int, str] | None = None
    for label in labels:
        for match in re.finditer(
            rf"(?<![\w]){re.escape(label)}(?![\w])", text, flags=re.IGNORECASE
        ):
            pos = match.start()
            if best is None or pos > best[0]:
                best = (pos, label)
    return best[1] if best else None
