"""H5 honesty pass: pin the first-party scaffold-pack classification.

``recipes/first_party/__init__.py`` documents the canonical classification of
every pack as either a split-architecture opt-in (4 packs) or the intentional
PR1 fixture-only dormant (``coding/ownership``). These assertions fence the
docstring so a future activation of any pack must FIRST update the docstring
intentionally — preventing a fake-toggle flip from sneaking in unchecked.
"""
from __future__ import annotations

import magi_agent.recipes.first_party as first_party
import magi_agent.recipes.first_party.coding.ownership as ownership


def test_first_party_init_docstring_pins_classification_table() -> None:
    doc = first_party.__doc__ or ""
    # Each of the 4 split-architecture packs is listed by name AND with its
    # activation env-flag family, so a copy/paste activation can't hide here.
    for pack, gate in (
        ("discovery", "MAGI_DISCOVERY_ENABLED"),
        ("self_improvement", "MAGI_LEARNING_"),
        ("memory_recall", "MAGI_MEMORY_RECALL_ENABLED"),
        ("learning_usage", "MAGI_LEARNING_ENABLED"),
    ):
        assert pack in doc, f"first_party docstring is missing {pack}"
        assert gate in doc, f"first_party docstring is missing gate {gate} for {pack}"
    # ownership is honestly labelled as intentional dormant in the same table.
    assert "ownership" in doc
    assert "intentional dormant" in doc


def test_ownership_module_docstring_marks_intentional_dormant() -> None:
    doc = ownership.__doc__ or ""
    # The docstring must explicitly classify this pack as intentional dormant
    # AND reference the canonical fixture-only activation gate — so any future
    # activation has to update both signals.
    assert "intentional dormant" in doc.lower()
    assert "PR1-coding-ownership-fixture-only" in doc
    assert "DO NOT activate" in doc
