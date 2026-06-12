"""Golden capture/regen for the gate5b dispatch oracle. --write guarded.

Usage:
    python -m tests.fixtures.gate5b_golden.capture --write
    python -m tests.fixtures.gate5b_golden.capture            # dry-run/diff
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from tests.fixtures.gate5b_golden import scenarios

GOLDEN_DIR = Path(__file__).parent / "golden"

SCENARIOS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "dispatch_ok": scenarios.run_dispatch_ok_scenario,
    "dispatch_blocked": scenarios.run_dispatch_blocked_scenario,
}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def render(trace: list[dict[str, Any]]) -> str:
    return json.dumps(trace, indent=2, sort_keys=True) + "\n"


def capture_all(*, write: bool) -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, driver in SCENARIOS.items():
        rendered = render(driver())
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
            print(f"  {'NEW' if existing is None else 'DRIFT':<10} {name}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    changed = capture_all(write=args.write)
    if not args.write and changed:
        print(f"\n{changed} golden(s) would change. If intended, re-run with --write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
