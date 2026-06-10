"""Idempotence guard for magi_agent/ARCHITECTURE.md (PR 17-PR7, E7).

``magi_agent/ARCHITECTURE.md`` is a generated artifact produced by
``scripts/generate_module_map.py`` from the live ``magi_agent`` source tree.

E7's honesty gap was that the committed map had fallen far behind the tree:
it covered fewer than half of the sub-packages, still listed modules that had
been deleted by the cluster-10/13/16 diet PRs, and omitted the active control
modules that replaced them.

The fix for E7 is a single-owner *regeneration* of the file — never a hand
edit. To keep that honest going forward, this test asserts that re-running the
generator reproduces the committed file byte-for-byte. If the source tree gains,
loses, or renames a module without a regeneration, this fails with a pointer to
the fix.

(``scripts/check_module_map.sh`` is the CI/bash equivalent; this pytest is the
in-suite assertion the 17-PR7 spec calls for.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate_module_map.py"
ARCHITECTURE = ROOT / "magi_agent" / "ARCHITECTURE.md"


def _regenerate() -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"Generator failed: {result.stderr}"
    return result.stdout


def test_architecture_file_exists() -> None:
    assert ARCHITECTURE.is_file(), "magi_agent/ARCHITECTURE.md is missing"


def test_committed_module_map_is_freshly_generated() -> None:
    """The committed ARCHITECTURE.md must equal the generator output."""
    generated = _regenerate()
    committed = ARCHITECTURE.read_text(encoding="utf-8")
    assert committed == generated, (
        "magi_agent/ARCHITECTURE.md is stale relative to the source tree. "
        "Regenerate and commit:\n"
        "    uv run python scripts/generate_module_map.py "
        "> magi_agent/ARCHITECTURE.md"
    )


def test_module_map_covers_every_subpackage() -> None:
    """Every magi_agent sub-package (dir with __init__.py) gets a heading."""
    package_dir = ROOT / "magi_agent"
    expected: set[str] = set()
    for path in package_dir.rglob("__init__.py"):
        rel = path.parent.relative_to(package_dir)
        expected.add("(root)" if rel == Path(".") else str(rel))

    committed = ARCHITECTURE.read_text(encoding="utf-8")
    headings = {
        line[len("### ") :].rstrip("/")
        for line in committed.splitlines()
        if line.startswith("### ")
    }
    missing = expected - headings
    assert not missing, f"ARCHITECTURE.md missing package headings: {sorted(missing)}"
