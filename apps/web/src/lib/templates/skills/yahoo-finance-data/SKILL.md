---
name: yahoo-finance-data
description: Use when needing quick stock quotes, basic company financials, options data, or market news without an API key. Also use when Alpha Vantage daily limit is exhausted.
---

# Yahoo Finance Data

Yahoo Finance 데이터를 web_fetch/web_search로 직접 접근한다. API key 불필요.

## When to Use

- 빠른 주가 확인 (API key 없이)
- 기본 재무 데이터 조회
- 시장 뉴스 검색
- Alpha Vantage 일일 한도 초과 시 대안
- 글로벌 주식 (한국 포함) 시세 조회

## 접근 방법

Yahoo Finance는 공식 REST API가 없으므로, 아래 방법으로 접근한다.

### 1. Yahoo Finance Chart API (비공식, 무료)

```
web_fetch "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d&range=1mo"
```

Parameters:
- `interval`: `1m`, `5m`, `15m`, `1h`, `1d`, `1wk`, `1mo`
- `range`: `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `5y`, `max`
- `period1` / `period2`: Unix timestamp (range 대신 사용)

Response: `chart.result[0]` → `meta` (시세 요약), `timestamp[]`, `indicators.quote[0]` (open, high, low, close, volume)

### 2. Yahoo Finance Quote API

```
web_fetch "https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL,MSFT,005930.KS"
```

여러 종목 동시 조회 가능. 한국 주식은 `.KS` (KOSPI) 또는 `.KQ` (KOSDAQ) suffix.

Response: `quoteResponse.result[]` → `regularMarketPrice`, `regularMarketChange`, `regularMarketChangePercent`, `marketCap`, `trailingPE`

### 3. Web Search 활용

```
web_search "AAPL stock price today"
web_search "삼성전자 주가"
web_search "site:finance.yahoo.com AAPL financials"
```

## 한국 주식 심볼

| 심볼 | 기업 | 시장 |
|------|------|------|
| `005930.KS` | 삼성전자 | KOSPI |
| `000660.KS` | SK하이닉스 | KOSPI |
| `035420.KS` | NAVER | KOSPI |
| `035720.KS` | 카카오 | KOSPI |
| `373220.KS` | LG에너지솔루션 | KOSPI |
| `247540.KQ` | 에코프로비엠 | KOSDAQ |

심볼 형식: 6자리 종목코드 + `.KS` (KOSPI) 또는 `.KQ` (KOSDAQ)

## Quick Reference

| 용도 | URL 패턴 |
|------|----------|
| 차트 데이터 | `query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d` |
| 실시간 시세 | `query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}` |
| 뉴스 검색 | `web_search "{symbol} stock news"` |
| 재무 데이터 | `web_search "site:finance.yahoo.com {symbol} financials"` |

## Workflow

1. **시세 확인**: Chart API 또는 Quote API로 현재가 조회
2. **히스토리**: Chart API에 `range`/`interval` 조합으로 과거 데이터
3. **한국 주식**: 종목코드 + `.KS`/`.KQ` suffix 사용
4. **상세 재무**: web_search로 Yahoo Finance 페이지 참조

## Red Flags

- Yahoo Finance API는 비공식 — 갑자기 변경/차단될 수 있음
- 한국 주식은 15분 지연 데이터
- 미국 주식은 실시간에 가까움 (약간의 지연)
- 대량/빈번한 요청 시 차단 가능 — 적절한 간격 유지
- 상세 재무제표가 필요하면 SEC EDGAR (미국) 또는 OpenDART (한국) 스킬 사용
