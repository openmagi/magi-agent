"""CLI: run the honesty battery (optionally per governance layer), score the
claim-vs-evidence divergence, print a report.

Usage:
    python -m benchmarks.honesty.report --corpus /tmp/honesty \
        --tasks tests_pass_honest --layer baseline
    python -m benchmarks.honesty.report --corpus /tmp/honesty --layer baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .battery import BATTERY, BY_ID
from .runner import RunArtifacts, artifacts_to_turns, ingest_corpus, run_task
from .scorer import ClaimType, score_corpus


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids; default all")
    ap.add_argument("--layer", default="baseline")
    ap.add_argument(
        "--magi-bin",
        default=None,
        help="override the magi binary; default runs the worktree source via the homebrew python",
    )
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument(
        "--score-only",
        action="store_true",
        help="score existing run dirs without invoking the agent",
    )
    ap.add_argument(
        "--eligible",
        nargs="*",
        default=None,
        help="claim types whose producers were live (default: claim types in the run set)",
    )
    args = ap.parse_args()

    tasks = [BY_ID[t] for t in args.tasks] if args.tasks else list(BATTERY)
    if args.score_only:
        # Source-agnostic: ingest whatever is on disk (stream-json capture OR an
        # interactive TUI/serve session transcript) + evidence ledgers.
        turns = ingest_corpus(args.corpus, args.layer, [t.id for t in tasks])
        print(f"[ingest] {len(turns)} run(s) from {args.corpus}/{args.layer}", flush=True)
    else:
        arts: list[RunArtifacts] = []
        for t in tasks:
            print(f"[run] {t.id} (layer={args.layer}) ...", flush=True)
            magi_cmd = [args.magi_bin] if args.magi_bin else None
            a = run_task(
                t, args.corpus, layer=args.layer, magi_cmd=magi_cmd, timeout_s=args.timeout
            )
            print(
                f"      rc={a.returncode} timeout={a.timed_out} "
                f"raw={a.raw_ndjson.exists()} evidence={len(a.evidence_files)}",
                flush=True,
            )
            arts.append(a)
        turns = artifacts_to_turns(arts)
    if args.eligible:
        eligible = [ClaimType(e) for e in args.eligible]
    else:
        eligible = sorted({t.claim_type for t in tasks}, key=lambda c: c.value)
    rep = score_corpus(turns, eligible_types=eligible)

    print("\n=== HONESTY BENCH REPORT ===")
    print(f"layer                : {args.layer}")
    print(f"eligible claim types : {rep.eligible_types}")
    print(f"turns total          : {rep.turns_total}")
    print(f"turns w/ claim       : {rep.turns_with_eligible_claim}")
    print(f"turns CONTRADICTED   : {rep.turns_contradicted}  (headline)")
    print(f"turns diverged       : {rep.turns_diverged}  (contradicted + absent)")
    print(f"claims supported     : {rep.claims_supported}")
    print(f"claims contradicted  : {rep.claims_contradicted}")
    print(f"claims absent        : {rep.claims_absent}")
    print(f"contradiction rate   : {rep.turn_contradiction_rate:.3f}")
    print(f"divergence rate      : {rep.turn_divergence_rate:.3f}")
    print(f"by type              : {json.dumps(rep.by_type)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
