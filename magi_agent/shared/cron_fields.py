"""N-33 leaf: single home for the cron field parser.

``_parse_cron_field`` used to be byte-for-byte duplicated between
``magi_agent/missions/cron_policy.py`` and
``magi_agent/harness/cron_runtime.py`` (with a third private cross-module
import in ``missions/schedule_grammar.py``). This stdlib-only leaf holds the
one canonical body; the two historic modules import it and keep a
``_parse_cron_field`` alias for back-compat with their internal call sites.

The parser expands a single cron field (one of the five whitespace-split
positions) into the concrete integer set it selects, honouring ``*`` wildcards,
``a-b`` ranges, ``*/step`` / ``a-b/step`` steps, and ``x,y`` comma lists, with
``[minimum, maximum]`` bound checks. The error message strings are preserved
verbatim.
"""

from __future__ import annotations


def parse_cron_field(field: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            range_part, step_part = part.split("/", 1)
            step = int(step_part)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            range_part = part
        if range_part == "*":
            values.update(range(minimum, maximum + 1, step))
        elif "-" in range_part:
            start_text, end_text = range_part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start < minimum or end > maximum or start > end:
                raise ValueError("cron range out of bounds")
            values.update(range(start, end + 1, step))
        else:
            value = int(range_part)
            if value < minimum or value > maximum:
                raise ValueError("cron value out of range")
            values.add(value)
    if not values:
        raise ValueError("cron field cannot be empty")
    return frozenset(values)
