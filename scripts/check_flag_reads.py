#!/usr/bin/env python3
"""Direct flag-read ratchet gate (flag-governance PR3 prevention seam).

``magi_agent/config/flags.py`` is the canonical flag registry + typed reader
(flag_bool / flag_profile_bool / flag_str / flag_int), but dozens of call
sites still read ``MAGI_*``/``CORE_AGENT_*`` straight off ``os.environ`` with
ad-hoc truthy parsing. This gate budgets those direct reads against a
committed baseline so the debt can only ratchet DOWN:

* The "direct read count" = occurrences of ``os.environ.get(``,
  ``os.getenv(`` or ``os.environ[`` immediately followed by a quoted
  ``MAGI_``/``CORE_AGENT_`` name, in ``magi_agent/**/*.py`` excluding the
  config allowlist (``config/env.py``, ``config/flags.py``) and test
  directories. Injected ``Mapping`` parameters named ``environ`` and writes
  (``setdefault``/``pop``) are out of scope by design.
* count > baseline -> exit 1: route the new read through
  ``magi_agent.config.flags`` instead of ``os.environ``.
* count < baseline -> exit 1 too: lock the migration in by ratcheting the
  baseline down (run ``--update`` and commit ``scripts/flag_reads_budget.txt``).
* count == baseline -> exit 0.

Usage (locally and in CI):
    uv run --no-sync python scripts/check_flag_reads.py            # gate
    uv run --no-sync python scripts/check_flag_reads.py --update   # ratchet

Mirrors scripts/check_xfail_budget.py. Pure stdlib, pure file reads — no
network, no model.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BASELINE_RELPATH = Path("scripts") / "flag_reads_budget.txt"
SCAN_ROOT = Path("magi_agent")
ALLOWLIST = frozenset(
    {
        Path("magi_agent/config/env.py"),
        Path("magi_agent/config/flags.py"),
    }
)

# os.environ.get("MAGI_... / os.getenv('CORE_AGENT_... / os.environ["MAGI_...
# \s* tolerates the split-call form (argument on the next line).
_READ_RE = re.compile(
    r"\bos\s*\.\s*(?:environ\s*\.\s*get|getenv)\s*\(\s*[\"'](?:MAGI_|CORE_AGENT_)"
    r"|\bos\s*\.\s*environ\s*\[\s*[\"'](?:MAGI_|CORE_AGENT_)"
)


def _in_scope(relpath: Path) -> bool:
    if relpath in ALLOWLIST:
        return False
    return "tests" not in relpath.parts


def count_reads_by_file(root: Path) -> dict[Path, int]:
    """Map repo-relative path -> direct flag-read count (in-scope files only)."""
    counts: dict[Path, int] = {}
    scan_root = root / SCAN_ROOT
    if not scan_root.is_dir():
        return counts
    for path in sorted(scan_root.rglob("*.py")):
        relpath = path.relative_to(root)
        if not _in_scope(relpath):
            continue
        found = len(
            _READ_RE.findall(path.read_text(encoding="utf-8", errors="ignore"))
        )
        if found:
            counts[relpath] = found
    return counts


def count_direct_flag_reads(root: Path) -> int:
    """Total direct MAGI_/CORE_AGENT_ env reads outside the config allowlist."""
    return sum(count_reads_by_file(root).values())


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
    by_file = count_reads_by_file(root)
    count = sum(by_file.values())

    if args.update:
        baseline_path.write_text(f"{count}\n", encoding="utf-8")
        print(f"flag-read baseline updated: {baseline_path} = {count}")
        return 0

    if not baseline_path.is_file():
        print(
            f"flag-read gate FAILED: missing baseline {baseline_path}\n"
            "Generate it with: uv run --no-sync python "
            "scripts/check_flag_reads.py --update",
            file=sys.stderr,
        )
        return 1

    try:
        baseline = read_baseline(baseline_path)
    except ValueError as exc:
        print(f"flag-read gate FAILED: {exc}", file=sys.stderr)
        return 1

    if count > baseline:
        offenders = "\n".join(
            f"    {path}: {n}" for path, n in sorted(by_file.items())
        )
        print(
            f"flag-read gate FAILED: {count} direct MAGI_/CORE_AGENT_ env "
            f"read(s) > baseline {baseline}.\n"
            f"Do not read flags off os.environ directly — register the flag in "
            f"magi_agent/config/flags.py FLAGS and read it via config.flags "
            f"(flag_bool / flag_profile_bool / flag_str / flag_int).\n"
            f"Current per-file counts:\n{offenders}",
            file=sys.stderr,
        )
        return 1

    if count < baseline:
        print(
            f"flag-read gate FAILED: {count} direct read(s) < baseline "
            f"{baseline}.\n"
            f"Nice — you migrated call sites to config.flags. ratchet down: "
            f"update the baseline to {count} so the win is locked in:\n\n"
            f"    uv run --no-sync python scripts/check_flag_reads.py --update\n\n"
            f"and commit scripts/flag_reads_budget.txt in the same PR.",
            file=sys.stderr,
        )
        return 1

    print(f"flag-read gate OK: {count} direct read(s) (== baseline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
