---
name: meta-social
description: Use when the user asks to post on Facebook or Instagram, reply to DMs, check messages, or manage Meta social media accounts.
metadata:
  author: openmagi
  version: "2.0"
---

# Meta Social Integration

Facebook 페이지 포스팅, Instagram 게시물 발행, DM 관리를 수행한다.

## Part of Marketing Automation System

이 스킬은 Open Magi Marketing Automation Cycle의 일부이다:

```
ANALYZE → RESEARCH → CREATE → DEPLOY → TRACK → LEARN → (repeat)
```

**이 스킬의 역할: ENGAGE** — 오가닉 소셜 콘텐츠의 발행과 관리를 담당한다. 과거 포스트 성과를 분석하여 성과가 좋은 포맷을 참조하고, 데이터 기반으로 새 포스트를 작성한다.

**파이프라인 흐름:**
- **입력:** `ad-copywriter` (생성된 카피/콘텐츠), `meta-insights` (과거 포스트 성과 데이터)
- **출력:** `meta-insights` (발행 후 성과 추적), `ad-experiment-tracker` (포스트 실험 기록)
- **추적:** `ad-experiment-tracker` (콘텐츠 실험 기록 및 학습)

## Setup

OAuth 연동으로 자동 인증됨. 유저가 Settings > Integrations에서 Meta 계정을 연결하면, 봇이 자동으로 API 접근 권한을 받는다. 토큰은 chat-proxy가 자동 관리하며 만료 시 자동 갱신됨.

**수동 키 설정 불필요.**

## Commands

### Facebook 페이지 게시물 작성

```bash
integration.sh meta/page-post POST '{"message":"게시물 내용"}'
```

링크 포함 게시물:
```bash
integration.sh meta/page-post POST '{"message":"내용","link":"https://example.com"}'
```

**응답:**
```json
{
  "id": "page_id_post_id"
}
```

### Facebook 페이지 게시물 조회

```bash
integration.sh meta/page-feed              # 최근 10개
integration.sh meta/page-feed?limit=25     # 최근 25개
```

**응답:**
```json
{
  "data": [
    {
      "id": "123_456",
      "message": "게시물 내용",
      "created_time": "2026-03-11T...",
      "likes": { "summary": { "total_count": 15 } },
      "comments": { "summary": { "total_count": 3 } }
    }
  ]
}
```

### Instagram 게시물 발행

```bash
integration.sh meta/ig-post POST '{"caption":"캡션 내용","image_url":"https://example.com/image.jpg"}'
```

캐러셀 (여러 이미지):
```bash
integration.sh meta/ig-carousel POST '{"caption":"캡션","image_urls":["https://img1.jpg","https://img2.jpg"]}'
```

### Instagram DM 조회

```bash
integration.sh meta/ig-conversations       # 최근 대화 목록
```

### Instagram DM 답장

```bash
integration.sh meta/ig-reply POST '{"conversation_id":"대화_ID","message":"답장 내용"}'
```

### Facebook Messenger 조회

```bash
integration.sh meta/messenger-conversations  # 최근 대화 목록
```

### Facebook Messenger 답장

```bash
integration.sh meta/messenger-reply POST '{"recipient_id":"수신자_PSID","message":"답장 내용"}'
```

## 성과 기반 포스팅 (Performance-Based Posting)

새 포스트를 작성하기 전에 과거 성과를 분석하고, 잘된 포맷을 참조하여 데이터 기반으로 콘텐츠를 만든다.

### Step 1: 과거 포스트 성과 확인

최근 포스트를 가져온 후, 각 포스트의 성과 데이터를 조회한다:

```bash
integration.sh meta/page-feed?limit=25
# 각 포스트의 ID로 성과 조회
integration.sh meta/post-insights?post_id=123_456
```

Instagram의 경우:
```bash
integration.sh meta/ig-media-insights?media_id=789
```

### Step 2: 성과 패턴 분석

조회한 데이터를 아래 포맷으로 분석한다:

```
## 최근 포스트 성과 분석

| # | 포스트 요약 | 유형 | Impressions | Engagement | Engagement Rate | vs 평균 |
|---|-----------|------|------------|------------|----------------|---------|
| 1 | 제품 출시 소식 | 이미지+텍스트 | 3,200 | 245 | 7.7% | +54% |
| 2 | 업계 팁 공유 | 텍스트 전용 | 1,800 | 89 | 4.9% | -2% |
| 3 | 고객 후기 | 캐러셀 | 2,500 | 198 | 7.9% | +58% |
| 4 | 프로모션 안내 | 링크 | 900 | 32 | 3.6% | -28% |

### 성과 좋은 패턴
- **이미지/캐러셀 포함** 포스트가 텍스트 전용 대비 engagement rate +60%
- **고객 후기/사회적 증거** 포함 시 engagement 최고
- **직접 링크 포스트**는 알고리즘 도달률 저하 (engagement rate 최저)

### 권장 포맷
→ 다음 포스트에 적용: 캐러셀 형태 + 고객 후기 포함 + CTA는 댓글 유도
```

### Step 3: 성과 기반 새 포스트 작성

분석된 패턴을 적용하여 새 포스트를 작성한다:
- 성과 좋은 포맷(이미지 유형, 텍스트 길이, 톤)을 참조
- `ad-copywriter`가 생성한 카피가 있으면 소셜 포맷에 맞게 조정
- 포스트 발행 전에 유저에게 내용 확인 요청

### Step 4: 실험으로 등록 (선택)

새로운 포맷이나 전략을 테스트하는 포스트라면, `ad-experiment-tracker`에 실험으로 등록할 것을 권장한다:

```
→ 이 포스트를 실험으로 등록하려면 `ad-experiment-tracker`를 사용하세요.
  예시: "캐러셀 vs 단일 이미지 engagement 비교 실험 시작"
  - Hypothesis: 캐러셀 포맷이 단일 이미지보다 engagement rate 20% 이상 높을 것
  - 측정 기간: 7일
  - 결과 확인: `meta-insights`로 성과 조회 후 `ad-experiment-tracker`로 결과 기록
```

## Notes

- **이미지 필수**: Instagram 게시물은 반드시 이미지 URL이 필요 (텍스트만 불가).
- **Instagram 이미지 URL**: 공개적으로 접근 가능한 HTTPS URL이어야 함.
- **Rate Limit**: 429 에러 시 대기 후 재시도. Facebook API는 시간당 200 calls/user.
- **Write 권한**: 유저가 Settings에서 Read+Write로 연결한 경우에만 포스팅/DM 답장 가능.
- **페이지 토큰**: Page Access Token은 만료되지 않음 (long-lived user token에서 파생).
- **DM 24시간 규칙**: Messenger/Instagram DM 답장은 마지막 메시지로부터 24시간 이내만 가능.
- **비교 데이터**: 성과 비교 시 항상 변화율(%)을 포함한다.
- **금액 표시**: 실제 통화 단위로 표시한다 (micros/cents 자동 변환).
