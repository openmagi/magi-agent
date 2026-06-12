"""Golden capture/regen utility for the control-plane oracle.

Runs the 4 scenario drivers and writes each normalized trace to its golden JSON
(pretty, sorted keys). Guarded behind an explicit ``--write`` flag so it never
auto-overwrites a committed golden. This is the ONLY sanctioned way to mutate a
golden; the regression test fails loudly on any unreviewed control-plane change.

Usage:
    python -m tests.fixtures.neutral_runtime_golden.capture --write
    python -m tests.fixtures.neutral_runtime_golden.capture        # dry-run/diff
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from tests.fixtures.neutral_runtime_golden import scenarios

GOLDEN_DIR = Path(__file__).parent / "golden"

# scenario name -> driver. The key is also the golden file stem.
SCENARIOS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "loop_guard": scenarios.run_loop_guard_scenario,
    "compaction": scenarios.run_compaction_scenario,
    "edit_retry": scenarios.run_edit_retry_scenario,
    "ga_constraint": scenarios.run_ga_constraint_scenario,
}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def render(trace: list[dict[str, Any]]) -> str:
    return json.dumps(trace, indent=2, sort_keys=True) + "\n"


def capture_all(*, write: bool) -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, driver in SCENARIOS.items():
        trace = driver()
        rendered = render(trace)
        path = golden_path(name)
        existing = path.read_text() if path.exists() else None
        if existing == rendered:
            print(f"  unchanged  {name}")
            continue
        changed += 1
        if write:
            path.write_text(rendered)
            print(f"  WROTE      {name}")
        else:
            status = "NEW" if existing is None else "DRIFT"
            print(f"  {status:<10} {name} (run with --write to update)")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write/overwrite the golden JSON files (otherwise dry-run/diff).",
    )
    args = parser.parse_args()
    changed = capture_all(write=args.write)
    if not args.write and changed:
        print(
            f"\n{changed} golden(s) would change. Review the control-plane diff; if "
            f"intended, re-run with --write."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
