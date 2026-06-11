from __future__ import annotations

import re
from pathlib import Path

from magi_agent.evidence.types import BUILTIN_EVIDENCE_TYPES

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DOC_PATH = REPO_ROOT / "docs" / "evidence.md"

_COUNT_RE = re.compile(
    r"defines\s+(\d+)\s+builtin evidence types", re.IGNORECASE
)


def _doc_text() -> str:
    return EVIDENCE_DOC_PATH.read_text(encoding="utf-8")


def test_evidence_doc_declares_correct_type_count() -> None:
    """docs/evidence.md must declare the same number of builtin evidence
    types as the code (BUILTIN_EVIDENCE_TYPES) to prevent doc drift."""
    text = _doc_text()
    match = _COUNT_RE.search(text)
    assert match is not None, (
        "docs/evidence.md must state '... defines N builtin evidence types'"
    )
    declared = int(match.group(1))
    assert declared == len(BUILTIN_EVIDENCE_TYPES), (
        f"docs/evidence.md declares {declared} builtin evidence types "
        f"but code defines {len(BUILTIN_EVIDENCE_TYPES)} "
        f"({list(BUILTIN_EVIDENCE_TYPES)})"
    )


def test_evidence_doc_lists_every_builtin_type() -> None:
    """Every builtin evidence type name must be documented in
    docs/evidence.md so the list stays in sync with the code."""
    text = _doc_text()
    missing = [name for name in BUILTIN_EVIDENCE_TYPES if name not in text]
    assert not missing, (
        f"docs/evidence.md is missing builtin evidence type entries: {missing}"
    )
