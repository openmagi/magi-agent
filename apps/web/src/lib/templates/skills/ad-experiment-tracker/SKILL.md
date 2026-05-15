---
name: ad-experiment-tracker
description: Track advertising experiments with hypothesis logging, result recording, learning accumulation, and MEMORY.md auto-sync
metadata:
  author: openmagi
  version: "1.0"
---

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system of 10 skills that work together:

```
[ANALYZE] → [CREATE] → [DEPLOY] → [TRACK] → [LEARN] → repeat
```

| Stage | Skills | Role |
|-------|--------|------|
| ANALYZE | `marketing-report`, `ad-optimizer`, `creative-analyzer` | Identify what's working and what's not |
| RESEARCH | `audience-research` | Understand target audiences and intent |
| CREATE | `ad-copywriter`, `ad-creative-generator` | Generate optimized ad copy + visuals |
| DEPLOY | `google-ads`, `meta-ads` | Apply copy to live campaigns |
| ENGAGE | `meta-social`, `meta-insights` | Organic content and analytics |
| **TRACK** | **`ad-experiment-tracker`** ← you are here | **Record experiments, track results, accumulate learnings** |

**This skill's role:** The learning engine of the system. Every ad change becomes a trackable experiment. Results accumulate into rules that make future ad creation smarter.

---

## Storage

### Primary Log: `/workspace/ad-experiments.md`

Full experiment history. Never truncate — append only.

### Summary: MEMORY.md `Ad Experiment Summary` section

Auto-synced after every `complete` or `review` action. Contains:
- Total experiment count and success/fail ratio
- Top 3 most recent learnings
- Active rules for copy generation

---

## Workflows

### 1. `start` — Register New Experiment

When the user makes any ad change (new copy, budget shift, targeting change, creative swap), register it:

```markdown
## Experiment #[auto-increment] — [YYYY-MM-DD]

- **Hypothesis:** [What you expect to happen and why]
- **Platform:** [Google Ads / Meta Ads / Both]
- **Campaign:** [Campaign name or ID]
- **Change type:** [Copy / Budget / Targeting / Creative / Bid strategy]
- **변경 내용:** [Specific description of what changed]
- **Control:** [What the baseline/comparison is]
- **Primary metric:** [CTR / CPA / CVR / ROAS / other]
- **Secondary metrics:** [List other metrics to watch]
- **측정 기간:** [Duration] ([start date] ~ [end date])
- **Min data threshold:** [e.g., 1,000 impressions, 50 conversions]
- **Status:** ⏳ In Progress
```

**Rules:**
- Auto-increment experiment number from the last entry in `ad-experiments.md`
- If file doesn't exist, create it with a header and start at #001
- Always include a falsifiable hypothesis — "this should improve X" is too vague
- Set realistic measurement period (minimum 7 days for most ad experiments)
- Define min data threshold to avoid premature conclusions

### 2. `check` — Review In-Progress Experiments

For each experiment with Status `⏳ In Progress`:

1. Check if measurement period has elapsed
2. Pull current performance data:
   - Google Ads: `integration.sh google-ads/performance?customerId=<id>&days=<period>`
   - Meta Ads: `integration.sh meta/insights?campaign_id=<id>&date_preset=<period>`
3. Compare against control/baseline
4. Report interim results:

```markdown
### Interim Check — [date]
- **Days elapsed:** X of Y
- **Data volume:** [impressions/conversions vs threshold]
- **Primary metric:** [current value] vs control [value] ([+/-X%])
- **Secondary metrics:** [values]
- **Assessment:** [Too early / Trending positive / Trending negative / Inconclusive]
```

**Rules:**
- Don't conclude before min data threshold is met
- If trending strongly negative (>30% worse) with sufficient data, flag for early termination consideration
- Never auto-terminate — always recommend, let user decide

### 3. `complete` — Record Results & Extract Learning

When measurement period ends or user decides to conclude:

1. Pull final performance data
2. Calculate statistical significance (if possible):
   - Difference > 2× standard error = likely significant
   - < 1,000 impressions per variant = insufficient data (note this)
3. Update experiment entry:

```markdown
- **Status:** ✅ Complete (or ❌ Failed or ⚠️ Inconclusive)
- **결과:**
  - Primary: [metric] [before] → [after] ([+/-X%])
  - Secondary: [metric] [before] → [after] ([+/-X%])
- **Statistical confidence:** [High/Medium/Low — explain]
- **결론:** [One sentence — what did we learn?]
- **Action:** [What to do next based on this result]
```

4. **MEMORY.md Auto-Sync** — After completing, update the `Ad Experiment Summary` section:

```markdown
## Ad Experiment Summary

- **총 실험:** [N]회 | **성공:** [N] | **실패:** [N] | **진행중:** [N]
- **최근 학습 (Top 3):**
  1. [Most recent significant learning] (Exp #XXX)
  2. [Second most recent] (Exp #XXX)
  3. [Third most recent] (Exp #XXX)
- **카피 생성 시 적용할 규칙:**
  - [Rule derived from successful experiments]
  - [Rule derived from failed experiments — what to avoid]
  - [Any other validated patterns]
```

**Rules for summary sync:**
- Top 3 learnings = most recent experiments with clear conclusions (skip inconclusive)
- Copy rules = only from experiments with High or Medium statistical confidence
- Remove rules that have been contradicted by newer experiments
- Keep summary concise — max 15 lines

### 4. `review` — Analyze Experiment History

Analyze all experiments in `ad-experiments.md` for meta-patterns:

**Analysis to perform:**
1. **Win rate by change type:** Which types of changes (copy/budget/targeting) succeed most often?
2. **Platform patterns:** Do certain strategies work better on Google vs Meta?
3. **Copy pattern analysis:** Which headline types, tones, or structures consistently win?
4. **Time patterns:** Any seasonal or day-of-week effects?
5. **Diminishing returns:** Are recent experiments showing smaller improvements? (creative fatigue)

**Output format:**

```markdown
## Experiment Review — [date]

### Overview
- Total experiments: [N] | Win rate: [X%]
- Avg improvement (wins): [+X%] | Avg decline (losses): [-X%]

### Patterns by Change Type
| Change Type | Experiments | Wins | Win Rate | Avg Impact |
|-------------|-------------|------|----------|------------|
| Copy | X | X | X% | +X% |
| Budget | X | X | X% | +X% |
| Targeting | X | X | X% | +X% |

### Top Performing Patterns
1. [Pattern] — [evidence from X experiments]
2. [Pattern] — [evidence]

### Patterns to Avoid
1. [Anti-pattern] — [evidence from X experiments]

### Recommendations
- [Specific actionable recommendation based on patterns]
```

After review, update MEMORY.md summary with any new rules discovered.

---

## Integration with Other Skills

| Skill | When to use ad-experiment-tracker |
|-------|----------------------------------|
| `ad-copywriter` | After generating new copy → `start` experiment |
| `ad-optimizer` | After pausing/reallocating → `start` experiment |
| `google-ads` | After any campaign mutation → `start` experiment |
| `meta-ads` | After any campaign/ad_set change → `start` experiment |
| `meta-social` | After posting with new content strategy → `start` experiment |
| `marketing-report` | During weekly report → `check` active experiments |
| `creative-analyzer` | When analyzing creative performance → `review` history |
| `audience-research` | After changing targeting based on research → `start` experiment |

---

## Example Full Cycle

```
1. marketing-report shows Campaign A CTR dropped 20% WoW
2. creative-analyzer identifies headline fatigue (frequency 4.2, CTR declining)
3. ad-copywriter generates 15 new headlines (question-type prioritized per past learning)
4. google-ads applies new headlines via RSA mutate
5. ad-experiment-tracker START: "Question headlines replacing fatigued number headlines should recover CTR to 3.5%+"
6. [7 days pass]
7. ad-experiment-tracker CHECK: CTR at 3.8%, +26% vs control — trending positive
8. ad-experiment-tracker COMPLETE: ✅ Success, CTR 3.8% (+26%), CPA -18%
9. MEMORY.md updated: "Question headlines recover fatigued campaigns effectively"
10. Next cycle: ad-copywriter prioritizes question headlines with higher confidence
```
