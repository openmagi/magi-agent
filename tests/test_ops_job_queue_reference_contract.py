"""H-23 — assert ``magi_agent.ops.job_queue`` is an intentionally dormant
reference contract, not accidentally dead code.

The ``ops/job_queue.py`` module ships a fail-closed, fully-tested
specification of the agent job-queue lifecycle FSM (queued → running →
completed / failed / dead_lettered / cancelled / timed_out) with an
idempotency index, a lease timeout, and a redaction-aware authority
projection. It has zero non-test callers — the genuine durable agent
work-queue lives at ``magi_agent.missions.work_queue``.

H-23 makes the dormancy unmistakable: the module declares
``REFERENCE_CONTRACT = True`` and its top-level docstring states that
wiring requires an explicit decision per AGENTS.md.

This module locks both signals so a future reader (or a dead-code
sweep) cannot mistake the file for live OSS job-queueing:

1. The constant is set and exported.
2. The top-level docstring carries the phrase that names the dormancy.
3. No non-test caller in ``magi_agent/`` invokes the four FSM entry
   points (``AgentJobQueue.__init__``, ``enqueue_job``, the four
   ``JobLease`` / ``EnqueueResult`` constructors). A future wiring PR
   that adds a real caller must also flip ``REFERENCE_CONTRACT`` to
   ``False`` in the same commit — surfacing the contract-status
   change to review.
"""

from __future__ import annotations

import re
from pathlib import Path

import magi_agent
import magi_agent.ops.job_queue


def test_job_queue_reference_contract_flag_is_true() -> None:
    assert magi_agent.ops.job_queue.REFERENCE_CONTRACT is True
    assert "REFERENCE_CONTRACT" in magi_agent.ops.job_queue.__all__


def test_job_queue_docstring_declares_dormancy() -> None:
    doc = magi_agent.ops.job_queue.__doc__ or ""
    assert "reference contract" in doc.lower()
    assert "not wired" in doc.lower() or "not invoked" in doc.lower()
    assert "missions.work_queue" in doc.lower() or "missions/work_queue" in doc.lower(), (
        "the dormancy docstring must point a future reader at the live "
        "durable queue (``missions.work_queue``) so the wiring direction "
        "is unmistakable"
    )


_PACKAGE_ROOT = Path(magi_agent.__file__).parent
#: ``ops/job_queue.py`` itself, and its sibling test files, are allowed to
#: reference these symbols — everything else under ``magi_agent/`` must not.
_ALLOWED_REL_PATHS = frozenset({Path("ops/job_queue.py")})

_ENTRY_POINTS = (
    "AgentJobQueue",
    "enqueue_job",
)
# Match an attribute-style usage (e.g. ``AgentJobQueue(`` or
# ``enqueue_job(``). A bare-identifier inside an unrelated string is NOT a
# call — guard with the ``(`` lookahead.
_CALL_RE = {
    name: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*\(")
    for name in _ENTRY_POINTS
}


def test_no_live_caller_under_magi_agent() -> None:
    """The four FSM entry points must remain unwired in OSS. If a
    future PR genuinely wires one, this test fails on purpose so the
    same PR must explicitly flip ``REFERENCE_CONTRACT`` and update this
    guard with the wiring rationale."""

    offenders: dict[str, list[str]] = {name: [] for name in _ENTRY_POINTS}
    for path in _PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(_PACKAGE_ROOT)
        if rel in _ALLOWED_REL_PATHS:
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
        "A live OSS caller has appeared for an AgentJobQueue FSM entry "
        "point. The same PR must flip "
        "``magi_agent.ops.job_queue.REFERENCE_CONTRACT`` to False and "
        "update this guard with the wiring rationale (or wire through "
        "``magi_agent.missions.work_queue`` instead — the genuine "
        f"durable queue). Offenders: {leaked}"
    )
