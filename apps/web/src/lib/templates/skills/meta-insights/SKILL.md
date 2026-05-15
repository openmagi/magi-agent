---
name: meta-insights
description: Use when the user asks about Facebook page or Instagram account analytics, metrics, follower stats, post performance, or engagement data.
metadata:
  author: openmagi
  version: "2.0"
---

# Meta Insights Integration

Facebook 페이지 및 Instagram 계정의 인사이트/분석 데이터를 조회한다.

## Part of Marketing Automation System

이 스킬은 Open Magi Marketing Automation Cycle의 일부이다:

```
ANALYZE → RESEARCH → CREATE → DEPLOY → TRACK → LEARN → (repeat)
```

**이 스킬의 역할: ENGAGE (분석)** — 오가닉 소셜 성과를 분석하고, 유료 광고 성과와 비교하여 통합적인 마케팅 인사이트를 제공한다. 분석 결과는 다음 사이클의 콘텐츠 전략에 반영된다.

**파이프라인 흐름:**
- **입력:** `meta-social` (발행된 포스트 성과 추적), `meta-ads` (유료 광고 성과 비교 대상)
- **출력:** `marketing-report` (통합 리포트에 오가닉 성과 포함), `ad-copywriter` (성과 좋은 콘텐츠 패턴 전달)
- **추적:** `ad-experiment-tracker` (콘텐츠 실험 결과 기록)

## Setup

OAuth 연동으로 자동 인증됨. 유저가 Settings > Integrations에서 Meta 계정을 연결하면, 봇이 자동으로 인사이트 조회 권한을 받는다.

**수동 키 설정 불필요.**

## Commands

### Facebook 페이지 인사이트

```bash
integration.sh meta/page-insights                           # 최근 28일
integration.sh meta/page-insights?period=day&since=2026-03-01&until=2026-03-11
```

**응답:**
```json
{
  "data": [
    { "name": "page_impressions", "period": "day", "values": [{"value": 1500, "end_time": "2026-03-11"}] },
    { "name": "page_engaged_users", "period": "day", "values": [{"value": 230, "end_time": "2026-03-11"}] },
    { "name": "page_fans", "period": "lifetime", "values": [{"value": 5200}] },
    { "name": "page_views_total", "period": "day", "values": [{"value": 890, "end_time": "2026-03-11"}] }
  ]
}
```

### Instagram 계정 인사이트

```bash
integration.sh meta/ig-insights                              # 최근 28일
integration.sh meta/ig-insights?period=day&since=2026-03-01&until=2026-03-11
```

**응답:**
```json
{
  "data": [
    { "name": "impressions", "period": "day", "values": [{"value": 3200, "end_time": "2026-03-11"}] },
    { "name": "reach", "period": "day", "values": [{"value": 2100, "end_time": "2026-03-11"}] },
    { "name": "profile_views", "period": "day", "values": [{"value": 150, "end_time": "2026-03-11"}] },
    { "name": "follower_count", "period": "day", "values": [{"value": 1250, "end_time": "2026-03-11"}] }
  ]
}
```

### 게시물별 성과 조회

```bash
integration.sh meta/post-insights?post_id=123_456           # Facebook 게시물
integration.sh meta/ig-media-insights?media_id=789           # Instagram 게시물
```

**Facebook 게시물 응답:**
```json
{
  "data": [
    { "name": "post_impressions", "values": [{"value": 850}] },
    { "name": "post_engaged_users", "values": [{"value": 120}] },
    { "name": "post_clicks", "values": [{"value": 45}] },
    { "name": "post_reactions_like_total", "values": [{"value": 30}] }
  ]
}
```

**Instagram 게시물 응답:**
```json
{
  "data": [
    { "name": "impressions", "values": [{"value": 2300}] },
    { "name": "reach", "values": [{"value": 1800}] },
    { "name": "engagement", "values": [{"value": 95}] },
    { "name": "saved", "values": [{"value": 12}] }
  ]
}
```

## Organic vs Paid 성과 비교 분석

Meta에서 오가닉(무료) 콘텐츠와 유료 광고(Meta Ads)의 성과를 비교 분석하여 예산 배분 및 콘텐츠 전략을 최적화한다.

### Step 1: 오가닉 성과 수집

페이지/계정 인사이트와 최근 포스트 성과를 조회한다:

```bash
integration.sh meta/page-insights?period=day&since=2026-03-01&until=2026-03-11
integration.sh meta/ig-insights?period=day&since=2026-03-01&until=2026-03-11
integration.sh meta/page-feed?limit=25
```

각 포스트의 개별 성과도 조회:
```bash
integration.sh meta/post-insights?post_id=<post_id>
integration.sh meta/ig-media-insights?media_id=<media_id>
```

### Step 2: 유료 광고 성과 수집

같은 기간의 Meta Ads 성과를 조회한다:

```bash
integration.sh meta-ads/insights
integration.sh meta-ads/campaigns
```

### Step 3: Organic vs Paid 비교 리포트 작성

아래 포맷으로 비교 분석을 출력한다:

```
## Organic vs Paid 성과 비교 — [기간]

### 종합 비교
| Metric | Organic | Paid | Paid vs Organic |
|--------|---------|------|-----------------|
| Impressions | 45,000 | 120,000 | +167% |
| Reach | 32,000 | 85,000 | +166% |
| Engagement | 2,800 | 3,500 | +25% |
| Engagement Rate | 6.2% | 2.9% | -53% |
| Clicks | 1,200 | 4,800 | +300% |
| Cost | $0 | $1,250.00 | — |
| Cost per Engagement | $0 | $0.36 | — |
| Cost per Click | $0 | $0.26 | — |

### 핵심 인사이트
- **Organic engagement rate가 Paid보다 2배 이상** → 충성 팔로워 기반이 강함
- **Paid reach가 Organic 대비 +166%** → 신규 유저 확보에는 유료 광고가 효과적
- **Cost per engagement $0.36** → 업종 평균 대비 [높음/낮음] 수준

### 콘텐츠별 비교
| 콘텐츠 유형 | Organic Eng. Rate | Paid Eng. Rate | 차이 | 권장 |
|------------|-------------------|----------------|------|------|
| 이미지+텍스트 | 7.2% | 3.1% | -57% | 오가닉 우선 |
| 동영상 | 5.8% | 4.5% | -22% | 양쪽 모두 효과적 |
| 캐러셀 | 8.1% | 3.8% | -53% | 오가닉 우선 |
| 링크 포스트 | 2.1% | 2.8% | +33% | 유료 부스팅 권장 |

### 예산 배분 제안
- **Organic에 집중할 콘텐츠**: 높은 engagement rate 유형 (캐러셀, 이미지+텍스트)
- **Paid로 부스팅할 콘텐츠**: reach 확대가 필요한 유형 (링크 포스트, 전환 목적)
- **전환 효율**: 오가닉에서 engagement rate 높은 포스트를 유료로 부스팅하면 CPE 절감 가능

### 다음 단계
→ 오가닉 성과 좋은 포맷을 `ad-copywriter`에 전달하여 유료 광고 카피에 반영
→ `marketing-report`에서 Google Ads 포함 전체 채널 통합 비교 확인
→ 예산 재배분이 필요하면 `ad-optimizer` 사용
```

### Step 4: 실험 추적 연동

Organic vs Paid 비교 결과를 기반으로 전략을 변경할 때, `ad-experiment-tracker`로 기록한다:

```
→ 전략 변경을 실험으로 기록하려면 `ad-experiment-tracker`를 사용하세요.
  예시: "오가닉 고성과 포스트를 유료 부스팅했을 때 CPE 변화 측정"
  - Hypothesis: 오가닉 engagement rate 상위 포스트를 부스팅하면 CPE가 평균 대비 30% 절감될 것
  - 측정 기간: 14일
  - 결과 확인: 이 스킬로 Organic vs Paid 재비교 후 `ad-experiment-tracker`로 결과 기록
```

## Notes

- **최소 100 팔로워**: Instagram 인사이트는 팔로워 100명 이상인 비즈니스/크리에이터 계정에서만 제공.
- **데이터 보존기간**: 인사이트 데이터는 최근 2년치만 조회 가능.
- **period 값**: day, week, days_28, month, lifetime.
- **Rate Limit**: 인사이트 API는 시간당 200 calls/user.
- **비즈니스 계정 필수**: Instagram 인사이트는 비즈니스 또는 크리에이터 계정에서만 사용 가능.
- **비교 데이터**: 모든 성과 비교 시 반드시 변화율(%)을 포함한다.
- **금액 표시**: 실제 통화 단위로 표시한다 (cents 자동 변환).
- **Organic vs Paid 비교**: Meta Ads가 연결되어 있지 않으면, 오가닉 분석만 제공하고 유료 비교 섹션은 생략한다.
