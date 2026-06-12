"""Inject a pack-provided harness into the live resolved preset state.

A pack ``ResolvedHarnessPack`` participates in the live ``ResolvedHarnessPresetState``
on equal footing with first-party by replacing one of the role slots
(general/coding/research/verification). Uses ``model_copy`` (alias-aware).
"""
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack, ResolvedHarnessPresetState

_SLOTS = frozenset({"general", "coding", "research", "verification"})


def apply_harness_pack(
    state: ResolvedHarnessPresetState,
    *,
    slot: str,
    pack: ResolvedHarnessPack,
) -> ResolvedHarnessPresetState:
    """Inject a pack-provided ResolvedHarnessPack into one of the resolved state's
    role slots, so a pack harness participates in the live resolved preset."""
    if slot not in _SLOTS:
        raise ValueError(f"unknown harness slot: {slot}")
    return state.model_copy(update={slot: pack})
