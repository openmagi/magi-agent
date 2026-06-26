"""Single source of truth for the observability event taxonomy.

This module defines:
  - NOISE_KINDS: event kinds that are hidden by default in the UI (high-volume,
    low-signal stream events emitted at sub-turn granularity).
  - CATEGORIES: mapping of semantic category name -> list of event kinds within
    that category.  Categories are mutually exclusive groupings of non-noise
    kinds; a kind may appear in more than one category only when it genuinely
    belongs to multiple semantic groups (e.g. 'aborted' is both a lifecycle
    terminus and an error).
  - get_meta_taxonomy(): returns a JSON-serializable dict with shape
        {"categories": {<name>: [<kind>, ...]}, "noise_kinds": [<kind>, ...]}
    This is the contract consumed by the /meta endpoint and, transitively, by
    the frontend (Task 9).  Do NOT duplicate this mapping elsewhere on the
    server.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Noise: default-hidden, high-frequency sub-turn event kinds
# ---------------------------------------------------------------------------

NOISE_KINDS: list[str] = [
    "text_delta",
    "heartbeat",
    "turn_phase",
    "runtime_trace",
    "tool_progress",
]

# ---------------------------------------------------------------------------
# Categories: semantic groupings of non-noise event kinds
#
# Order within each list is display-stable (alphabetical within group).
# 'aborted' appears in both 'lifecycle' (it is a terminal state) and 'errors'
# (it represents an abnormal end); this is intentional and mirrors real usage.
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, list[str]] = {
    "lifecycle": [
        "aborted",
        "checkpoint",
        "compaction_end",
        "compaction_start",
        "turn_end",
        "turn_start",
    ],
    "tools": [
        "source_inspected",
        "tool_end",
        "tool_start",
    ],
    "policy": [
        "rule_check",
        "rule_violation",
    ],
    "errors": [
        "aborted",
        "error",
    ],
    "other": [
        "artifact_created",
        "child_progress",
        "task_board",
    ],
}


# ---------------------------------------------------------------------------
# Public payload factory
# ---------------------------------------------------------------------------

def get_meta_taxonomy() -> dict:
    """Return the JSON-serializable taxonomy payload for the /meta endpoint.

    Shape::

        {
            "categories": {
                "lifecycle": ["aborted", "checkpoint", ...],
                "tools":     ["source_inspected", "tool_end", "tool_start"],
                "policy":    ["rule_check", "rule_violation"],
                "errors":    ["aborted", "error"],
                "other":     ["artifact_created", "child_progress", "task_board"],
            },
            "noise_kinds": ["text_delta", "heartbeat", "turn_phase",
                            "runtime_trace", "tool_progress"],
        }

    The frontend uses ``noise_kinds`` to determine which kinds to hide by
    default and ``categories`` to group visible events in the timeline UI.
    """
    # Return shallow copies so callers cannot mutate the module-level constants.
    return {
        "categories": {cat: list(kinds) for cat, kinds in CATEGORIES.items()},
        "noise_kinds": list(NOISE_KINDS),
    }
