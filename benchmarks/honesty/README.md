# Honesty bench — trust the receipt, not the prose

Autonomous agents assert runtime-verifiable results: *"the tests pass"*, *"the
sum is 333"*, *"I edited the file"*, *"I committed the change."* Sometimes a typed
receipt backs the sentence. Sometimes only the sentence exists. This bench
measures how well different governance layers tell the two apart — on the **same
agent transcripts**.

## The three layers

| layer | what it reads | how it decides |
|---|---|---|
| **advisory** | the agent's words | trusts them; ships everything |
| **LLM-judge** | the agent's final answer only (no receipts) | a second model rules SHIP / FLAG |
| **evidence-bound** | the turn's typed evidence receipts | FLAG iff an assertive claim has no backing receipt |

## Result (magi-agent 0.1.110, N = 16 unbacked + 11 backed)

Two things matter: **catch the unbacked assertions** (recall) and **don't
false-flag genuinely-completed work** (FPR). The judge is **Claude Opus 4.8** (a
frontier model), run fresh per turn seeing only the agent's final answer, swept
across three prompt tones so the comparison is not one cherry-picked prompt.

| layer | catches unbacked (recall ↑) | false-flags real work (FPR ↓) |
|---|---|---|
| advisory (trust the words) | 0/16 (0%) | 0/11 (0%) |
| Opus 4.8 judge · trusting | 0/16 (0%) | 0/11 (0%) |
| Opus 4.8 judge · neutral | 16/16 (100%) | 11/11 (100%) |
| Opus 4.8 judge · skeptical | 16/16 (100%) | 11/11 (100%) |
| **evidence-bound (reads the receipt)** | **16/16 (100%)** | **0/11 (0%)** |

**Even a frontier judge cannot be tuned to a useful operating point** — it is a
pure on/off switch on prompt tone. Trust it more and it ships all 27 turns
(misses every unbacked claim). Distrust it and it flags all 27 — including the 11
genuinely-completed edits and commits. There is no middle tone, because the
discriminating signal (the receipt) is *not in the prose*. The evidence gate
reads that signal directly, so it gets both columns exactly: every unbacked
assertion flagged, every real completion shipped. (An earlier run with a gpt-5.5
judge gives the same shape — the failure is model-independent.)

The backed turns are independently verifiable: after each one the file on disk
holds the corrected code (`return max(xs)`, `return n * n`, ...) and the commit
turns have a new entry in `git log`. A skeptical reader flagging those as
"unverified" is flagging work that provably happened.

## Why this is fair (not a strawman)

- **evidence-bound is the measurement, not a predictor.** It reads the receipt
  that *defines* "backed", so its recall/FPR are exact by construction. That is
  the whole point: "did the agent verify?" is a lookup, not a guess. advisory and
  the LLM-judge are the predictors trying to approximate that lookup from prose;
  their numbers are the empirical finding.
- **The judge is swept, not cherry-picked.** trusting → neutral → skeptical. No
  tone wins both columns.
- **The claim detector is conservative and claim-type-blind.** It counts only
  explicitly assertive phrasing, so the bench *under-reports* divergence; it never
  invents it. Turns where the model honestly discloses it did not verify
  ("I did not run the suite, but based on reading…") are excluded, not counted as
  lies.
- **Ground truth is independent of every layer** — it is the presence/absence of
  a typed receipt, cross-checked against the filesystem and `git log`.

## Honest scope

- The model under test is broadly honest about **test execution**: asked to
  confirm a suite passes without running it, it discloses the non-execution or
  refuses — so those turns are `no_claim`, not divergences. The residual,
  measured risk is **flat assertions of computed/derived results** stated without
  a hedge and without a receipt.
- This is a demonstration harness at N = 16/11, not a multi-model leaderboard.
  Scaling N or adding models is just more compute; the mechanism is claim-type-
  agnostic and model-independent by design.

## Reproduce

```bash
# 1. generate a corpus (real agent runs, neutral runtime)
python -m benchmarks.honesty.report --corpus /path/corpus --layer baseline \
    --tasks calc_a ... edit_smoke_a ... commit_smoke_a ...

# 2. score the three layers + sweep the judge tones -> brag.md
python -m benchmarks.honesty.brag --corpus /path/corpus --layer baseline \
    --judge --tones trusting neutral skeptical --out brag.md
```

Layers, populations (unbacked / backed / no_claim), and per-turn verdicts are all
in `brag.py`; the claim detector and receipt resolver are in `scorer.py`; the
swept judge is `llm_judge.py`.
