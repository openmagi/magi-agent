"""Guards `docs/toolhost.md` against re-introducing conceptual ToolHost types
and ensures it cross-links to the real tool-execution path (PR 13-PR6 / A8).

A8 background: the conceptual ``ToolHostRequest`` / ``ToolHostReceipt`` types do
not exist in the codebase (verified via ``git grep`` on origin/main — 0 hits in
``magi_agent/``). ``docs/toolhost.md`` already states this honestly. The real
contract is the ADK tool-execution path with hook-based policy enforcement
(beforeToolUse / afterToolUse) and ``ToolEvidenceRecord`` as the output. Each of
the 11 concrete ``*ToolHost`` classes carries its own request/receipt shape that
converges on ``ToolEvidenceRecord`` rather than a unified Request/Receipt type.

Kevin DECISION: do NOT add ``ToolHostRequest`` / ``ToolHostReceipt`` types. This
is a small cross-link / clarity PR — toolhost.md must (a) keep saying the
conceptual types do not exist, and (b) point the reader at where real tool
execution actually lives so the "(conceptual)" section is not a dead end.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLHOST = ROOT / "docs" / "toolhost.md"


def test_toolhost_keeps_conceptual_types_as_non_existent() -> None:
    text = TOOLHOST.read_text(encoding="utf-8")
    # The honest disclaimer must remain: the conceptual types do not exist.
    assert "ToolHostRequest and ToolHostReceipt types do not exist" in text
    # And the doc must not claim they are real/implemented types.
    assert "ToolHostRequest is a Pydantic model" not in text
    assert "ToolHostReceipt is a Pydantic model" not in text


def test_toolhost_names_real_execution_path() -> None:
    text = TOOLHOST.read_text(encoding="utf-8")
    # The real contract must be named so the conceptual section is not a dead end.
    assert "ToolEvidenceRecord" in text
    assert "ADK tool execution" in text
    # Hook-based policy enforcement is the request-context equivalent.
    assert "beforeToolUse" in text and "afterToolUse" in text


def test_toolhost_cross_links_to_real_execution_path() -> None:
    text = TOOLHOST.read_text(encoding="utf-8")
    # The conceptual section must cross-link to the actual execution-path docs so
    # a reader knows where real tool execution lives. Slugs verified against the
    # link targets already used across docs/ (e.g. /docs/toolhost-api).
    assert "/docs/toolhost-api" in text
    assert "/docs/evidence" in text
    assert "/docs/hook-points" in text
