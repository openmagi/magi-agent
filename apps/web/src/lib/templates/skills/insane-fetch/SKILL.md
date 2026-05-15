---
name: insane-fetch
description: Tough-site URL fetcher. Use when `jina-reader` returns empty/captcha/upstream_error, or for sites known to block simple requests (Coupang, LinkedIn, fmkorea, disc). Auto-escalates across 2 phases (TLS spoofing + identity warming). Returns escalate_to_browser=true when JS rendering is required.
metadata:
  author: openmagi
  version: "1.0"
---

# Insane Fetch

완강한 사이트를 뚫는 페처. `jina-reader`로 안 되면 이걸 쓴다.

## When to Use

- `jina-reader`가 `error: empty | captcha | upstream_error` 반환
- 사이트가 WAF/TLS 지문 차단으로 유명: Coupang, LinkedIn, fmkorea, 디시, 요즘IT
- 로그인 없이 공개된 콘텐츠/메타데이터만 필요할 때

## When NOT to Use (위 또는 옆으로)

- 단순 뉴스/블로그/PDF → `jina-reader` 먼저
- 로그인 / 폼 입력 / JS 인터랙션 → `browser`
- 여러 페이지 크롤 → `firecrawl`

## Usage

```bash
$BIN_DIR/insane-fetch.sh "https://www.coupang.com/np/search?q=키보드"
```

성공:
```json
{
  "success": true,
  "phase": 2,              // 1 = Phase 1 light probes, 2 = TLS impersonation
  "status": 200,
  "target": "safari",      // which TLS target won (Phase 2)
  "url_final": "https://...",
  "content": {
    "html": "<!doctype html>...",
    "jsonld": [ { "@type": "Product", "name": "...", "offers": { "price": 29900 } } ],
    "ogp":   { "title": "...", "description": "..." }
  }
}
```

실패 (세 가지 분기):

```jsonc
// (a) JS 렌더링 필요 — 이 경우 browser 스킬로 넘어갈 것
{ "success": false, "phase": 1, "failure_reason": "js_required", "escalate_to_browser": true, ... }

// (b) 인증 필요 — 유저에게 알리고 종료. 더 올려도 안 뚫림
{ "success": false, "failure_reason": "auth_required", ... }

// (c) rate_limited / worker_unavailable / empty — 재시도 or 포기
{ "success": false, "failure_reason": "rate_limited", ... }
```

## Decision Flow (이전 단계 포함)

```
 유저가 URL 줌
   └─ jina.sh $URL
        ├─ success                → 끝
        ├─ paywall                → "유료 콘텐츠입니다" 안내
        ├─ empty / captcha / upstream_error ↓
   └─ insane-fetch.sh $URL
        ├─ success                → content.html 또는 content.jsonld 사용
        ├─ escalate_to_browser    → browser skill 로 open → scrape
        ├─ auth_required          → "로그인이 필요한 사이트입니다" 안내
        ├─ rate_limited           → 2초 대기 후 1회 재시도
        └─ worker_unavailable     → firecrawl fallback
```

## 콘텐츠 우선순위

응답에서 꺼내 쓸 필드는 이 순서:
1. **`content.jsonld`** — 상품 (Product/Offer), 기사 (NewsArticle), 프로필 (Person) 등 구조화 데이터. 본문 못 가져와도 여기서 핵심 정보 확보 가능
2. **`content.html`** — 원본 HTML (최대 1.5MB). 필요 시 후처리로 텍스트 추출
3. **`content.ogp`** — 제목/설명/대표이미지 (부분 성공에도 대개 있음)

## Cost

- Phase 1 성공: 0.1¢
- Phase 2 성공 (TLS impersonation 사용): 0.5¢
- 실패 과금 없음 (rate_limit / worker_unavailable / escalate_to_browser 모두 무료)

## Limits

- 봇당 30 req/min, 글로벌 120 req/min
- 한 URL 최대 크기 1.5MB 본문
- 타임아웃 45초 (Phase 2까지 포함)

## Red Flags

- **유저에게 "API 키가 필요합니다" 안내하지 마라** — 플랫폼 관리
- **단순 뉴스 URL에 바로 쓰지 마라** — `jina-reader` 부터 시도 (10배 싸다)
- **`escalate_to_browser: true`에서 멈추지 마라** — `browser` 스킬로 이어가야 의미 있음
