---
name: audience-research
description: Use when researching target audiences, discovering interest categories, estimating audience sizes, or planning targeting strategies across Google Ads and Meta Ads.
metadata:
  author: openmagi
  version: "2.0"
---

# Audience Research

Research and plan ad targeting — interests, demographics, audience sizing, and cross-platform strategy.

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system where ANALYZE, CREATE, DEPLOY, and TRACK skills work together.

```
ANALYZE → RESEARCH → CREATE → DEPLOY → TRACK → LEARN → (repeat)
```

**This skill's role: RESEARCH** — Discover and profile target audiences, then feed structured insights to `ad-copywriter` for data-driven copy generation. Upstream skills (`marketing-report`, `ad-optimizer`, `creative-analyzer`) identify optimization opportunities; this skill translates them into audience targeting strategies and copy generation hints.

**Pipeline flow:**
- **Input from:** `marketing-report` (performance gaps), `ad-optimizer` (underperforming segments)
- **Output to:** `ad-copywriter` (audience profiles + copy hints), `google-ads` / `meta-ads` (targeting specs)
- **Track with:** `ad-experiment-tracker` (record targeting changes as experiments)

## When to Use

- User asks "who should I target?", "find my audience", "audience research"
- Planning a new campaign's targeting
- Expanding or refining existing audience targeting
- Comparing audience options across platforms
- Translating Google search intent data into Meta targeting strategies (or vice versa)

## Capabilities by Platform

### Meta Ads (if connected)

**Interest discovery:**
```bash
integration.sh meta-ads/interests?q=fitness
```

**Interest suggestions (related interests):**
```bash
integration.sh meta-ads/interest-suggestions?interest_id=6003139266461
```

**Audience size estimation:**
```bash
integration.sh meta-ads/audience-estimate '{"targeting":{"geo_locations":{"countries":["US"]},"interests":[{"id":"6003139266461"}],"age_min":25,"age_max":45}}'
```

**Behavior targeting:**
```bash
integration.sh meta-ads/behaviors?q=purchase
```

**Demographic targeting:**
```bash
integration.sh meta-ads/demographics?type=life_events
```

**Geographic targeting:**
```bash
integration.sh meta-ads/geo-locations?q=New+York&type=city
```

### Google Ads (if connected)

**Keyword-based audience research via GAQL:**
```bash
integration.sh google-ads/query '{"customerId":"...","query":"SELECT keyword_view.keyword.text, metrics.impressions, metrics.clicks FROM keyword_view WHERE segments.date DURING LAST_30_DAYS ORDER BY metrics.impressions DESC LIMIT 30"}'
```

**Search term analysis (discover what users actually search):**
```bash
integration.sh google-ads/query '{"customerId":"...","query":"SELECT search_term_view.search_term, metrics.impressions, metrics.clicks, metrics.conversions FROM search_term_view WHERE segments.date DURING LAST_30_DAYS ORDER BY metrics.impressions DESC LIMIT 50"}'
```

## Research Workflow

### Step 1: Understand the Business
Ask the user:
- What product/service are they advertising?
- Who is their current customer? (age, location, interests)
- What's their budget range?
- What platforms do they want to use?

### Step 2: Discover Audiences
- Search for relevant interests and behaviors
- Estimate audience sizes for different targeting combinations
- Identify overlapping vs. unique audiences

### Step 3: Cross-Platform Audience Mapping

When both platforms are connected, bridge Google search intent data with Meta targeting:

1. **Pull Google search terms** — identify what users actually search for
2. **Classify search intent** — categorize terms by intent type:

| Intent Type | Example Search Terms | Audience Signal |
|-------------|---------------------|-----------------|
| Price-sensitive | "cheap X", "X discount", "X coupon" | Budget-conscious buyers |
| Comparison | "X vs Y", "best X for", "X review" | Research-phase, high intent |
| Problem-aware | "how to fix X", "X not working" | Pain-point driven |
| Brand-aware | "[brand name]", "[product name]" | Retargeting candidates |

3. **Map to Meta targeting** — translate intent signals into Meta interest/behavior targeting:

| Google Search Intent | Meta Targeting Suggestion | Rationale |
|---------------------|--------------------------|-----------|
| Price-sensitive terms high volume | Interest: "Coupons", Behavior: "Engaged shoppers" | Same price-conscious mindset |
| Comparison terms | Interest: competitor brands, Lookalike from converters | Research-phase users on social |
| Problem-aware terms | Interest: related solutions, Life Events | Problem-solution alignment |

### Step 4: Competitor Keyword Analysis → Copy Hints

Analyze competitor keywords and structure findings as copy generation hints for `ad-copywriter`:

```
## Competitor Keyword Analysis — Copy Hints for ad-copywriter

### Market Positioning Insights
| Competitor Theme | Volume | Our Opportunity | Suggested Copy Angle |
|-----------------|--------|-----------------|---------------------|
| "affordable X" | High | Price differentiation | Headline: price/value emphasis |
| "best X for beginners" | Medium | Accessibility positioning | Headline: ease-of-use, "no experience needed" |
| "X with free trial" | High | Risk reduction | CTA: free trial / money-back guarantee |

### Gap Keywords (competitors not targeting)
| Keyword Gap | Volume | Suggested Copy Angle |
|------------|--------|---------------------|
| "X for small business" | Medium | Niche positioning, "built for SMBs" |
```

### Step 5: Present Audience Profiles with Copy Recommendations

Structure output so `ad-copywriter` can directly use the audience profiles:

```
## Audience Research: [Product/Service]

### Audience Profile 1: [Name]
| Attribute | Detail |
|-----------|--------|
| Platform | Meta / Google / Both |
| Targeting | [interests, demographics, behaviors] |
| Est. size | X–Y people |
| Intent type | Price-sensitive / Comparison / Problem-aware |
| Rationale | [why this audience fits] |

**Copy hints for `ad-copywriter`:**
- This audience is **price-sensitive** → emphasize discounts, value, ROI in headlines
- High response to **question-format** headlines ("Still paying too much for X?")
- Description should include **concrete numbers** (savings %, pricing)

### Audience Profile 2: [Name]
| Attribute | Detail |
|-----------|--------|
| Platform | Meta / Google / Both |
| Targeting | [interests, demographics, behaviors] |
| Est. size | X–Y people |
| Intent type | Comparison / High-intent |
| Rationale | [why this audience fits] |

**Copy hints for `ad-copywriter`:**
- This audience is **comparison-shopping** → emphasize differentiators, social proof
- Use **"vs" or "unlike"** framing in headlines
- Description should highlight **unique features** competitors lack

### Targeting Strategy
- Start with Audience 1 (highest intent)
- Test Audience 2 after 7 days of data
- Exclude [overlap criteria] to avoid audience cannibalization

### Next Steps
→ Use `ad-copywriter` to generate tailored copy for each audience profile above
→ Record targeting changes with `ad-experiment-tracker` to measure audience performance
→ After 7–14 days, use `marketing-report` to evaluate audience segment performance
```

## Rules

- Don't recommend audiences smaller than 10,000 people (too narrow for optimization)
- Don't recommend audiences larger than 50M without narrowing criteria
- Always suggest at least 2-3 audience options for testing
- Note platform differences (Meta has interest targeting, Google is keyword-based)
- If only one platform is connected, focus on that platform's targeting options
- Always structure audience profiles with explicit copy hints for `ad-copywriter`
- When presenting competitor analysis, frame insights as actionable copy angles
- All currency values must be in real currency units (auto-convert micros/cents)
- All comparison tables must include change rate (%) where applicable
- Recommend recording targeting changes with `ad-experiment-tracker` for tracking
