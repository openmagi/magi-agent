---
name: google-ads
description: Use when managing Google Ads campaigns, viewing ad performance, adjusting budgets, analyzing keywords, or running GAQL queries. Requires Google Ads integration to be enabled in Settings.
metadata:
  author: openmagi
  version: "2.0"
---

# Google Ads

## Part of Marketing Automation System

This skill is part of the **DEPLOY** stage in the Marketing Automation Cycle:

```
ANALYZE → CREATE → DEPLOY → TRACK → LEARN → (repeat)
```

| Stage | Skills | Role |
|-------|--------|------|
| ANALYZE | `marketing-report`, `ad-optimizer`, `creative-analyzer` | Identify what's working and what isn't |
| RESEARCH | `audience-research` | Find new audiences and keywords |
| CREATE | `ad-copywriter`, `ad-creative-generator` | Generate optimized ad copy + visuals |
| **DEPLOY** | **`google-ads`**, `meta-ads` | **Apply changes to live campaigns** |
| TRACK | `ad-experiment-tracker` | Record hypotheses and measure results |
| ENGAGE | `meta-social`, `meta-insights` | Organic social and audience insights |

**This skill's role:** Execute campaign changes on Google Ads — apply new copy from `ad-copywriter`, adjust budgets from `ad-optimizer` recommendations, and feed results back to `ad-experiment-tracker` for learning.

## Prerequisites

- Google integration connected with Ads permission enabled in Settings
- Google Ads Developer Token entered in Settings
- At least one Google Ads account accessible

## Getting Started

First, list your accessible accounts:

```bash
integration.sh google-ads/accounts
```

This returns customer IDs. Use them in subsequent commands.

## Commands

### List Campaigns

```bash
integration.sh google-ads/campaigns?customerId=1234567890
```

Returns: campaign name, status, budget, impressions, clicks, cost, conversions.

### Performance Summary (Last 30 Days)

```bash
integration.sh google-ads/performance?customerId=1234567890
```

For a custom date range, use `days` parameter:

```bash
integration.sh google-ads/performance?customerId=1234567890&days=7
```

### Custom GAQL Query

```bash
integration.sh google-ads/query '{"customerId":"1234567890","query":"SELECT campaign.name, metrics.impressions, metrics.clicks, metrics.ctr FROM campaign WHERE campaign.status = '\''ENABLED'\'' AND segments.date DURING LAST_7_DAYS ORDER BY metrics.impressions DESC LIMIT 20"}'
```

### Modify Campaigns (Mutate)

```bash
integration.sh google-ads/mutate '{"customerId":"1234567890","mutateOperations":[{"campaignOperation":{"update":{"resourceName":"customers/1234567890/campaigns/555","status":"PAUSED"},"updateMask":"status"}}]}'
```

## Currency Conversion (Micros)

Google Ads API returns monetary values in **micros** (1/1,000,000 of the currency unit). **Always convert to real currency before displaying to the user.**

| API Value (micros) | Real Currency |
|---------------------|---------------|
| 1,500,000 | $1.50 |
| 25,000,000 | $25.00 |
| 350,000 | $0.35 |

**Rules:**
- Divide all `cost_micros`, `average_cpc`, `budget_amount_micros` values by 1,000,000
- Display with currency symbol and 2 decimal places (e.g., `$12.50`, `€8.30`)
- In comparison tables, show both the value and the change rate (%):

| Campaign | Last Week | This Week | Change |
|----------|-----------|-----------|--------|
| Summer Sale | $125.00 | $98.50 | -21.2% |
| Brand Campaign | $45.00 | $52.30 | +16.2% |

- CTR: multiply by 100 and display as percentage (0.035 → 3.5%)

## GAQL Quick Reference

### Useful Queries

**Campaign performance with CTR and CPC:**
```sql
SELECT campaign.name, campaign.status, metrics.impressions, metrics.clicks,
       metrics.ctr, metrics.average_cpc, metrics.cost_micros, metrics.conversions
FROM campaign
WHERE campaign.status = 'ENABLED' AND segments.date DURING LAST_30_DAYS
ORDER BY metrics.cost_micros DESC
```

**Ad group performance:**
```sql
SELECT ad_group.name, ad_group.status, campaign.name,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM ad_group
WHERE ad_group.status = 'ENABLED' AND segments.date DURING LAST_30_DAYS
ORDER BY metrics.cost_micros DESC LIMIT 30
```

**Keyword performance:**
```sql
SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
       ad_group_criterion.quality_info.quality_score,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM keyword_view
WHERE segments.date DURING LAST_30_DAYS
ORDER BY metrics.cost_micros DESC LIMIT 50
```

**Search terms report:**
```sql
SELECT search_term_view.search_term, campaign.name,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM search_term_view
WHERE segments.date DURING LAST_30_DAYS
ORDER BY metrics.impressions DESC LIMIT 50
```

**Daily spend trend:**
```sql
SELECT segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions
FROM customer
WHERE segments.date DURING LAST_30_DAYS
ORDER BY segments.date ASC
```

**RSA (Responsive Search Ad) asset performance:**
```sql
SELECT ad_group_ad.ad.responsive_search_ad.headlines,
       ad_group_ad.ad.responsive_search_ad.descriptions,
       ad_group_ad_asset_view.performance_label,
       metrics.impressions, metrics.clicks, metrics.conversions
FROM ad_group_ad_asset_view
WHERE segments.date DURING LAST_30_DAYS
ORDER BY metrics.impressions DESC LIMIT 50
```

## Applying Copy from ad-copywriter

When `ad-copywriter` generates new Google RSA copy (15 headlines + 4 descriptions), follow this workflow to deploy it:

### Step 1: Snapshot Current State (Before)

Query the current ad's headlines and descriptions:

```sql
SELECT ad_group_ad.ad.responsive_search_ad.headlines,
       ad_group_ad.ad.responsive_search_ad.descriptions,
       ad_group_ad.ad.id, ad_group.id, campaign.id
FROM ad_group_ad
WHERE campaign.id = CAMPAIGN_ID AND ad_group_ad.status = 'ENABLED'
```

Record the current copy as the "before" state. Save it for `ad-experiment-tracker`.

### Step 2: Apply New Copy via Mutate

Use the mutate endpoint to update the RSA with new headlines/descriptions:

```bash
integration.sh google-ads/mutate '{"customerId":"1234567890","mutateOperations":[{"adGroupAdOperation":{"update":{"resourceName":"customers/1234567890/adGroupAds/AD_GROUP_ID~AD_ID","ad":{"responsiveSearchAd":{"headlines":[{"text":"New Headline 1","pinnedField":"HEADLINE_1"},{"text":"New Headline 2"}],"descriptions":[{"text":"New description text here."}]}}},"updateMask":"ad.responsive_search_ad.headlines,ad.responsive_search_ad.descriptions"}}]}'
```

### Step 3: Register Experiment

After applying, immediately use `ad-experiment-tracker` to record:
- **Hypothesis:** What you expect the new copy to achieve (e.g., "Question-style headlines will improve CTR by 15%+")
- **Before snapshot:** The original headlines/descriptions and their metrics
- **After snapshot:** The new headlines/descriptions applied
- **Measurement period:** Recommend at least 7 days for statistical significance

### Step 4: Verify Deployment

Query again to confirm the changes are live:

```sql
SELECT ad_group_ad.ad.responsive_search_ad.headlines,
       ad_group_ad.ad.responsive_search_ad.descriptions
FROM ad_group_ad
WHERE ad_group_ad.ad.id = AD_ID
```

## Experiment Tracking Integration

**Every change to a campaign should be tracked as an experiment.** This includes:
- Copy changes (headlines, descriptions)
- Budget adjustments
- Bid strategy changes
- Keyword additions/removals
- Ad group pauses/enables

### Recording Pattern

After making any change via the mutate endpoint:

1. **Before:** Capture pre-change metrics (cost, impressions, clicks, CTR, CPA, conversions) for the affected campaign/ad group over the previous 7 days
2. **Change:** Execute the mutation
3. **After:** Tell the user to use `ad-experiment-tracker` with a clear hypothesis and the before-snapshot data
4. **Review:** After the measurement period (7-14 days), use `ad-experiment-tracker` to compare before/after and record the conclusion

### Example Hypothesis Format

> "Switching Campaign X from Manual CPC to Maximize Conversions will reduce CPA by 10%+ while maintaining conversion volume within 5%"

## Pipeline References

### Inputs to this skill
- **From `ad-copywriter`:** New RSA headline/description sets ready to deploy
- **From `ad-optimizer`:** Recommendations to pause underperformers, reallocate budget, change bid strategies
- **From `audience-research`:** New keyword suggestions to add as ad group criteria

### Outputs from this skill
- **To `ad-experiment-tracker`:** Before/after snapshots and hypotheses for every change made
- **To `marketing-report`:** Performance data feeds into cross-platform reporting
- **To `creative-analyzer`:** Asset-level performance data for copy element analysis

### Suggested Next Actions
- After deploying new copy: "Use `ad-experiment-tracker` to register this as an experiment"
- After viewing poor performance: "Use `ad-optimizer` for optimization recommendations, or `ad-copywriter` to generate fresh copy"
- After analyzing results: "Use `marketing-report` for a full cross-platform performance view"

## Important Notes

- Always convert cost values from micros (divide by 1,000,000) before presenting to the user
- CTR is returned as a fraction (0.05 = 5%) — always display as a percentage
- Always check campaign status before mutating — don't modify REMOVED campaigns
- For MCC (manager) accounts, pass `loginCustomerId` as a query param
- GAQL is read-only (SELECT only) — mutations use the mutate endpoint
- Create new campaigns/ad groups in PAUSED status — activate only after user confirmation
- When making multiple changes, batch them in a single mutate call when possible
