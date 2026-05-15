---
name: firecrawl
description: Use when scraping, crawling, or extracting content from web pages. Also use for site mapping, discovering URLs on a domain, or converting web pages to clean markdown. Platform-managed — works out of the box, no API key setup required.
---

# Firecrawl Web Scraping & Crawling

Firecrawl API로 웹 페이지를 스크래핑하고, 사이트를 크롤링하고, URL을 매핑한다.

## Platform-provided (no BYO key)

이 스킬은 **플랫폼 프록시 경유** (`$API_PROXY_URL/v1/firecrawl`, chat-proxy → firecrawl gateway)로 동작한다. 봇은 키를 관리하지 않고, 플랫폼이 자동 주입한다.

- 바로 사용 가능 — Settings에 Firecrawl API key를 등록할 필요 **없음**
- 사용량은 플랫폼 크레딧에서 자동 차감
- `$BIN_DIR/firecrawl.sh`는 `$GATEWAY_TOKEN`으로 플랫폼 프록시를 호출하고, 하드코딩된 `api.firecrawl.dev`를 직접 치지 않는다
- 사용자가 자체 `FIRECRAWL_API_KEY`를 넣어 둔 경우에만 직통 경로로 전환 (fallback)

**중요:** 사용자에게 "API 키가 필요합니다"라고 안내하지 마라 — 플랫폼이 기본 제공한다.

## Quick Start — firecrawl.sh

`$BIN_DIR/firecrawl.sh` 스크립트로 간편하게 호출:

```bash
# 단일 페이지 스크래핑 (markdown 반환)
$BIN_DIR/firecrawl.sh scrape "https://example.com"

# 사이트 크롤링 (여러 페이지)
$BIN_DIR/firecrawl.sh crawl "https://example.com"

# URL 매핑 (사이트의 모든 URL 발견)
$BIN_DIR/firecrawl.sh map "https://example.com"
```

## API Reference

**Base URL**: `$API_PROXY_URL/v1/firecrawl` (플랫폼 프록시 — 기본 경로)
**인증**: 자동 — `firecrawl.sh`가 `$GATEWAY_TOKEN`으로 플랫폼 프록시를 호출. 직접 `https://api.firecrawl.dev`를 치지 마라.

### 1. Scrape — 단일 페이지

```bash
$BIN_DIR/firecrawl.sh scrape "https://example.com"
```

**Response**:
```json
{
  "success": true,
  "data": {
    "markdown": "# Page Title\n\nPage content...",
    "metadata": {
      "title": "Example",
      "description": "...",
      "sourceURL": "https://example.com"
    }
  }
}
```

### 2. Crawl — 사이트 크롤링

```bash
# 기본 10페이지
$BIN_DIR/firecrawl.sh crawl "https://example.com"

# 최대 50페이지
$BIN_DIR/firecrawl.sh crawl "https://example.com" 50
```

**Response**: `{"success": true, "id": "crawl-id-here"}`

크롤은 비동기 — 상태 확인이 필요하면 curl로 직접:
```bash
curl -s "$API_PROXY_URL/v1/firecrawl/crawl/CRAWL_ID" \
  -H "Authorization: Bearer $GATEWAY_TOKEN"
```

### 3. Map — URL 발견

```bash
$BIN_DIR/firecrawl.sh map "https://example.com"
```

**Response**: `{"success": true, "links": ["https://example.com/page1", ...]}`

## Workflow

1. **단일 페이지 내용 필요** → `scrape` 사용
2. **사이트 구조 파악** → `map`으로 URL 목록 확인 → 필요한 페이지만 `scrape`
3. **여러 페이지 수집** → `crawl` 사용 (limit 설정 권장)

## Best Practices

- `scrape`는 동기 호출 — 즉시 결과 반환
- `crawl`은 비동기 — ID로 상태 폴링 필요
- 크롤 시 limit 설정으로 과도한 요청 방지
- 큰 사이트는 `map` → 선별 `scrape`가 효율적
- JavaScript 렌더링 페이지도 자동 처리

## Red Flags

- 플랫폼 크레딧 부족 시 요청 실패 가능 — 사용자에게 크레딧 충전 안내
- Rate limit 초과 시 429 에러 → 잠시 후 재시도
- robots.txt 존중 — 차단된 페이지는 스크래핑 불가
