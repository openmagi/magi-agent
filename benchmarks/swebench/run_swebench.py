from __future__ import annotations

import argparse
import re
from pathlib import Path

from benchmarks.swebench.container import ensure_magi_venv, run_instance
from benchmarks.swebench.dataset import load_verified, select_subset
from benchmarks.swebench.evaluate import run_evaluation
from benchmarks.swebench.predictions import (
    Prediction,
    append_prediction,
    load_completed_ids,
)
from benchmarks.swebench.report import summarize

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-ids", nargs="*", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout-seconds", type=int, default=1800)
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--out-dir", default="benchmarks/swebench/results")
    ap.add_argument("--inference-only", action="store_true")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_id):
        raise SystemExit("--run-id must match [A-Za-z0-9._-]+")

    # Provider-agnostic: resolve whatever the user configured (anthropic / openai
    # / gemini / fireworks) via ~/.magi/config.toml or a provider env key. The
    # magi runtime supports all four through ADK's LiteLlm.
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    cfg = resolve_provider_config(model_override=args.model)
    if cfg is None:
        raise SystemExit(
            "No model provider configured. Set ~/.magi/config.toml or a provider "
            "env key (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / "
            "FIREWORKS_API_KEY)."
        )
    print(f"[provider] {cfg.provider} / {cfg.model}")

    out_dir = Path(args.out_dir) / args.run_id
    preds_path = out_dir / "predictions.jsonl"

    instances = select_subset(
        load_verified(), limit=args.limit, only_ids=args.only_ids
    )
    ensure_magi_venv(REPO_ROOT)

    done = load_completed_ids(preds_path)
    for inst in instances:
        if inst.instance_id in done:
            continue
        result = run_instance(
            inst,
            provider=cfg.provider,
            model=cfg.model,
            api_key=cfg.api_key,
            timeout_seconds=args.timeout_seconds,
        )
        (out_dir / "logs").mkdir(parents=True, exist_ok=True)
        (out_dir / "logs" / f"{inst.instance_id}.log").write_text(
            result.log, encoding="utf-8"
        )
        append_prediction(
            preds_path, Prediction(inst.instance_id, "magi", result.patch)
        )
        print(f"[inference] {inst.instance_id}: patch_bytes={len(result.patch)}")

    if args.inference_only:
        return 0

    outcome = run_evaluation(
        preds_path, run_id=args.run_id, max_workers=args.max_workers
    )
    attempted_ids = load_completed_ids(preds_path)
    summary = summarize(
        resolved_ids=outcome.resolved_ids,
        attempted_ids=attempted_ids,
    )
    print(
        f"[result] resolved {summary.resolved}/{summary.attempted} "
        f"= {summary.resolved_pct}%  report={outcome.report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
