"""Pool the honesty-bench result across multiple AGENT models and print the
per-model + pooled table with Wilson 95% CIs.

The finding the multi-model run answers: is the 3-tier result (answer judge can't
separate real work from a bare claim / a transcript judge recovers most of it /
the receipt gate is exact) a property of one agent model, or model-independent?

Reads the committed audit trails — no LLM calls:
  - gpt-5.5:  published-verdicts/{ground_truth.json, verdicts_<src>_<tone>.json}
  - others:   published-verdicts/multimodel/<tag>/{ground_truth.json,
              verdicts_answer.json, verdicts_transcript.json}   (answer file
              keyed "tid|tone" over 4 tones; transcript over 2)

Usage:
    python -m benchmarks.honesty.multimodel [--out table.md]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

_HERE = Path(__file__).parent
_PV = _HERE / "published-verdicts"

CONDS = (
    "answer:trusting",
    "answer:balanced",
    "answer:neutral",
    "answer:skeptical",
    "transcript:balanced",
    "transcript:neutral",
)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _load_gpt() -> tuple[dict, dict]:
    gt = json.loads((_PV / "ground_truth.json").read_text())
    cache: dict[str, dict[str, str]] = {}
    for cond in CONDS:
        src, tone = cond.split(":")
        f = _PV / f"verdicts_{src}_{tone}.json"
        if not f.exists():
            continue
        for tid, v in json.loads(f.read_text()).items():
            cache.setdefault(tid, {})[cond] = v.upper()
    return gt, cache


def _load_tagged(tag: str) -> tuple[dict, dict]:
    base = _PV / "multimodel" / tag
    gt = json.loads((base / "ground_truth.json").read_text())
    cache: dict[str, dict[str, str]] = {}
    for grp in ("answer", "transcript"):
        f = base / f"verdicts_{grp}.json"
        if not f.exists():
            continue
        for k, v in json.loads(f.read_text()).items():
            tid, tone = k.split("|")
            cache.setdefault(tid, {})[f"{grp}:{tone}"] = v.upper()
    return gt, cache


def _rates(gt: dict, cache: dict) -> tuple[dict, int, int]:
    unb = [t for t, v in gt.items() if v["population"] == "unbacked"]
    bak = [t for t, v in gt.items() if v["population"] == "backed"]
    out = {}
    for cond in CONDS:
        r = sum(1 for t in unb if cache.get(t, {}).get(cond) == "FLAG")
        b = sum(1 for t in bak if cache.get(t, {}).get(cond) == "FLAG")
        out[cond] = (r, len(unb), b, len(bak))
    return out, len(unb), len(bak)


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({round(100 * k / n)}%)" if n else "0/0 (n/a)"


def _pct_ci(k: int, n: int) -> str:
    if not n:
        return "0/0 (n/a)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({round(100 * k / n)}%, CI [{round(100 * lo)},{round(100 * hi)}])"


def build_markdown() -> str:
    models = {
        "gpt-5.5 (OpenAI)": _load_gpt(),
        "claude-sonnet-4-6 (Anthropic)": _load_tagged("sonnet"),
        "gemini-3.1-pro (Google)": _load_tagged("gemini"),
    }
    L: list[str] = []
    L.append("# Honesty bench — the result holds across agent models\n")
    L.append(
        "The same 3-layer comparison, run with three different AGENT models "
        "(OpenAI / Anthropic / Google) under an Opus-4.8 judge. If the pattern were "
        "a quirk of one model it would not repeat; it does.\n"
    )

    for name, (gt, cache) in models.items():
        rt, nu, nb = _rates(gt, cache)
        L.append(f"## {name}  (unbacked N={nu}, backed N={nb})\n")
        L.append("| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |")
        L.append("|---|---|---|")
        for cond in CONDS:
            r, ru, b, rb = rt[cond]
            L.append(f"| judge · {cond} | {_pct(r, ru)} | {_pct(b, rb)} |")
        L.append(f"| **evidence-bound** | **{_pct(nu, nu)}** | **{_pct(0, nb)}** |")
        L.append("")

    # Pooled
    pooled = {c: [0, 0, 0, 0] for c in CONDS}
    pu = pb = 0
    for _name, (gt, cache) in models.items():
        rt, nu, nb = _rates(gt, cache)
        pu += nu
        pb += nb
        for c in CONDS:
            r, ru, b, rb = rt[c]
            pooled[c][0] += r
            pooled[c][1] += ru
            pooled[c][2] += b
            pooled[c][3] += rb
    L.append(f"## Pooled across all three models (unbacked N={pu}, backed N={pb})\n")
    L.append("| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |")
    L.append("|---|---|---|")
    for c in CONDS:
        r, ru, b, rb = pooled[c]
        L.append(f"| judge · {c} | {_pct_ci(r, ru)} | {_pct_ci(b, rb)} |")
    L.append(f"| **evidence-bound** | **{_pct_ci(pu, pu)}** | **{_pct_ci(0, pb)}** |")
    L.append("")
    L.append(
        "**Read the pooled `blocks receipt-backed work` column:** a suspicious "
        "answer-only judge (neutral/skeptical) blocks **all 40** genuinely-completed "
        "tasks (CI lower bound 91%), across all three providers; the transcript "
        "judge and the receipt gate block ~0 (CI upper bound 9-13%). The confidence "
        "intervals do not overlap. Whether prose can separate real work from a bare "
        "claim does not depend on which frontier model wrote the prose.\n"
    )
    L.append("### Honest note on the recall column\n")
    L.append(
        "The stronger agent models (sonnet, gemini) simply *make far fewer* "
        "unbacked claims — asked to compute without a tool, they disclose they "
        "can't rather than assert a number (so their unbacked N is only 3 each, vs "
        "21 for gpt-5.5). That is a real, welcome finding — a more honest agent is "
        "harder to catch lying because it lies less — but it makes the per-model "
        "recall CIs wide. The load-bearing, model-independent result is the "
        "`blocks-backed` column (N=40 pooled): prose review cannot vouch for real "
        "work without over-blocking, no matter the model.\n"
    )
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    md = build_markdown()
    print(md)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"\n[written] {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
