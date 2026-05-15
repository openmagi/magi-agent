---
name: ad-optimizer
description: Use when optimizing ad spend, reallocating budgets between campaigns, pausing underperformers, or adjusting bids. Works with Google Ads and Meta Ads. Connects to ad-copywriter and ad-experiment-tracker for full-cycle optimization.
metadata:
  author: openmagi
  version: "2.0"
---

# Ad Optimizer

Automated ad optimization — budget reallocation, underperformer management, spend pacing, and creative refresh pipeline.

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system where ANALYZE, CREATE, DEPLOY, and TRACK skills work together.

```
ANALYZE (marketing-report, creative-analyzer, this skill)
   |
   v
CREATE (ad-copywriter, ad-creative-generator) -- generates new copy + visuals when this skill detects underperformers
   |
   v
DEPLOY (google-ads / meta-ads) -- applies changes
   |
   v
TRACK (ad-experiment-tracker) -- records every optimization as an experiment
   |
   v
LEARN (MEMORY.md) -- accumulated patterns inform future optimizations
```

**Role:** Detects underperformers and inefficiencies, executes budget/bid optimizations, and triggers the copy refresh pipeline when performance issues stem from creative quality.

## When to Use

- User asks to "optimize my ads", "cut bad campaigns", "reallocate budget"
- Scheduled optimization checks (daily/weekly)
- Spend pacing alerts (over/under budget)
- After reviewing a `marketing-report` that flagged underperformers

## Unified Terminology

- **campaign** — top-level campaign entity (both platforms)
- **ad_set** (Meta) / **ad_group** (Google) — targeting group within a campaign
- **creative** — individual ad unit
- **variation** — A/B test variant of a creative

## Pre-Optimization: Load Learned Patterns

Before any optimization, check for accumulated learnings:

1. **MEMORY.md** — look for `## Ad Experiment Summary` section
2. **`/workspace/ad-experiments.md`** — review past experiment results

Apply learned patterns to optimization decisions. Examples:
- "Past 3 experiments showed question headlines improved CTR +23% — recommend copy refresh with question format"
- "Emoji usage in Meta primary text reduced CTR -12% (Exp #011) — avoid in new creatives"
- "Price-focused descriptions improved CVR +15% (Exp #009) — prioritize for campaigns with low CVR"

## Optimization Actions

### 1. Auto-Pause Underperformers

**Criteria for pausing:**
- Campaign has spent >$100 AND has 0 conversions in last 7 days
- Campaign CPA is >3x the account average CPA over last 14 days
- Campaign ROAS is <0.5 over last 14 days (for revenue-optimized campaigns)

**Steps:**
1. Pull campaign performance for last 7-14 days
2. Calculate account-level average CPA
3. Identify campaigns meeting pause criteria
4. **Always confirm with user before pausing** — present the data and recommendation
5. If user approves, execute pause via mutate
6. **Record the action:** Guide user to log this with `ad-experiment-tracker`

**Google Ads:**
```bash
integration.sh google-ads/query '{"customerId":"...","query":"SELECT campaign.id, campaign.name, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign WHERE campaign.status = '\''ENABLED'\'' AND segments.date DURING LAST_14_DAYS"}'
```

**Meta Ads:**
```bash
integration.sh meta-ads/campaigns
integration.sh meta-ads/insights
```

**When to suggest copy refresh instead of pausing:**
If the underperformance is driven by creative metrics (low CTR, high frequency) rather than structural issues (wrong audience, wrong objective):
- CTR below account average but audience metrics (impressions, reach) are healthy → **creative problem**
- CPA high but CTR is normal → likely audience/bidding issue, proceed with pause/budget change
- Frequency >3.0 → creative fatigue, **don't pause — refresh instead**

When creative issues are detected:
> "This campaign's CTR (X%) is below the account average (X%) while impressions remain healthy. This suggests a creative quality issue, not a targeting problem. Use `ad-copywriter` to generate fresh headlines and descriptions for this campaign. Based on past experiments, [learned pattern, e.g., question-type headlines have shown +23% CTR improvement]."

### 2. Budget Reallocation

**Logic:**
1. Rank campaigns by efficiency (CPA or ROAS)
2. Calculate marginal returns — diminishing returns if CPA increases as spend increases
3. Recommend shifting budget from lowest to highest performers

**Output format:**
```
## Budget Reallocation Recommendation

| Campaign | Current Daily | Proposed Daily | Change | Change % | Reason |
|----------|--------------|----------------|--------|----------|--------|
| [Top performer] | $50 | $65 | +$15 | +30% | Lowest CPA ($8), room to scale |
| [Underperformer] | $40 | $25 | -$15 | -37.5% | CPA $24, 3x account avg |
```

**Rules:**
- Never reallocate more than 20% of a campaign's budget in one change without confirmation
- Minimum remaining budget: $10/day per campaign
- Don't touch campaigns in learning period
- Always show change rate (%) alongside absolute change amounts
- All spend values in real currency units (Google: cost_micros / 1,000,000; Meta: cents / 100)

### 3. Spend Pacing

**Daily check:**
1. Compare actual spend vs. expected daily budget
2. Alert if over-pacing (>120% of expected) or under-pacing (<80% of expected)
3. For over-pacing: suggest bid reduction or budget cap
4. For under-pacing: suggest bid increase or targeting expansion

**Output format:**
```
## Spend Pacing Alert

| Campaign | Daily Budget | Actual Spend | Pace | Status |
|----------|-------------|--------------|------|--------|
| [Name] | $100 | $135 | 135% | Over-pacing |
| [Name] | $80 | $52 | 65% | Under-pacing |
```

## Post-Optimization: Experiment Recording

After every optimization action, guide the user to record it as an experiment:

> "This optimization has been applied. To track its impact, use `ad-experiment-tracker` to record this change:
> - **Hypothesis:** [e.g., Shifting $15/day from Campaign A to Campaign B will reduce blended CPA]
> - **Change:** [What was changed]
> - **Measurement period:** 7 days recommended
>
> This allows the system to learn whether this type of optimization works for your account."

## Safety Rules

- **Learning period**: Never modify campaigns with <50 conversions or <7 days of data
- **Minimum spend**: Don't evaluate campaigns with <$100 total spend
- **Confirmation required**: Always present data and get user approval before mutations
- **Budget changes >20%**: Explicitly warn user about the magnitude of change
- **Audit trail**: Log every optimization action with before/after values
- **No cascade**: Don't pause multiple campaigns simultaneously — do one at a time
- **Currency conversion**: Always convert cost_micros (Google) and cents (Meta) to real currency before displaying
- **Experiment tracking**: Recommend recording every optimization action with `ad-experiment-tracker`

## Recommended Pipeline

After running optimization, suggest the natural next steps:

1. **Creative issues detected** → "Use `ad-copywriter` to generate new ad copy for underperforming campaigns"
2. **Audience issues detected** → "Use `audience-research` to find new targeting opportunities"
3. **Changes applied** → "Use `ad-experiment-tracker` to record these changes as experiments"
4. **Next reporting cycle** → "Use `marketing-report` next week to measure the impact of these optimizations"
