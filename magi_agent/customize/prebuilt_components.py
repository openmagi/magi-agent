"""PR-P4: prebuilt (always-on) runtime components catalog.

Some prebuilt behaviors are enforced by the kernel gate5b workspace toolhost
ENVELOPE, not by an opt-out harness preset or a pack `provides` entry, so they
never appeared anywhere in the dashboard even though they gate every workspace
mutation (e.g. read-before-write). This module is a small, curated, read-only
descriptor list so the operator can SEE them. They are always-on and not
togglable from the dashboard (kernel safety/workflow envelope); the catalog is
descriptive only and never consulted on the runtime hot path.

Grounding: each entry corresponds to an always-on behavior in
``magi_agent/gates/gate5b_full_toolhost.py`` (read-before-mutation enforcement,
path safety, per-call receipts, bounded output, memory-mode policy).
"""

from __future__ import annotations

from typing import Any

# key / name / description / where (the enforcing subsystem). All always-on.
_PREBUILT: tuple[dict[str, str], ...] = (
    {
        "key": "read_before_write",
        "name": "Read before write",
        "description": (
            "A workspace file must be read this turn before it can be edited, "
            "written, or patched. Blocks blind mutations."
        ),
        "where": "gate5b workspace toolhost",
    },
    {
        "key": "path_safety",
        "name": "Path safety",
        "description": (
            "Workspace tools may only read and mutate paths inside the bot "
            "workspace; path traversal outside it is denied."
        ),
        "where": "gate5b workspace toolhost",
    },
    {
        "key": "tool_call_receipts",
        "name": "Tool-call receipts",
        "description": (
            "Every workspace tool call produces a verifiable receipt (bytes, "
            "digest, coding/edit-match/diagnostics records) the evidence gates "
            "can check."
        ),
        "where": "gate5b workspace toolhost",
    },
    {
        "key": "bounded_tool_output",
        "name": "Bounded tool output",
        "description": (
            "Large tool outputs are capped and recorded so a single call cannot "
            "blow the model's context window."
        ),
        "where": "gate5b workspace toolhost",
    },
    {
        "key": "memory_mode_policy",
        "name": "Memory-mode policy",
        "description": (
            "Workspace writes honor the active memory-mode policy (which "
            "directories are writable for the turn)."
        ),
        "where": "gate5b workspace toolhost",
    },
)


def prebuilt_components_view() -> list[dict[str, Any]]:
    """Return the curated always-on prebuilt components (read-only, descriptive).

    Each entry: ``{key, name, description, where, alwaysOn: True}``.
    """
    return [{**entry, "alwaysOn": True} for entry in _PREBUILT]
