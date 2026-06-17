"""WHAT-menu for deterministic custom rules.

A custom ``deterministic_ref`` rule may only require a ref that has a LIVE
producer — i.e. a ref ``evidence/local_tool_collector._inferred_refs`` actually
emits on a turn. Requiring a ref with no producer would block every applicable
turn unconditionally, so the builder offers only this curated, producer-backed
set.

This is an EXPLICIT descriptor mapping (spec §12), not a raw set-intersection of
``BUILTIN_EVIDENCE_TYPES`` (type names) and ``_inferred_refs`` (ref strings) —
those two vocabularies don't join cleanly. Keep these refs in sync with
``_inferred_refs``; ``test_customize_what_menu`` guards the producer-backed
invariant.
"""

from __future__ import annotations

from typing import Any

# Each entry: the public ref + a human label, its source evidence type, the
# enforcement tier, the (fixed) fire-at point, and the actions a det rule may use.
_WHAT_MENU: tuple[dict[str, Any], ...] = (
    {
        "ref": "verifier:dev-coding:test-evidence",
        "label": "Tests pass after a code change",
        "evidenceType": "TestRun",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
    {
        "ref": "evidence:test-run",
        "label": "Tests were actually run",
        "evidenceType": "TestRun",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
    {
        "ref": "evidence:git-diff",
        "label": "A code change was recorded (git diff)",
        "evidenceType": "GitDiff",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
)


def what_menu() -> list[dict[str, Any]]:
    """Return the WHAT-menu descriptors (lists, JSON-serializable)."""
    return [{**e, "allowedActions": list(e["allowedActions"])} for e in _WHAT_MENU]


def known_refs() -> frozenset[str]:
    return frozenset(e["ref"] for e in _WHAT_MENU)


def is_known_ref(ref: str) -> bool:
    return ref in known_refs()


def allowed_actions_for(ref: str) -> tuple[str, ...]:
    for e in _WHAT_MENU:
        if e["ref"] == ref:
            return tuple(e["allowedActions"])
    return ()
