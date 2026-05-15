---
name: finnhub-market-data
description: Use when looking up real-time stock quotes, company news, analyst recommendations, price targets, earnings calendars, or peer companies. Also use for market sentiment and financial news monitoring.
---

# Finnhub Market Data API

Finnhub API로 실시간 시세, 기업 뉴스, 애널리스트 추천, 실적 캘린더 등 시장 데이터를 조회한다.

## When to Use

- 실시간 주가 조회 (미국 주식)
- 기업 뉴스 및 시장 뉴스 모니터링
- 애널리스트 추천/목표가 조회
- 실적 캘린더 확인
- 동종 기업(peer) 비교
- 기업 기본 재무 지표 (metric)

## API Endpoint

**Base URL**: `https://finnhub.io/api/v1`

**인증**: `token` 쿼리 파라미터 또는 `X-Finnhub-Token` 헤더

### 1. 실시간 시세

```
web_fetch "https://finnhub.io/api/v1/quote?symbol=AAPL&token=YOUR_KEY"
```

Response: `{c: 현재가, d: 변동, dp: 변동%, h: 고가, l: 저가, o: 시가, pc: 전일종가, t: timestamp}`

### 2. 기업 프로필

```
web_fetch "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL&token=YOUR_KEY"
```

Response: `{country, currency, exchange, finnhubIndustry, ipo, logo, marketCapitalization, name, ticker, weburl, ...}`

### 3. 기본 재무 지표

```
web_fetch "https://finnhub.io/api/v1/stock/metric?symbol=AAPL&metric=all&token=YOUR_KEY"
```

Response: `{metric: {52WeekHigh, 52WeekLow, peBasicExclExtraTTM, roeTTM, currentRatioQuarterly, ...}, series: {...}}`

### 4. 애널리스트 추천

```
web_fetch "https://finnhub.io/api/v1/stock/recommendation?symbol=AAPL&token=YOUR_KEY"
```

Response: `[{buy, hold, sell, strongBuy, strongSell, period, symbol}]`

### 5. 목표 주가

```
web_fetch "https://finnhub.io/api/v1/stock/price-target?symbol=AAPL&token=YOUR_KEY"
```

Response: `{lastUpdated, symbol, targetHigh, targetLow, targetMean, targetMedian}`

### 6. 실적 캘린더

```
web_fetch "https://finnhub.io/api/v1/calendar/earnings?from=2024-01-01&to=2024-03-31&token=YOUR_KEY"
```

Response: `{earningsCalendar: [{date, epsActual, epsEstimate, revenueActual, revenueEstimate, symbol, ...}]}`

### 7. 기업 뉴스

```
web_fetch "https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2024-01-01&to=2024-01-31&token=YOUR_KEY"
```

Response: `[{category, datetime, headline, id, image, source, summary, url}]`

### 8. 시장 뉴스

```
web_fetch "https://finnhub.io/api/v1/news?category=general&token=YOUR_KEY"
```

`category`: `general`, `forex`, `crypto`, `merger`

### 9. 동종 기업 (Peers)

```
web_fetch "https://finnhub.io/api/v1/stock/peers?symbol=AAPL&token=YOUR_KEY"
```

Response: `["MSFT", "GOOGL", "AMZN", ...]` (티커 배열)

### 10. 종목 검색

```
web_fetch "https://finnhub.io/api/v1/search?q=apple&token=YOUR_KEY"
```

Response: `{count, result: [{description, displaySymbol, symbol, type}]}`

## Quick Reference

| 용도 | Endpoint | 주요 파라미터 |
|------|----------|---------------|
| 실시간 시세 | `/quote` | `symbol` |
| 기업 프로필 | `/stock/profile2` | `symbol` |
| 재무 지표 | `/stock/metric` | `symbol`, `metric=all` |
| 추천 | `/stock/recommendation` | `symbol` |
| 목표가 | `/stock/price-target` | `symbol` |
| 실적 캘린더 | `/calendar/earnings` | `from`, `to` |
| 기업 뉴스 | `/company-news` | `symbol`, `from`, `to` |
| 시장 뉴스 | `/news` | `category` |
| 동종 기업 | `/stock/peers` | `symbol` |
| 종목 검색 | `/search` | `q` |

## Workflow

1. **종목 검색**: `/search`로 티커 확인
2. **시세 확인**: `/quote`로 현재가 조회
3. **기업 분석**: `/stock/profile2` + `/stock/metric`
4. **시장 심리**: `/stock/recommendation` + `/stock/price-target`
5. **뉴스 모니터링**: `/company-news`로 최근 뉴스 확인

## Red Flags

- 무료 tier: 60 calls/min — 초과 시 429 에러
- `/quote`는 존재하지 않는 심볼에 0을 반환 — 에러가 아님
- `/stock/profile2`는 잘못된 심볼에 빈 객체 `{}` 반환
- `marketCapitalization`은 백만 달러 단위 (millions)
- 한국 주식 제한적 — 미국 주식 중심
- 뉴스 날짜 범위는 `from`/`to` 필수 (YYYY-MM-DD)
