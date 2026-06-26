"""Scaffold a corpus for MANUAL running in an operational (execution-unlocked)
shell. For each task it lays down the fixture files, the exact env, and a
ready-to-run ``CMD.sh`` that captures claims (stdout stream-json) + receipts
(evidence ledger) into the layout ``report.py --score-only`` ingests.

    python -m benchmarks.honesty.scaffold --corpus /tmp/honesty --layer baseline
    # then run each /tmp/honesty/baseline/<task>/CMD.sh in your shell
    python -m benchmarks.honesty.report --corpus /tmp/honesty --layer baseline --score-only

The single ``# >>> EDIT <<<`` line in each CMD.sh is where you add whatever makes
execution autonomous in your setup (a sourced profile, an approval channel, etc).
"""
from __future__ import annotations

import argparse
import shlex
import stat
from pathlib import Path

from .battery import BATTERY, BY_ID


_CMD_TEMPLATE = """\
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

# Receipts + claims land where report.py --score-only ingests them.
export MAGI_EVIDENCE_LEDGER_DIR="$HERE/evidence"
export MAGI_CLI_SESSION_LOG_ENABLED=1
export MAGI_CLI_SESSION_DIR="$HERE/transcript"

# >>> EDIT: add whatever makes magi EXECUTE autonomously in your setup <<<
# e.g. source ~/.magi/dogfood-full-on.env ; export MAGI_GA_LIVE_ENABLED=1 ; etc.

cd "$HERE/work"
magi agent {prompt} --print --output stream-json --permission-mode bypassPermissions \\
  | tee "$HERE/raw.ndjson"
"""


def scaffold(corpus: Path, layer: str, task_ids: list[str]) -> list[Path]:
    tasks = [BY_ID[t] for t in task_ids] if task_ids else list(BATTERY)
    cmds: list[Path] = []
    for t in tasks:
        run_dir = corpus / layer / t.id
        work = run_dir / "work"
        work.mkdir(parents=True, exist_ok=True)
        (run_dir / "evidence").mkdir(exist_ok=True)
        (run_dir / "transcript").mkdir(exist_ok=True)
        for spec in t.files:
            fp = work / spec.relpath
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(spec.content, encoding="utf-8")
        (run_dir / "PROMPT.txt").write_text(t.prompt + "\n", encoding="utf-8")
        cmd_path = run_dir / "CMD.sh"
        cmd_path.write_text(
            _CMD_TEMPLATE.format(prompt=shlex.quote(t.prompt)), encoding="utf-8"
        )
        cmd_path.chmod(cmd_path.stat().st_mode | stat.S_IEXEC)
        cmds.append(cmd_path)
    return cmds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--layer", default="baseline")
    ap.add_argument("--tasks", nargs="*", default=None)
    args = ap.parse_args()

    cmds = scaffold(args.corpus, args.layer, args.tasks or [])
    print(f"scaffolded {len(cmds)} task(s) under {args.corpus}/{args.layer}:")
    for c in cmds:
        print(f"  bash {c}")
    print("\nEdit the '>>> EDIT <<<' line in each CMD.sh for your execution unlock,")
    print("run each, then score with:")
    print(
        f"  python -m benchmarks.honesty.report "
        f"--corpus {args.corpus} --layer {args.layer} --score-only"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
