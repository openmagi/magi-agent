# LegalBench Lean Harness — Design

Date: 2026-06-05
Branch: `feat/legalbench-lean-harness`
Status: design (pending implementation plan)

## Goal

Build a first-party **legal harness + recipe pack** for `magi-agent` that
measurably raises a Claude baseline's score on **LegalBench** (Stanford/Guha et
al., US legal-reasoning benchmark — 162 tasks, 6 reasoning types), and exposes
that lift as a property of Magi's composable determinism.

Primary purpose is external credibility (marketing) for the OSS runtime. The
harness must move the number for real, not merely add scaffolding.

## Scope decisions (locked)

- **Lean, evidence-backed recipe pack** — only the levers research ties to
  actual LegalBench score gains. No full agentic stack.
- **Core subset v1** — a curated ~20–40 task subset representative of all 6
  reasoning types. Full 162-task run is a later expansion.
- **No external RAG** — LegalBench tasks are largely closed-book (context is in
  the prompt). Reuse existing grounding machinery only where applicable.
- **Claude-centric + published-baseline comparison** — measure base-vs-harness
  lift on Claude, present alongside the published LegalBench numbers (with
  split/metric footnotes). No multi-provider matrix in v1.

## Research findings that constrain the design (honest framing)

These shaped the lean scope and must be reflected in any external claim:

1. **No harness leaderboard exists for LegalBench.** The official repo
   (`HazyResearch/legalbench`) ships datasets + prompts + `evaluation.py`, no
   ranking/submission server. The only live ranking (vals.ai) is a *frontier-
   model bake-off*, not a harness comparison; top models cluster ~85–87%.
2. **The base model dominates the score; harness lift is second-order.** The
   LegalBench paper explicitly calls its own numbers a *lower bound*. Realistic
   harness-driven ceiling is **low-to-mid single digits** of overall
   balanced-accuracy lift over a well-prompted Claude baseline — meaningful on
   weak reasoning types, but it will not leapfrog a model generation.
3. **No OSS harness to fork.** We build, not fork. The most credible *technique*
   reference is Chain of Logic (rule decomposition) for rule-application tasks.
4. **Evidence-ranked score movers** (corrects the naive prior that favored
   self-consistency / IRAC / citation-verification):
   - **Curated few-shot from the train split** — strongest and cheapest, but
     demonstration choice causes up to ~20-pt balanced-accuracy swings. Curate;
     never random.
   - **Explicit rule-statement injection** — highest *reliable*, low-variance
     lever for rule-conclusion / rule-application tasks (rules are rare in
     pretraining).
   - **Per-task prompt selection (plain vs. legalese)** — moderate,
     model-dependent; decide per task on the train split, do not apply globally.
   - **Constrained output parsing (forced label tokens)** — recovers
     verbalizer-mismatch losses the paper repeatedly flags.
   - Weak / dropped for v1 score purposes: default CoT (can hurt classification),
     self-consistency, IRAC-as-prompt, citation-verification, RAG, debate.

## Core principle: every lever is a measurable deterministic checkpoint

Magi's value framing is *composable determinism* (README §"The Solution"): the
model stays creative; the **state transitions around it are deterministic**
(policy snapshot → context projector → evidence ledger → validators → repair
policy → output projector → audit). Each score lever is implemented as an
independently toggleable checkpoint, and the eval reports each checkpoint's
**marginal lift**. This is both the scientific control and the marketing asset:
"our composable determinism lets you measure which deterministic step earned the
score."

| Lever (evidence-backed) | Determinism stage |
| --- | --- |
| Curated train-split few-shot | context-projector policy (seed-fixed, curated) |
| Explicit rule-statement injection | prompt/context policy |
| Per-task prompt selection (plain/legalese) | policy selection (chosen on train split) |
| Constrained output parsing / forced labels | output projector + validator |

## Architecture

```text
LegalBench task data ─▶ loader (curated subset + train/test split + answer key)
                          │
                          ▼
                  few-shot selector (curated, deterministic)
                          │
                          ▼
                  prompt builder ── rule-statement injection
                          │       └─ plain/legalese variant (train-chosen)
                          ▼
            recipe(first_party/legal) compiled via existing compiler stack
                          │
                          ▼
                  model run (Claude) ─▶ constrained output parser
                          │
                          ▼
                  legal_eval scorer ─▶ report:
                     per reasoning-type + overall balanced accuracy
                     + base-vs-harness lift
                     + per-checkpoint ablation
                     + published-baseline comparison column
```

## Components

### New

| File | Purpose |
| --- | --- |
| `benchmarks/legalbench/loader.py` | Load curated subset + train/test splits + answer keys (HF `nguha/legalbench` format). Includes the curated task manifest covering 6 reasoning types. |
| `benchmarks/legalbench/fewshot.py` | Curated, deterministic (seed-fixed) exemplar selection from the train split. No random sampling. |
| `recipes/first_party/legal/rule_inject.py` | Per-task library of explicit rule statements (e.g. abercrombie, diversity jurisdiction, UCC, hearsay) injected into the prompt. |
| `recipes/first_party/legal/prompt_variants.py` | Plain vs. technical prompt variants + per-task selection decided on the train split. |
| `recipes/first_party/legal/output_parser.py` | Constrained output parsing / forced-label mapping (verbalizer). |
| `recipes/first_party/legal/recipe.py` | Composes the above as deterministic checkpoints through the existing recipe compiler (thin glue). |
| `benchmarks/legal_eval.py` | Pure, post-hoc scorer cloned from `benchmarks/coding_eval.py`: balanced accuracy per reasoning type + overall, scoring categories (pass/fail/partial/abstain/infra-unavailable), base-vs-harness lift, per-checkpoint ablation, published-baseline comparison. |
| `benchmarks/legalbench/runner.py` | Orchestrates live run + `baseline` mode + `ablation` mode (toggle each checkpoint to measure marginal lift). Behind a default-OFF gate. |

### Reused (do not rebuild)

- Recipe compilation: `recipes/compiler.py`, `composition.py`,
  `effective_contract.py`, `materializer.py`; `recipes/first_party/<domain>/`
  package pattern.
- Scoring template: `benchmarks/coding_eval.py` (schema-versioned task classes,
  scoring categories, evidence-completeness, violation detection).
- Optional abstain branch: `harness/repair_policy.py`,
  `recipes/retry_repair_policies.py`.
- Light determinism record: evidence ledger / audit surfaces for the
  composable-determinism narrative.

## Data flow

1. Loader yields curated tasks with train/test splits and answer keys.
2. Few-shot selector picks a fixed, curated exemplar set per task (seed-fixed).
3. Prompt builder assembles: instruction + (optional) explicit rule statement +
   selected variant + curated exemplars + the test instance.
4. Recipe runs the model; constrained parser maps the raw output to a canonical
   label.
5. Scorer compares to the answer key and aggregates per reasoning-type / overall
   balanced accuracy, plus lift and ablation tables.

## Defensible methodology (number credibility)

- Few-shot exemplar selection and plain/legalese choice are decided **on the
  train split only, then frozen**, before any test-set scoring. No test-set
  overfitting.
- Report few-shot variance (seed / exemplar set) — never claim a number from a
  single lucky seed.
- Marketing claim is scoped to **"+X over a well-prompted Claude baseline,
  decomposed per deterministic checkpoint,"** concentrated on weak reasoning
  types (rule-recall, long-doc interpretation). Do **not** claim "tops
  LegalBench." Any comparison to published numbers carries split/metric
  footnotes.

## Gating, cost, safety

- Default-OFF env gate (e.g. `MAGI_LEGAL_HARNESS_ENABLED`) following existing
  harness conventions.
- Cost guards: `--max-tasks`, token/cost ceiling → stop and emit a partial
  report when exceeded.
- No external network in v1 (no RAG) → minimal SSRF surface.

## Error handling

- Task load failure → `infra-unavailable` (not counted as fail), per
  `coding_eval` semantics.
- Provider/model error → retry via `retry_repair_policies`, then
  `infra-unavailable`.
- Output parse failure → `partial` or `fail` per policy.
- Budget exceeded → stop, emit partial report.

## Testing

- Repo convention: fixture-based unit tests.
- Scorer, parser, few-shot selector, rule-injector, and variant selector are
  **pure functions** → tested with fixtures and known answer keys, no live model
  calls (mirrors `coding_eval`'s "no provider calls" property).
- Runner's live path is exercised behind the gate via fixture-record replay.
- Coverage targets: reasoning-type mapping, scoring categories, lift math,
  per-checkpoint ablation math, few-shot determinism, prompt assembly,
  verbalizer mapping.

## Out of scope (v1) / v2 candidates

- Full 162-task run.
- External statute/case-law RAG (only if a corpus-grounded subset proves it
  helps).
- Multi-provider comparison matrix.
- Self-consistency, IRAC-as-prompt, citation-verification, multi-agent debate,
  Chain-of-Logic structured decomposition (revisit Chain-of-Logic for
  rule-application if v1 rule-injection underperforms there).

## Success criteria

1. `legal_eval` produces a reproducible report: per reasoning-type + overall
   balanced accuracy, base-vs-harness lift, and per-checkpoint ablation.
2. Harness shows a positive, reproducible lift over the Claude baseline on the
   curated subset (target: meaningful gain on weak reasoning types; overall lift
   honestly reported even if low single digits).
3. All new pure components covered by fixture tests; suite green.
4. Default-OFF; no behavior change when the gate is off.

## Risks / caveats

- **Low ceiling.** Harness lift is second-order to the model. The deliverable's
  durable value is the *measurement framework* (per-checkpoint determinism lift)
  as much as the absolute number.
- **Few-shot variance.** ±20-pt swings on exemplar choice; mitigated by curation
  + train-split freezing + variance reporting.
- **Comparison fairness.** Harness-vs-base is the defensible claim;
  harness-vs-published-LLM requires same-split/same-metric care.

---

## Methodology / frozen decisions (pending data)

These decisions will be frozen after a train-split sweep.  They are currently
at defaults because no dataset files have been downloaded yet.

### Few-shot indices

`CURATED_INDICES` in `magi_agent/recipes/first_party/legal/fewshot.py` (via
`LegalCheckpoints`) is currently unset per task, so `select_fewshot` uses a
seeded random sample (`seed=0`, `k=4`).  Freezing per-task indices requires:

1. Download `train.tsv` for each task in `manifest.v1.json`.
2. For each task, sweep over candidate exemplar sets (e.g. top-10 by diversity
   or correctness on held-out train fold).
3. Pick the set with highest balanced accuracy on the train split.
4. Record the chosen indices in `LegalCheckpoints.curated_indices` defaults or
   a per-task registry — then commit.

Until this sweep runs, `k=4` seeded-random is a neutral, reproducible
placeholder.

### Prompt variant selection

`PROMPT_VARIANTS` in `magi_agent/recipes/first_party/legal/prompt_variants.py`
is empty in v1.  All tasks default to `"plain"`.  Freezing requires:

1. For each task, run `phrase_instruction(..., variant="plain")` and
   `phrase_instruction(..., variant="technical")` on the train split.
2. Pick the variant with higher balanced accuracy.
3. Record in `PROMPT_VARIANTS` — then commit.

**Do not add entries without empirical train-split validation.**

### Rule statements

`RULE_STATEMENTS` in `magi_agent/recipes/first_party/legal/rule_inject.py`
contains confident entries for four tasks (`abercrombie`, `hearsay`,
`contract_nli_explicit_identification`, `contract_nli_notice_on_compelled_disclosure`).
Additional entries should only be added when the rule is well-established and
can be stated accurately without risk of hallucination.

## Empirical findings — first run (2026-06-05)

First real run: `abercrombie` (95 test) + `hearsay` (94 test), `claude-sonnet-4-5`,
temp 0, parse-on, balanced accuracy.

| Config | abercrombie | hearsay | overall |
| --- | --- | --- | --- |
| zero-shot | 0.589 | 0.665 | 0.627 |
| + rule only | 0.726 | 0.716 | 0.721 |
| + few-shot only (k=5) | 0.779 | 0.615 | 0.697 |
| rule + few-shot (variant off, k=5) | 0.789 | 0.777 | **0.783** |
| variant only | 0.589 | 0.709 | 0.649 |
| full incl. prompt_variant prefix | 0.389 | 0.606 | 0.498 |

Lessons (drove fixes on this branch):
1. **Two defects the run exposed.** (A) The original `baseline_checkpoints()`
   turned `constrained_parse` OFF, so the scorer compared raw prose to exact gold
   labels → degenerate 0.0 baseline → hugely inflated lift. Fixed: baseline keeps
   parsing on. (B) The `prompt_variant` "plain" branch prepended
   `"Read carefully and answer. "`, which broke the few-shot `Q:/A:` format and
   collapsed the combined config (0.783 → 0.498). Fixed: "plain" is now a no-op.
2. **Best simple config = rule + few-shot** (≈0.78), a **+0.16** lift over a
   well-prompted zero-shot baseline (0.627). Rule injection is the most reliable
   single lever; few-shot helps `abercrombie` but slightly hurts `hearsay`
   (task-dependent, as the research predicted).
3. **Base model dominates** — zero-shot Sonnet 4.5 is already ~0.63 here.
4. Caveat: two tasks only (~95 items each); indicative, not a full LegalBench
   number.

### Corrected measurement (proper scorer, max_tokens=512)

A third defect surfaced when validating the above: the `parse_answer`
last-label-wins heuristic grabbed labels out of trailing reasoning, systematically
penalizing verbose (zero-shot) outputs. The table above is therefore diagnostic,
not final. After fixing the scorer (first-label extraction; `parse_rate` added as
a first-class metric) and running with an adequate token budget:

| Config | abercrombie | hearsay | overall | parse_rate |
| --- | --- | --- | --- | --- |
| harness (few-shot + rule) | 0.789 | 0.777 | **0.783** | 1.00 |
| zero-shot baseline | 0.632 | 0.714 | **0.673** | 0.97 |
| lift | +0.158 | +0.062 | **+0.110** | — |

Conclusion: **the harness is sound; the earlier alarming results were measurement
bugs, now fixed** (degenerate baseline, the variant prefix, and the last-label
parser). The harness's few-shot+rule config is essentially the *standard*
LegalBench few-shot protocol, and its absolute balanced accuracy (~0.78) sits in
the expected frontier-model range for these tasks. The honest harness lift over a
fairly-parsed zero-shot is **~+0.11**, with `parse_rate` now exposed (1.00 vs
0.97) so format effects are not hidden.

**How to measure LegalBench properly** (the methodology this exposed): (1)
induce label-only output (few-shot or an answer-only instruction), applied
uniformly to every config compared; (2) extract the answer faithfully
(first label / normalized exact match) and track `parse_rate` rather than
silently scoring unparseable outputs as misses; (3) report absolute balanced
accuracy per task vs published LegalBench numbers — "lift vs bare zero-shot" is
non-standard because LegalBench is itself a few-shot benchmark; (4) use the
same-format ablation to attribute contribution to each checkpoint.

### Validation: our recipe ≈ the official LegalBench recipe

Same model (Sonnet 4.5), same extraction, **prompt the only variable** — our
reconstructed harness vs the official bundled `base_prompt.txt`:

| Recipe | abercrombie | hearsay | overall |
| --- | --- | --- | --- |
| our harness (rule + few-shot) | 0.789 | 0.777 | 0.783 |
| official LegalBench prompt | 0.789 | 0.767 | 0.778 |

Within noise — `abercrombie` is identical. So the harness is **not** leaving
points on the table; ~0.78 is the genuine task ceiling for this model on these
(ambiguous, e.g. descriptive/suggestive) tasks, not a recipe deficiency. The
apparent gap vs the paper's GPT-4 (0.842 on `abercrombie`) is largely a metric
difference: the paper graded these rule-application tasks by lenient manual
*correctness*, whereas we use strict exact-match balanced accuracy.
