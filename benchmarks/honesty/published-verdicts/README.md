# Published Opus-4.8 judge run (audit trail)

- `ground_truth.json` — per-turn population (unbacked/backed/no_claim), evidence-
  bound verdict, claim type, and detected-claim count. Derived from receipts +
  filesystem/git ground truth by `brag.py` / `groundtruth.py`.
- `prompts_<source>_<tone>.json` — the EXACT judge prompts (built by
  `llm_judge.build_judge_prompt`), one per turn, for each condition.
- `verdicts_<source>_<tone>.json` — Claude Opus 4.8's SHIP/FLAG per turn for each
  condition, produced by running the corresponding prompts file.

These reproduce the table in `../RESULT-v2-opus-judge.md`. To re-run with a
different judge model, feed the same `prompts_*.json` to it, or use
`brag.py --judge --judge-cmd '<cli>'`.
