"""Guards `docs/security.md` against the stale memory-write claim (PR 17-PR3).

E5-d — `docs/security.md` previously stated:

    "Memory writes are blocked. Read-only memory adapters are the only
     supported mode."

That line contradicts the runtime: the `MemoryWrite` tool exists in the core
tool catalog (`magi_agent/tools/catalog.py`) and is gated by the
`MAGI_MEMORY_WRITE_ENABLED` environment variable (default-OFF). Read-only is
the *default* mode, but writes are an opt-in gated capability — not a
categorical block.

Scope note: the `block_final_answer` evidence-enforcement line (security.md:46)
is already accurate and is owned by a different PR; this guard only covers the
memory-write claim.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "docs" / "security.md"


def test_security_drops_categorical_memory_write_block() -> None:
    text = SECURITY.read_text(encoding="utf-8")
    # The stale categorical claims must be gone.
    assert "Memory writes are blocked." not in text
    assert "Read-only memory adapters are the only supported mode." not in text


def test_security_documents_gated_memory_write_tool() -> None:
    text = SECURITY.read_text(encoding="utf-8")
    # The corrected line must name the gated tool and its flag.
    assert "MemoryWrite" in text
    assert "MAGI_MEMORY_WRITE_ENABLED" in text


def test_security_marks_memory_write_default_off() -> None:
    text = SECURITY.read_text(encoding="utf-8").lower()
    # The gate must be described as off-by-default with read-only as the default.
    assert "default-off" in text or "default off" in text
    assert "read-only" in text
