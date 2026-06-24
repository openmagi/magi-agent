"""H-6 — assert ``magi_agent.billing`` is intentionally a reference contract.

The ``billing/`` package ships a fail-closed, redaction-correct, fully-tested
spend-cap/quota FSM that no OSS-runtime caller invokes. H-6 makes that
dormancy unmistakable: the package declares ``REFERENCE_CONTRACT = True`` and
its top-level docstring states that wiring requires an explicit decision per
AGENTS.md.

This module asserts both signals so a future reader (or a sweep tool) cannot
mistake the package for accidental dead code:

1. The constant is set and exported.
2. The top-level docstring carries the phrase that names the dormancy.
3. No non-test caller in ``magi_agent/`` invokes the four entry points
   (``reserve_spend`` / ``commit_spend_reservation`` /
   ``release_spend_reservation`` / ``evaluate_quota``). If a future wiring PR
   adds a real caller it must also flip ``REFERENCE_CONTRACT`` to ``False``
   in the same commit, surfacing the contract-status change to review.
"""

from __future__ import annotations

import re
from pathlib import Path

import magi_agent
import magi_agent.billing


def test_billing_reference_contract_flag_is_true() -> None:
    assert magi_agent.billing.REFERENCE_CONTRACT is True
    assert "REFERENCE_CONTRACT" in magi_agent.billing.__all__


def test_billing_docstring_declares_dormancy() -> None:
    doc = magi_agent.billing.__doc__ or ""
    assert "reference contract" in doc.lower()
    assert "not wired" in doc.lower() or "not invoked" in doc.lower()
    assert "SpendCapProbe" in doc


_PACKAGE_ROOT = Path(magi_agent.__file__).parent
_ENTRY_POINTS = (
    "reserve_spend",
    "commit_spend_reservation",
    "release_spend_reservation",
    "evaluate_quota",
)
# Match either a direct call ``reserve_spend(`` or an attribute access
# ``billing.reserve_spend(`` — anything that would constitute a live caller.
_CALL_RE = {
    name: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*\(") for name in _ENTRY_POINTS
}


def test_no_live_caller_under_magi_agent() -> None:
    """The four spend-FSM entry points must remain unwired in OSS. If a
    future PR genuinely wires one, this test fails on purpose so the same
    PR must explicitly flip ``REFERENCE_CONTRACT`` and update this guard."""

    offenders: dict[str, list[str]] = {name: [] for name in _ENTRY_POINTS}
    for path in _PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(_PACKAGE_ROOT)
        # The package itself defines the entry points; tests document the
        # contract. Both are out of scope for "live caller".
        if rel.parts and rel.parts[0] == "billing":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in _CALL_RE.items():
            if pattern.search(text):
                offenders[name].append(str(rel))
    leaked = {name: locs for name, locs in offenders.items() if locs}
    assert not leaked, (
        "A live OSS caller has appeared for a spend-FSM entry point. The "
        "same PR must flip ``magi_agent.billing.REFERENCE_CONTRACT`` to "
        "False and update this guard with the wiring rationale. "
        f"Offenders: {leaked}"
    )
