---
name: jina-reader
description: Fast markdown extraction of most public URLs (news, blogs, articles, PDFs). Platform-managed — no API key. First choice before `browser` or `firecrawl` when you just need readable content.
metadata:
  author: openmagi
  version: "1.0"
---

# Jina Reader

Jina Reader(`r.jina.ai`)를 통한 빠른 마크다운 추출. 대부분의 뉴스/블로그/아티클/PDF가 한 방에 변환된다.

## When to Use

- URL이 있고 **본문/아티클 내용**만 필요할 때 — 이 스킬을 **가장 먼저** 시도
- 네이버 뉴스/증권, Medium, Substack, 브런치, 한경, 대부분 한국 커뮤니티, PDF
- 구조화된 메타데이터도 함께 필요하면 `--json` 모드 (제목/설명/RSS URL 자동 발견)

## When NOT to Use (올라갈 것)

- 로그인/폼 입력 필요 → `browser`
- 여러 페이지 크롤/사이트맵 → `firecrawl`
- WAF 차단(쿠팡/링크드인/fmkorea) 또는 본 스킬이 `captcha`/`empty` 반환 → `insane-fetch` (*P1, 준비 중*)

## Usage

```bash
# 기본 — 마크다운 반환
$BIN_DIR/jina.sh "https://n.news.naver.com/article/001/0012345"
```

성공 응답:
```json
{ "success": true, "mode": "markdown", "data": "# 기사제목\n\n본문...", "bytes": 4820 }
```

실패 응답 — `error` 필드로 분기:
```json
{ "success": false, "error": "empty",    "detail": "..." }   // 빈 응답 → insane-fetch
{ "success": false, "error": "captcha",  "detail": "..." }   // 챌린지 → insane-fetch/browser
{ "success": false, "error": "paywall",  "detail": "..." }   // 유료 → 유저에게 안내
{ "success": false, "error": "rate_limited" }                // 잠시 후 재시도
{ "success": false, "error": "upstream_error", "detail": "..." }
```

## Decision Flow (봇이 따라야 할 체인)

```
jina.sh $URL
  └─ success    → 끝. data 사용.
  └─ empty      → insane-fetch.sh $URL  (P1 준비되면)
  └─ captcha    → insane-fetch.sh $URL  (P1 준비되면) → 실패하면 browser
  └─ paywall    → "유료 콘텐츠입니다" 유저 안내
  └─ rate_limited → 1-2초 후 재시도 (1회만)
  └─ upstream_error → browser skill 또는 firecrawl
```

## Advanced Modes

```bash
# JSON 모드 (metadata + external.alternate=RSS URL 자동 발견)
$BIN_DIR/jina.sh "$URL" --json

# SPA 모드 (JS 렌더링 완료까지 대기)
$BIN_DIR/jina.sh "$URL" --spa

# 본문 영역만 선택자로 타겟팅 (네비/풋터 제거)
$BIN_DIR/jina.sh "$URL" --selector=".article-body"

# 캐시 우회 (실시간 주가 같이 신선한 데이터 필요 시)
$BIN_DIR/jina.sh "$URL" --no-cache
```

## Cost

- 성공 호출 0.02¢. 실패는 과금 없음.
- 플랫폼 rate-limit: 봇당 60 req/min, 글로벌 400 req/min.

## Red Flags

- **API 키가 필요하다고 유저에게 안내하지 마라** — 플랫폼 관리 스킬이다
- `success:false`가 나와도 **바로 결과를 거부하지 마라** — 위 Decision Flow대로 다음 스텝으로
- `--no-cache`는 꼭 필요할 때만 (실시간성) — 일반 조회는 기본 캐시(5분) 사용
