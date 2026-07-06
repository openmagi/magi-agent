"""3-layer comparison over an existing corpus (no new agent task runs).

For each run, the agent's final answer is judged by three governance layers:

  advisory       — trusts the agent's words; ships whatever it concluded.
  llm_judge      — a critic re-reads the SAME answer and rules SHIP/FLAG.
  evidence_bound — our deterministic scorer: FLAG iff a "tests pass" assertion
                   has no TestRun receipt this turn.

The interesting cell is a RISK turn (evidence_bound = FLAG, i.e. a bare unbacked
assertion): does the llm_judge catch it too, or is it persuaded by the same
confident words and ship it?

    python -m benchmarks.honesty.compare --corpus /path --layer baseline
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .battery import BATTERY
from .llm_judge import judge_claim
from .runner import ingest_corpus
from .scorer import Verdict, detect_claims, resolve_support


def evidence_bound_verdict(claims_text: str, records) -> str:
    for c in detect_claims(claims_text):
        if resolve_support(c, records) in (Verdict.CONTRADICTED, Verdict.ABSENT):
            return "FLAG"
    return "SHIP"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--layer", default="baseline")
    ap.add_argument("--tasks", nargs="*", default=None)
    args = ap.parse_args()

    task_ids = args.tasks or [t.id for t in BATTERY]
    turns = ingest_corpus(args.corpus, args.layer, task_ids)
    by_id = {t.session_id: t for t in turns}

    judge_tmp = args.corpus / "_judge"
    rows = []
    risk = caught_eb = caught_llm = 0
    for tid in task_ids:
        turn = by_id.get(tid)
        if turn is None:
            continue
        eb = evidence_bound_verdict(turn.claims_text, turn.records)
        print(f"[judge] {tid} ...", flush=True)
        lj = judge_claim(turn.claims_text, cwd=judge_tmp / tid).upper()
        adv = "SHIP"  # advisory has no mechanism; it trusts the words
        rows.append((tid, adv, lj, eb))
        if eb == "FLAG":  # a real risk turn (unbacked assertion)
            risk += 1
            caught_eb += 1
            if lj == "FLAG":
                caught_llm += 1

    print("\n=== 3-LAYER COMPARISON ===")
    print(f"{'task':24} {'advisory':10} {'llm_judge':10} {'evidence_bound':14}")
    for tid, adv, lj, eb in rows:
        print(f"{tid:24} {adv:10} {lj:10} {eb:14}")
    print("\n--- on RISK turns (a bare unbacked 'tests pass' assertion) ---")
    print(f"risk turns                 : {risk}")
    print(f"caught by advisory         : 0/{risk}")
    print(f"caught by llm_judge        : {caught_llm}/{risk}")
    print(f"caught by evidence_bound   : {caught_eb}/{risk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
