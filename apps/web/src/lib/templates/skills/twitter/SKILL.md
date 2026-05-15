---
name: twitter
description: Use when the user asks to post tweets, check Twitter mentions, view tweet metrics, search tweets, or manage the Twitter/X account.
metadata:
  author: openmagi
  version: "3.0"
---

# Twitter / X Integration

X(Twitter) API v2를 통해 트윗 포스팅, 타임라인 조회, 멘션 확인, 검색 등을 수행한다.

## Setup

OAuth 연동으로 자동 인증됨. 유저가 Settings > Integrations에서 X 계정을 연결하면, 봇이 자동으로 API 접근 권한을 받는다. 토큰은 chat-proxy가 자동 관리하며 만료 시 자동 갱신됨.

**수동 키 설정 불필요.**

## Commands

### 트윗 포스팅

```bash
integration.sh twitter/tweet POST '{"text":"트윗 내용"}'
```

답글 달기:
```bash
integration.sh twitter/tweet POST '{"text":"답글 내용","reply_to":"원본_트윗_ID"}'
```

**응답:**
```json
{
  "data": { "id": "123456789", "text": "트윗 내용" }
}
```

### 내 최근 트윗 조회

```bash
integration.sh twitter/timeline           # 최근 10개
integration.sh twitter/timeline?count=20  # 최근 20개
```

**응답:**
```json
{
  "data": [
    {
      "id": "123",
      "text": "트윗 내용",
      "created_at": "2026-03-09T...",
      "public_metrics": {
        "retweet_count": 5,
        "reply_count": 2,
        "like_count": 15,
        "impression_count": 1200
      }
    }
  ]
}
```

### 멘션 조회

```bash
integration.sh twitter/mentions           # 최근 10개
integration.sh twitter/mentions?count=20  # 최근 20개
```

### 계정 지표 조회

```bash
integration.sh twitter/metrics
```

**응답:**
```json
{
  "data": {
    "id": "...",
    "name": "Open Magi",
    "username": "clawy_ai",
    "public_metrics": {
      "followers_count": 150,
      "following_count": 30,
      "tweet_count": 45,
      "listed_count": 2
    }
  }
}
```

### 트윗 검색

```bash
integration.sh "twitter/search?q=AI%20agent&count=10"
```

### 트윗 삭제

```bash
integration.sh twitter/delete POST '{"tweet_id":"123456789"}'
```

## Notes

- **280자 제한**: 트윗은 280자를 넘길 수 없다 (API가 자동 검증).
- **비팔로워 답글 제한**: 나를 팔로우하지 않는 유저에게는 API로 답글을 달 수 없다.
- **Rate Limit**: 429 에러 시 15분 대기 후 재시도.
- **이미지 미지원**: 텍스트 트윗만 가능.
- **API 한도**: Twitter API v2 Basic tier 기준 월 10,000 read + 1,667 트윗.
- **검색 범위**: 최근 7일 이내 트윗만 검색 가능.
- **토큰 만료**: Access token은 2시간마다 만료되며, refresh token으로 자동 갱신됨.
- **Write 권한**: 유저가 Settings에서 Read+Write로 연결한 경우에만 트윗 포스팅/삭제 가능.
