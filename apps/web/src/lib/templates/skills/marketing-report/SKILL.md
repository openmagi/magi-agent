---
name: marketing-report
description: Use when generating marketing performance reports across Google Ads and Meta Ads. Creates unified cross-platform reports grouped by campaign purpose with experiment results and trend analysis.
metadata:
  author: openmagi
  version: "2.0"
---

# Marketing Report

Generate cross-platform marketing performance reports covering Google Ads and Meta Ads.

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system where ANALYZE, CREATE, DEPLOY, and TRACK skills work together.

```
ANALYZE (this skill)  -->  CREATE (ad-copywriter, ad-creative-generator)  -->  DEPLOY (google-ads / meta-ads)
       ^                                                         |
       |                     TRACK (ad-experiment-tracker)  <----+
       +--- LEARN (MEMORY.md) <--- results flow back to next cycle
```

**Role:** Entry point of each cycle. Surfaces performance data, experiment results, and actionable next steps that feed into other skills.

## When to Use

- User asks for a "weekly report", "marketing report", or "ad performance summary"
- End of week/month reporting
- Comparing performance across platforms or campaign purposes
- Stakeholder updates
- Starting a new optimization cycle — always begin here

## Unified Terminology

- **campaign** — top-level campaign entity (both platforms)
- **ad_set** (Meta) / **ad_group** (Google) — targeting group within a campaign
- **creative** — individual ad unit
- **variation** — A/B test variant of a creative

## How to Generate

### Step 1: Gather Data

**Google Ads** (if connected):
```bash
integration.sh google-ads/performance?customerId=<ID>&days=7
integration.sh google-ads/campaigns?customerId=<ID>
```

**Meta Ads** (if connected):
```bash
integration.sh meta-ads/insights
integration.sh meta-ads/campaigns
```

If only one platform is connected, generate a single-platform report. Don't fail — just note that the other platform isn't connected.

### Step 2: Load Experiment History

Check for experiment tracking data:
```bash
cat /workspace/ad-experiments.md 2>/dev/null
```

If `ad-experiments.md` exists, extract:
- Recently completed experiments and their results
- Currently running experiments and their progress
- Accumulated learnings (top patterns)

Also check MEMORY.md for the `## Ad Experiment Summary` section with learned patterns.

### Step 3: Calculate Key Metrics

For each campaign, calculate:
- **Total Spend** — always in real currency units (Google: cost_micros / 1,000,000; Meta: cents / 100)
- **Impressions** and **Clicks**
- **CTR** (clicks / impressions x 100)
- **CPC** (cost / clicks)
- **Conversions** and **CPA** (cost / conversions)
- **ROAS** (conversion_value / cost) if available

### Step 4: Classify Campaigns by Purpose

Group every campaign into one of three categories based on its objective, targeting, and naming:

| Purpose | Typical Signals |
|---------|----------------|
| **Acquisition** | Prospecting audiences, broad/interest targeting, conversion objective, "prospecting"/"new"/"cold" in name |
| **Retargeting** | Custom audiences, website visitors, lookalikes, "retargeting"/"remarketing"/"warm" in name |
| **Brand** | Brand keywords, reach/awareness objective, branded terms, "brand"/"awareness" in name |

If unclear, classify as Acquisition by default.

### Step 5: Format Report

Use this template:

```
## Marketing Report — [Date Range]

### Executive Summary
- Total Spend: $X (WoW: +X%)
- Total Conversions: X (WoW: +X%)
- Blended CPA: $X (WoW: +X%)
- Blended ROAS: X.Xx (WoW: +X%)

### Performance by Purpose

#### Acquisition Campaigns
| Campaign | Platform | Spend | Impressions | Clicks | CTR | CPA | ROAS | WoW Spend | WoW CPA |
|----------|----------|-------|-------------|--------|-----|-----|------|-----------|---------|
| [Name] | Google | $X | X | X | X% | $X | X.Xx | +X% | -X% |
| [Name] | Meta | $X | X | X | X% | $X | X.Xx | +X% | -X% |

#### Retargeting Campaigns
| Campaign | Platform | Spend | Impressions | Clicks | CTR | CPA | ROAS | WoW Spend | WoW CPA |
|----------|----------|-------|-------------|--------|-----|-----|------|-----------|---------|
| ... |

#### Brand Campaigns
| Campaign | Platform | Spend | Impressions | Clicks | CTR | CPA | ROAS | WoW Spend | WoW CPA |
|----------|----------|-------|-------------|--------|-----|-----|------|-----------|---------|
| ... |

### Trend Analysis (WoW / MoM)

| Metric | This Week | Last Week | WoW Change | Why |
|--------|-----------|-----------|------------|-----|
| Spend | $X | $X | +X% | [e.g., Budget increase on Campaign A] |
| CPA | $X | $X | -X% | [e.g., New copy applied from Exp #012] |
| CTR | X% | X% | +X% | [e.g., Creative refresh on Meta retargeting] |
| ROAS | X.Xx | X.Xx | +X% | [e.g., Higher-intent audience targeting] |

Always add a "Why" commentary — attribute changes to specific actions:
- New copy applied (from `ad-copywriter`)
- Budget reallocation (from `ad-optimizer`)
- Creative refresh
- Audience changes
- Seasonality / external events
- Experiment results

### Experiment Results

#### Completed Experiments (this period)
| Exp # | Hypothesis | Result | Impact |
|-------|-----------|--------|--------|
| #012 | Question headlines > number headlines | CTR +28% | Applied to 3 campaigns |

#### Running Experiments
| Exp # | Hypothesis | Days Active | Preliminary |
|-------|-----------|-------------|-------------|
| #013 | Price in description improves CVR | 4/7 days | CVR +12% (insufficient data) |

(If no experiments exist, note: "No experiments tracked yet. Use `ad-experiment-tracker` to start tracking changes.")

### Underperformers (High Spend, Low ROAS)
| Campaign | Platform | Spend | CPA | vs Avg CPA | Conversions | Issue |
|----------|----------|-------|-----|------------|-------------|-------|
| [Name] | Google | $X | $X | +X% | X | [e.g., Creative fatigue, audience saturation] |

### Recommended Next Actions

Based on this report, here are specific next steps:

1. **Underperformer copy refresh** — Use `ad-copywriter` to generate new headlines/descriptions for [Campaign Name]. Current CTR X% is below account average.
2. **Budget reallocation** — Use `ad-optimizer` to shift $X/day from [Campaign A] to [Campaign B] which has X% lower CPA.
3. **Creative fatigue check** — Use `creative-analyzer` to assess ad fatigue on [Campaign Name] (frequency X.X, CTR declining).
4. **Audience expansion** — Use `audience-research` to find new targeting options for [Campaign Name] (impression share declining).
5. **Record experiment** — Use `ad-experiment-tracker` to log any optimization changes as experiments.
```

## Rules

- Always include the date range in the report header
- Use actual currency from the ad accounts — convert Google cost_micros (/ 1,000,000) and Meta cents (/ 100) to real currency
- Round percentages to 1 decimal place, currency to 2 decimal places
- All comparisons must include change rate (%) — WoW and MoM when data is available
- Group campaigns by purpose (Acquisition/Retargeting/Brand), not by platform
- Flag campaigns spending >20% of budget with <1 conversion as underperformers
- If week-over-week comparison isn't possible (first report), skip trend section and note it
- Don't fabricate data — if an API call fails, note it and report what you have
- Always end with "Recommended Next Actions" referencing specific skill names
- If `ad-experiments.md` doesn't exist yet, mention that `ad-experiment-tracker` can be used to start tracking
