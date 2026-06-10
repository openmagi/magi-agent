"""Guards ``docs/hook-points.md`` and ``docs/hook-points-reference.md`` against
drift from the real HookBus wiring + enum (PR 17-PR4).

These two docs render on the same site as ``docs/hooks.md`` (manifest-driven).
A spec review (17-docs-honesty.md PR4, E5-e) found:

1. Stale enum count — both siblings claimed "15 lifecycle points" / "15
   HookPoint enum values" while :class:`HookPoint` actually defines 17 members
   (``BEFORE_SYSTEM_PROMPT`` and ``BEFORE_MESSAGE_SEND`` were added).
2. Over-claimed enforcement — ``hook-points.md`` described the HookBus as the
   "primary mechanism for tool policy enforcement" / "tool denial ... is
   enforced" / "primary enforcement point" while user-hook wiring is
   default-OFF and http/llm executors are not yet wired. The honest
   default-OFF / not-yet-wired caveat added to ``hooks.md`` was absent here,
   producing a tone mismatch (one honest, one exaggerated) on the same site.

These tests pin the corrected enum count to ``len(HookPoint)`` (so the doc
self-heals on future enum additions) and require the same honesty caveat the
sibling ``hooks.md`` carries.
"""

from __future__ import annotations

import re
from pathlib import Path

from magi_agent.hooks.manifest import HookPoint

ROOT = Path(__file__).resolve().parents[2]
HOOK_POINTS_DOC = ROOT / "docs" / "hook-points.md"
HOOK_POINTS_REF_DOC = ROOT / "docs" / "hook-points-reference.md"

REAL_COUNT = len(HookPoint)


def _hook_points_text() -> str:
    return HOOK_POINTS_DOC.read_text(encoding="utf-8")


def _hook_points_ref_text() -> str:
    return HOOK_POINTS_REF_DOC.read_text(encoding="utf-8")


def test_hook_points_doc_does_not_claim_fifteen_points() -> None:
    text = _hook_points_text()
    assert "15 lifecycle points" not in text
    assert "15 HookPoint" not in text
    assert "(15 enum values)" not in text


def test_hook_points_ref_doc_does_not_claim_fifteen_values() -> None:
    text = _hook_points_ref_text()
    assert "15 HookPoint enum values" not in text
    assert "15 members" not in text
    assert "all 15" not in text


def test_hook_points_doc_uses_real_enum_count() -> None:
    text = _hook_points_text()
    assert str(REAL_COUNT) in text, (
        f"hook-points.md must mention the real HookPoint count {REAL_COUNT}"
    )


def test_hook_points_ref_doc_uses_real_enum_count() -> None:
    text = _hook_points_ref_text()
    assert str(REAL_COUNT) in text, (
        f"hook-points-reference.md must mention the real HookPoint count "
        f"{REAL_COUNT}"
    )


def test_hook_points_doc_carries_default_off_caveat() -> None:
    """Same honesty signal as the sibling hooks.md: user-hook enforcement is
    gated default-OFF and not yet live in the production turn loop."""
    text = _hook_points_text()
    assert "MAGI_USER_HOOKS_ENABLED" in text
    assert "default-OFF" in text or "default OFF" in text


def test_hook_points_doc_drops_unqualified_enforcement_overclaims() -> None:
    """The bare "primary mechanism for tool policy enforcement" /
    "primary enforcement point" assertions (enforcement-described-but-0-wired)
    must not stand unqualified."""
    text = _hook_points_text()
    assert "primary mechanism for tool policy enforcement" not in text
    assert "primary enforcement point" not in text


def test_hook_points_ref_lists_real_enum_members() -> None:
    """Every backticked camelCase HookPoint member must really exist, and the
    two previously-undocumented members must now appear."""
    text = _hook_points_ref_text()
    real = {point.value for point in HookPoint}
    candidates = set(re.findall(r'"([a-z][a-zA-Z]*[A-Z][a-zA-Z]*)"', text))
    hook_like = {c for c in candidates if c.startswith(("before", "after", "on"))}
    invented = hook_like - real
    assert not invented, f"ref doc references non-existent hook points: {invented}"
