#!/usr/bin/env python3
"""xfail budget ratchet gate.

~171 pre-existing test failures were quarantined when CI was introduced
(PR #406): the root ``conftest.py`` applies ``xfail(strict=False)`` to every
nodeid listed in ``tests/ci_quarantine.txt``. This gate makes that debt a
ratchet — the count can only go DOWN:

* The "xfail count" = inline ``pytest.mark.xfail`` marker occurrences in
  ``*.py`` under ``tests/`` and ``magi_agent/**/tests/`` PLUS nodeid entries in
  ``tests/ci_quarantine.txt`` (both quarantine routes are budgeted, so the
  manifest cannot be dodged with an inline marker or vice versa).
* count > baseline -> exit 1: fix an existing xfail instead of adding one.
* count < baseline -> exit 1 too: lock the win in by ratcheting the baseline
  down (run ``--update`` and commit ``scripts/xfail_budget.txt``).
* count == baseline -> exit 0.

Usage (locally and in CI):
    uv run --no-sync python scripts/check_xfail_budget.py            # gate
    uv run --no-sync python scripts/check_xfail_budget.py --update   # ratchet

Mirrors the ratchet conventions of scripts/check_naming.sh. Pure stdlib,
pure file reads — no network, no model.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

INLINE_MARKER = "pytest.mark.xfail"
BASELINE_RELPATH = Path("scripts") / "xfail_budget.txt"
QUARANTINE_RELPATH = Path("tests") / "ci_quarantine.txt"
_MANIFEST_COMMENT = "#"


def _iter_test_files(root: Path) -> Iterator[Path]:
    """Yield ``*.py`` files under ``tests/`` and ``magi_agent/**/tests/``."""
    tests_root = root / "tests"
    if tests_root.is_dir():
        yield from sorted(tests_root.rglob("*.py"))
    pkg_root = root / "magi_agent"
    if pkg_root.is_dir():
        for tests_dir in sorted(pkg_root.rglob("tests")):
            if tests_dir.is_dir():
                yield from sorted(tests_dir.rglob("*.py"))


def count_inline_xfails(root: Path) -> int:
    """Count inline ``pytest.mark.xfail`` occurrences in the test trees."""
    total = 0
    for path in _iter_test_files(root):
        total += path.read_text(encoding="utf-8", errors="ignore").count(INLINE_MARKER)
    return total


def count_quarantine_entries(manifest: Path) -> int:
    """Count quarantined nodeids in the CI quarantine manifest.

    Blank lines and ``#`` comment/``#reason:`` header lines do not count.
    A missing manifest counts as zero (fully un-quarantined).
    """
    if not manifest.is_file():
        return 0
    total = 0
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(_MANIFEST_COMMENT):
            continue
        total += 1
    return total


def count_xfails(root: Path) -> int:
    """Total budgeted xfails: inline markers + quarantine-manifest entries."""
    return count_inline_xfails(root) + count_quarantine_entries(
        root / QUARANTINE_RELPATH
    )


def read_baseline(path: Path) -> int:
    """Parse the committed baseline: a single non-negative integer."""
    content = path.read_text(encoding="utf-8").strip()
    if not content.isdigit():
        raise ValueError(
            f"baseline file {path} must contain a single non-negative integer, "
            f"got {content!r}"
        )
    return int(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repo root to scan (default: this checkout)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="write the current count into the baseline (ratchet down)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    baseline_path = root / BASELINE_RELPATH
    count = count_xfails(root)

    if args.update:
        baseline_path.write_text(f"{count}\n", encoding="utf-8")
        print(f"xfail budget baseline updated: {baseline_path} = {count}")
        return 0

    if not baseline_path.is_file():
        print(
            f"xfail budget gate FAILED: missing baseline {baseline_path}\n"
            "Generate it with: uv run --no-sync python "
            "scripts/check_xfail_budget.py --update",
            file=sys.stderr,
        )
        return 1

    try:
        baseline = read_baseline(baseline_path)
    except ValueError as exc:
        print(f"xfail budget gate FAILED: {exc}", file=sys.stderr)
        return 1

    if count > baseline:
        print(
            f"xfail budget gate FAILED: {count} xfail(s) > baseline {baseline}.\n"
            f"New xfails are not accepted — fix an existing xfail (inline "
            f"pytest.mark.xfail or tests/ci_quarantine.txt entry) instead of "
            f"adding one. The quarantine debt only ratchets down.",
            file=sys.stderr,
        )
        return 1

    if count < baseline:
        print(
            f"xfail budget gate FAILED: {count} xfail(s) < baseline {baseline}.\n"
            f"Nice — you fixed quarantined tests. ratchet down: update the "
            f"baseline to {count} so the win is locked in:\n\n"
            f"    uv run --no-sync python scripts/check_xfail_budget.py --update\n\n"
            f"and commit scripts/xfail_budget.txt in the same PR.",
            file=sys.stderr,
        )
        return 1

    print(f"xfail budget OK: {count} xfail(s) (== baseline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
