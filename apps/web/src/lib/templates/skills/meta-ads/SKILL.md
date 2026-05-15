---
name: meta-ads
description: Use when the user asks to create, manage, or analyze Meta (Facebook/Instagram) ad campaigns, ad sets, ads, or budgets.
metadata:
  author: openmagi
  version: "2.0"
---

# Meta Ads Integration

## Part of Marketing Automation System

This skill is part of the **DEPLOY** stage in the Marketing Automation Cycle:

```
ANALYZE → CREATE → DEPLOY → TRACK → LEARN → (repeat)
```

| Stage | Skills | Role |
|-------|--------|------|
| ANALYZE | `marketing-report`, `ad-optimizer`, `creative-analyzer` | Identify what's working and what isn't |
| RESEARCH | `audience-research` | Find new audiences and targeting options |
| CREATE | `ad-copywriter`, `ad-creative-generator` | Generate optimized ad copy + visuals |
| **DEPLOY** | `google-ads`, **`meta-ads`** | **Apply changes to live campaigns** |
| TRACK | `ad-experiment-tracker` | Record hypotheses and measure results |
| ENGAGE | `meta-social`, `meta-insights` | Organic social and audience insights |

**This skill's role:** Execute campaign changes on Meta Ads (Facebook/Instagram) — apply new copy from `ad-copywriter`, adjust budgets from `ad-optimizer` recommendations, and feed results back to `ad-experiment-tracker` for learning.

## Setup

OAuth 연동으로 자동 인증됨. 유저가 Settings > Integrations에서 Meta 계정을 연결하면 (Read+Write 모드), 봇이 광고 관리 권한을 받는다.

**수동 키 설정 불필요.**

## Commands

### 광고 계정 정보 조회

```bash
integration.sh meta/ad-account
```

**응답:**
```json
{
  "id": "act_123456",
  "name": "My Ad Account",
  "currency": "USD",
  "account_status": 1,
  "balance": "0",
  "spend_cap": "0"
}
```

### 캠페인 목록 조회

```bash
integration.sh meta/campaigns                    # 전체 캠페인
integration.sh meta/campaigns?status=ACTIVE     # 활성 캠페인만
```

### 캠페인 생성

```bash
integration.sh meta/campaigns POST '{"name":"새 캠페인","objective":"CONVERSIONS","daily_budget":"5000","status":"PAUSED"}'
```

### 캠페인 수정

```bash
integration.sh meta/campaigns PUT '{"campaign_id":"123","name":"수정된 이름","daily_budget":"10000"}'
```

### 광고 세트(ad_set) 조회

```bash
integration.sh meta/adsets?campaign_id=123
```

### 광고 세트(ad_set) 생성

```bash
integration.sh meta/adsets POST '{"campaign_id":"123","name":"광고세트명","daily_budget":"3000","targeting":{"geo_locations":{"countries":["KR"]},"age_min":25,"age_max":45},"status":"PAUSED"}'
```

### 광고 성과 조회

```bash
integration.sh meta/insights?campaign_id=123                           # 캠페인 성과
integration.sh meta/insights?campaign_id=123&date_preset=last_7d      # 최근 7일
integration.sh meta/insights?level=adset&campaign_id=123              # 광고세트 단위
```

**응답:**
```json
{
  "data": [
    {
      "campaign_name": "캠페인명",
      "impressions": "15000",
      "clicks": "450",
      "spend": "25.50",
      "ctr": "3.0",
      "cpc": "0.057",
      "conversions": "12",
      "cost_per_conversion": "2.13"
    }
  ]
}
```

### 캠페인 일시정지 / 재개

```bash
integration.sh meta/campaigns PUT '{"campaign_id":"123","status":"PAUSED"}'
integration.sh meta/campaigns PUT '{"campaign_id":"123","status":"ACTIVE"}'
```

## Currency Conversion (Cents)

Meta Ads API uses **cents** for budget values. **Always convert to real currency before displaying to the user.**

| API Value (cents) | Real Currency |
|--------------------|---------------|
| 5000 | $50.00 |
| 10000 | $100.00 |
| 350 | $3.50 |

**Rules:**
- Divide all `daily_budget`, `lifetime_budget`, `spend_cap` values by 100 when displaying
- When creating/updating budgets, remember to multiply the user's intended amount by 100 (e.g., user says "$50" → send `"daily_budget":"5000"`)
- Display with currency symbol and 2 decimal places (e.g., `$50.00`, `€8.30`)
- The `spend`, `cpc`, `cost_per_conversion` fields in insights responses are already in real currency units — do NOT divide these
- In comparison tables, always include the change rate (%):

| Campaign | Last Week Spend | This Week Spend | Change |
|----------|----------------|-----------------|--------|
| Summer Sale | $125.00 | $98.50 | -21.2% |
| Retargeting | $45.00 | $52.30 | +16.2% |

## Applying Copy from ad-copywriter

When `ad-copywriter` generates new Meta ad copy (5 variations with primary text, headline, description), follow this workflow to deploy:

### Step 1: Snapshot Current State (Before)

Query current ad performance for the target campaign:

```bash
integration.sh meta/insights?campaign_id=123&date_preset=last_7d
```

Record the current metrics (impressions, clicks, CTR, CPC, conversions, CPA) as the "before" state. Save for `ad-experiment-tracker`.

### Step 2: Create New Ad Set for Testing (Recommended)

For A/B testing new copy, create a new ad_set within the same campaign:

```bash
integration.sh meta/adsets POST '{"campaign_id":"123","name":"Copy Test - Question Headlines v1","daily_budget":"3000","targeting":{"geo_locations":{"countries":["KR"]},"age_min":25,"age_max":45},"status":"PAUSED"}'
```

Then create ads within this ad_set using the `ad-copywriter` generated copy. Each variation becomes a separate ad creative.

### Step 3: Register Experiment

After applying new copy, immediately use `ad-experiment-tracker` to record:
- **Hypothesis:** What you expect the new copy to achieve (e.g., "Price-focused primary text will improve CVR by 20%+")
- **Before snapshot:** The original ad_set metrics
- **After snapshot:** The new ad_set/creative details
- **Measurement period:** Recommend at least 7 days for statistical significance

### Step 4: Activate and Monitor

After user confirmation, activate the new ad_set:

```bash
integration.sh meta/campaigns PUT '{"campaign_id":"123","status":"ACTIVE"}'
```

Check back after the measurement period using:

```bash
integration.sh meta/insights?level=adset&campaign_id=123&date_preset=last_7d
```

## Experiment Tracking Integration

**Every change to a campaign should be tracked as an experiment.** This includes:
- Copy/creative changes
- Budget adjustments (daily_budget, lifetime_budget)
- Targeting changes (audience, geo, age, interests)
- Ad_set pauses/enables
- Objective changes

### Recording Pattern

After making any change:

1. **Before:** Capture pre-change metrics (spend, impressions, clicks, CTR, CPC, conversions, CPA) for the affected campaign/ad_set over the previous 7 days
2. **Change:** Execute the creation or mutation
3. **After:** Tell the user to use `ad-experiment-tracker` with a clear hypothesis and the before-snapshot data
4. **Review:** After the measurement period (7-14 days), use `ad-experiment-tracker` to compare before/after and record the conclusion

### Example Hypothesis Format

> "Switching ad_set X targeting from broad interests to lookalike audience will improve CPA by 15%+ while maintaining reach within 20%"

## Pipeline References

### Inputs to this skill
- **From `ad-copywriter`:** New Meta ad copy sets (5 variations: primary text + headline + description) ready to deploy
- **From `ad-optimizer`:** Recommendations to pause underperforming ad_sets, reallocate budgets, adjust targeting
- **From `audience-research`:** New interest targeting suggestions and audience profiles

### Outputs from this skill
- **To `ad-experiment-tracker`:** Before/after snapshots and hypotheses for every change made
- **To `marketing-report`:** Performance data feeds into cross-platform reporting
- **To `creative-analyzer`:** Ad-level performance data for copy element analysis
- **To `meta-insights`:** Campaign data for organic vs paid comparison

### Suggested Next Actions
- After deploying new copy: "Use `ad-experiment-tracker` to register this as an experiment"
- After viewing poor performance: "Use `ad-optimizer` for optimization recommendations, or `ad-copywriter` to generate fresh copy"
- After analyzing results: "Use `marketing-report` for a full cross-platform performance view"
- For organic strategy: "Use `meta-social` to align organic posting with ad campaign themes"

## Notes

- **Write 권한 필수**: 광고 생성/수정은 Read+Write 모드로 연결해야 가능.
- **예산 단위**: 센트 단위 — 항상 실제 통화로 변환하여 표시 (daily_budget "5000" = $50.00).
- **PAUSED 권장**: 새 캠페인/ad_set 생성 시 PAUSED로 생성 후 유저 확인 받은 뒤 ACTIVE로 변경 권장.
- **Rate Limit**: 광고 API는 시간당 200 calls/ad account.
- **objective 값**: CONVERSIONS, LINK_CLICKS, REACH, BRAND_AWARENESS, VIDEO_VIEWS 등.
- **date_preset 값**: today, yesterday, last_7d, last_14d, last_30d, this_month, last_month.
- When making multiple changes, batch related operations together and register as a single experiment.
