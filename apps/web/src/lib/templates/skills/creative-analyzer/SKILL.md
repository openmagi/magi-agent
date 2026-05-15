---
name: creative-analyzer
description: Use when analyzing ad creative performance, detecting creative fatigue, evaluating A/B test results, or breaking down copy element performance. Outputs structured data usable as ad-copywriter input.
metadata:
  author: openmagi
  version: "2.0"
---

# Creative Analyzer

Analyze ad creative performance — fatigue detection, A/B testing, copy element breakdown, and pattern extraction for the copy generation pipeline.

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system where ANALYZE, CREATE, DEPLOY, and TRACK skills work together.

```
ANALYZE (marketing-report, ad-optimizer, this skill)
   |
   +-- this skill extracts winning copy patterns
   |
   v
CREATE (ad-copywriter, ad-creative-generator) -- uses patterns to generate new copy + visuals
   |
   v
DEPLOY (google-ads / meta-ads) -- applies new creatives
   |
   v
TRACK (ad-experiment-tracker) -- tracks creative changes as experiments
```

**Role:** Deep-dives into creative-level performance, classifies copy elements by type and effectiveness, and produces structured pattern summaries that `ad-copywriter` can directly use to generate high-performing ad copy.

## When to Use

- User asks "how are my ads performing?", "creative fatigue?", "which ad is winning?"
- A/B test evaluation
- Creative refresh planning
- Ad format performance comparison
- Before using `ad-copywriter` — run this first to extract winning patterns
- After `ad-experiment-tracker` completes an experiment — analyze what worked

## Unified Terminology

- **campaign** — top-level campaign entity (both platforms)
- **ad_set** (Meta) / **ad_group** (Google) — targeting group within a campaign
- **creative** — individual ad unit
- **variation** — A/B test variant of a creative

## Analysis Types

### 1. Creative Fatigue Detection

**Indicators of fatigue:**
- Frequency > 3.0 (same person sees ad 3+ times)
- CTR declining >20% week-over-week
- CPA increasing >30% with stable targeting
- Impression share dropping without budget changes

**Meta Ads:**
```bash
integration.sh meta-ads/ads
integration.sh meta-ads/insights?level=ad&fields=impressions,clicks,ctr,frequency,cpc,cpa
```

**Google Ads:**
```bash
integration.sh google-ads/query '{"customerId":"...","query":"SELECT ad_group_ad.ad.id, ad_group_ad.ad.responsive_search_ad.headlines, metrics.impressions, metrics.clicks, metrics.ctr, metrics.cost_micros, metrics.conversions FROM ad_group_ad WHERE ad_group_ad.status = '\''ENABLED'\'' AND segments.date DURING LAST_14_DAYS"}'
```

**Output format:**
```
## Creative Fatigue Report

| Creative | Campaign | Platform | Frequency | CTR Trend (WoW) | CPA Trend (WoW) | Status |
|----------|----------|----------|-----------|-----------------|-----------------|--------|
| Ad 1 | Summer Sale | Meta | 4.2 | -25% | +40% | Fatigued |
| Ad 2 | Brand Awareness | Meta | 1.8 | +5% | -10% | Healthy |
| RSA 1 | Search - Branded | Google | N/A | -15% | +22% | Watch |

### Recommendations
- Ad 1: Creative refresh needed. Use `ad-copywriter` to generate new variations. Record refresh with `ad-experiment-tracker`.
- Ad 2: Performing well — consider increasing budget via `ad-optimizer`.
- RSA 1: CTR declining — run copy element analysis (below) to identify weak headlines.
```

### 2. Copy Element Analysis

Classify and evaluate individual copy elements to extract winning patterns.

**Headline Type Classification:**

| Type | Pattern | Example |
|------|---------|---------|
| **Question** | Starts with question word or ends with ? | "Looking for Better ROI?" |
| **Number** | Contains specific numbers, prices, percentages | "Save 50% on Summer Items" |
| **CTA** | Action verb leading, imperative mood | "Start Your Free Trial Today" |
| **Benefit** | Focuses on outcome/value proposition | "Faster Shipping, Happier Customers" |
| **Social Proof** | References reviews, users, awards | "Trusted by 10,000+ Businesses" |
| **Urgency** | Time-limited language | "Last Chance — Ends Tonight" |

**Description Analysis:**

| Property | Classification |
|----------|---------------|
| **Length** | Short (<40 chars), Medium (40-70 chars), Long (>70 chars) |
| **Tone** | Professional, Casual, Urgent, Emotional, Data-driven |
| **Structure** | Feature-focused, Benefit-focused, Problem-solution, Testimonial |

**Google Ads (RSA asset performance):**
```bash
integration.sh google-ads/query '{"customerId":"...","query":"SELECT ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad_asset_view.performance_label FROM ad_group_ad_asset_view WHERE segments.date DURING LAST_30_DAYS"}'
```

**Output format — Copy Element Performance:**
```
## Copy Element Analysis — [Campaign/Account Name]

### Headline Performance by Type
| Type | Count | Avg CTR | Avg CPA | Best Performer | Performance vs Avg |
|------|-------|---------|---------|----------------|-------------------|
| Question | 4 | 4.2% | $9.50 | "Looking for Better ROI?" | +31% CTR |
| Number | 6 | 3.8% | $10.20 | "Save 50% Today" | +19% CTR |
| CTA | 5 | 3.0% | $11.80 | "Start Free Trial" | -6% CTR |
| Benefit | 3 | 3.5% | $10.00 | "Faster Results, Less Effort" | +9% CTR |

### Description Performance
| Length | Tone | Avg CTR | Avg CVR | Count |
|--------|------|---------|---------|-------|
| Medium | Data-driven | 3.2% | 2.1% | 4 |
| Short | Urgent | 3.8% | 1.8% | 3 |
| Long | Professional | 2.9% | 2.3% | 5 |

### Top Patterns (structured for ad-copywriter)
> **Top pattern: Question headlines (CTR 4.2%), action descriptions with data-driven tone (CVR 2.1%)**
>
> **Winning formula:**
> - Headline: Question type — ask about the pain point/desired outcome
> - Headline: Number type — include specific savings/results (2nd priority)
> - Description: Medium length (40-70 chars), data-driven or benefit-focused tone
> - Description: Include specific price/number when possible (CVR +15% from Exp #009)
>
> **Avoid:**
> - Pure CTA headlines without value proposition (below avg CTR)
> - Long professional descriptions (lowest CTR)
> - [Any patterns flagged as negative from past experiments]

### Asset Performance Labels (Google RSA)
| Asset | Type | Performance Label | Headline Type |
|-------|------|------------------|---------------|
| "Looking for Better ROI?" | Headline | BEST | Question |
| "Save 50% Today" | Headline | GOOD | Number |
| "Our Products" | Headline | LOW | Generic |
| "Get results in 7 days..." | Description | BEST | Number + Benefit |
```

### 3. A/B Test Evaluation

**Steps:**
1. Pull performance data for test variations
2. Check minimum data thresholds (1,000+ impressions per variation, 7+ days)
3. Compare key metrics (CTR, CPA, conversion rate) — always include change rate (%)
4. Assess statistical significance (minimum 95% confidence)
5. Classify the winning copy elements by type

**Significance check (simplified):**
- If the difference between variations is >2x the standard error, likely significant
- With 1,000+ clicks per variation and >20% CTR difference, usually significant
- With <100 clicks per variation, too early to call

**Output format:**
```
## A/B Test Results: [Test Name]

| Variation | Impressions | Clicks | CTR | CPA | Conv Rate | Headline Type |
|-----------|------------|--------|-----|-----|-----------|---------------|
| A (Control) | 10,000 | 500 | 5.0% | $12.00 | 2.1% | Number |
| B (Test) | 10,000 | 650 | 6.5% | $9.50 | 2.8% | Question |

**Winner: Variation B** (+30% CTR, -21% CPA, +33% Conv Rate)
**Confidence: High** (sufficient data, >7 days)
**Winning element: Question-type headline** — consistent with past pattern (Exp #008: question headlines +23% CTR)

**Recommendation:** Roll out Variation B as the new control. Record result with `ad-experiment-tracker`. Use `ad-copywriter` to generate more question-type headline variations for other campaigns.
```

### 4. Ad Format Comparison

Compare performance across ad formats:
- Image vs. Video vs. Carousel (Meta)
- Responsive Search Ads vs. other formats (Google)
- Static vs. Dynamic creative

Always include change rate (%) in format comparisons:
```
| Format | CTR | CPA | vs Best Format |
|--------|-----|-----|----------------|
| Video | 4.5% | $8.50 | baseline |
| Carousel | 3.2% | $10.20 | -29% CTR, +20% CPA |
| Image | 2.8% | $11.00 | -38% CTR, +29% CPA |
```

## Structured Output for ad-copywriter

When analysis is complete, always produce a **Copy Generation Brief** at the end — a structured summary that `ad-copywriter` can use directly as input:

```
## Copy Generation Brief (for ad-copywriter)

### Winning Patterns
1. **Headline type:** Question (CTR 4.2%) > Number (CTR 3.8%) > Benefit (3.5%)
2. **Description tone:** Data-driven (CVR 2.1%) > Urgent (CVR 1.8%)
3. **Description length:** Medium 40-70 chars optimal
4. **Key elements:** Specific numbers/prices improve performance

### Avoid
- Pure CTA headlines without value proposition
- Emoji in Meta primary text (CTR -12%, Exp #011)
- Long descriptions (>70 chars) for search campaigns

### Context
- Product/service: [from campaign data]
- Target audience: [from campaign targeting]
- Top competitor patterns: [if available]

### Requested Output
- Google RSA: 15 headlines + 4 descriptions
- Meta: 5 variations (primary text + headline + description)
- Prioritize: [winning headline type] format
```

## Rules

- **Minimum 1,000 impressions** before evaluating any creative
- **Minimum 7 days of data** for trend analysis
- **Flag, don't auto-pause** — creative decisions need human review
- Don't compare creatives across different audiences or objectives (apples to oranges)
- Note external factors that could affect performance (seasonality, promotions, events)
- If data is insufficient, say so — don't make conclusions from small samples
- All comparisons must include change rate (%) — never just absolute numbers
- All spend/cost values in real currency (Google: cost_micros / 1,000,000; Meta: cents / 100)
- Always produce the Copy Generation Brief at the end for `ad-copywriter` pipeline
- Recommend recording A/B test results with `ad-experiment-tracker`
