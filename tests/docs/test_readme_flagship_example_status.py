"""Guards the flagship README example status label (PR 17-PR5, E6/A2).

The "Verify Source Before Claim" example (root ``README.md`` and the
site-rendered ``docs/README.md`` overview page) illustrates the
evidence-governance *model*. On a fresh install it is not reproducible as a
hard block: the research final projection gate is audit-only
(``magi_agent/research/final_projection_gate.py`` —
``final_answer_blocking_enabled: Literal[False]``), so claims are recorded but
the final answer is not blocked.

The gate that *does* block today is the coding-domain pre-final completion gate
(``magi_agent/cli/engine.py`` — ``pre_final_evidence_gate_blocked``), which is
default-ON for coding turns. This guard asserts both README surfaces carry an
honest status label that:

- marks the research example as a governance demo / audit-only (not a fresh
  install block), and
- points readers at the coding pre-final gate as the gate that does block.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOT_README = ROOT / "README.md"
DOCS_README = ROOT / "docs" / "README.md"

_SECTION_HEADINGS = (
    "## Example: One Task, Up the Stack",
    "## Example: Verify Source Before Claim",
    "## Verify Source Before Claim",
)


def _flagship_section(text: str) -> str:
    """Return the flagship example section body (heading -> next ``## ``)."""
    heading = next((h for h in _SECTION_HEADINGS if h in text), None)
    assert heading is not None, "flagship example heading missing"
    start = text.index(heading)
    rest = text[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_root_readme_has_status_label() -> None:
    section = _flagship_section(ROOT_README.read_text(encoding="utf-8"))
    lowered = section.lower()
    assert "status:" in lowered
    assert "audit-only" in lowered
    assert "default-off" in lowered or "default off" in lowered


def test_root_readme_clarifies_research_gate_does_not_block() -> None:
    section = _flagship_section(ROOT_README.read_text(encoding="utf-8")).lower()
    # Honest about the research final gate being observe/audit-only.
    assert "does not block the final answer" in section
    # Points readers at the gate that DOES block (coding pre-final gate).
    assert "pre-final" in section


def test_docs_readme_overview_has_matching_status_label() -> None:
    section = _flagship_section(DOCS_README.read_text(encoding="utf-8"))
    lowered = section.lower()
    assert "status:" in lowered
    assert "audit-only" in lowered
    assert "default-off" in lowered or "default off" in lowered
    assert "does not block the final answer" in lowered
    assert "pre-final" in lowered
