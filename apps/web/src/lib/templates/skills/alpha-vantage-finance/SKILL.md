---
name: alpha-vantage-finance
description: Use when looking up stock prices, company financials, forex rates, crypto prices, technical indicators, or US economic data. Also use for financial market research or portfolio analysis.
---

# Alpha Vantage Finance API

Alpha Vantage API로 주가, 재무제표, 환율, 암호화폐, 기술지표, 경제 데이터를 조회한다. 무료 key 발급 가능.

## When to Use

- 주식 시세 조회 (실시간/일별/주간/월간)
- 기업 재무제표 (매출, 영업이익, 대차대조표 등)
- 외환 환율 조회
- 암호화폐 시세
- 기술적 분석 (RSI, MACD, SMA 등)
- 미국 경제 지표 (GDP, 인플레이션 등)

## API Endpoint

**Base URL**: `https://www.alphavantage.co/query`

**인증**: `apikey` 쿼리 파라미터 (alphavantage.co에서 무료 발급, 일 25회)

### 1. 주식 시세

**실시간 시세:**
```
web_fetch "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=AAPL&apikey=YOUR_KEY"
```

Response: `Global Quote` → `05. price`, `08. previous close`, `09. change`, `10. change percent`

**일별 시세:**
```
web_fetch "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=AAPL&apikey=YOUR_KEY"
```

Response: `Time Series (Daily)` → 날짜별 `1. open`, `2. high`, `3. low`, `4. close`, `5. volume`

### 2. 기업 재무

**기업 개요:**
```
web_fetch "https://www.alphavantage.co/query?function=OVERVIEW&symbol=AAPL&apikey=YOUR_KEY"
```

Response: `MarketCapitalization`, `PERatio`, `DividendYield`, `EPS`, `52WeekHigh`, `52WeekLow`, `Sector`, `Industry`

**손익계산서:**
```
web_fetch "https://www.alphavantage.co/query?function=INCOME_STATEMENT&symbol=AAPL&apikey=YOUR_KEY"
```

**대차대조표 / 현금흐름:**
```
function=BALANCE_SHEET&symbol=AAPL
function=CASH_FLOW&symbol=AAPL
```

Response: `annualReports[]` + `quarterlyReports[]` 배열

### 3. 외환

```
web_fetch "https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=KRW&apikey=YOUR_KEY"
```

Response: `Realtime Currency Exchange Rate` → `5. Exchange Rate`

### 4. 암호화폐

```
web_fetch "https://www.alphavantage.co/query?function=DIGITAL_CURRENCY_DAILY&symbol=BTC&market=USD&apikey=YOUR_KEY"
```

### 5. 기술지표

```
web_fetch "https://www.alphavantage.co/query?function=RSI&symbol=AAPL&interval=daily&time_period=14&series_type=close&apikey=YOUR_KEY"
```

주요 지표: `SMA`, `EMA`, `RSI`, `MACD`, `BBANDS` (볼린저밴드), `STOCH`, `ADX`, `CCI`, `AROON`, `VWAP`

### 6. 경제 지표

```
web_fetch "https://www.alphavantage.co/query?function=REAL_GDP&interval=quarterly&apikey=YOUR_KEY"
```

지표: `REAL_GDP`, `CPI`, `INFLATION`, `FEDERAL_FUNDS_RATE`, `UNEMPLOYMENT`, `TREASURY_YIELD`

## Quick Reference

| function | 설명 | 주요 파라미터 |
|----------|------|--------------|
| `GLOBAL_QUOTE` | 실시간 시세 | `symbol` |
| `TIME_SERIES_DAILY` | 일별 시세 | `symbol`, `outputsize` |
| `OVERVIEW` | 기업 개요 | `symbol` |
| `INCOME_STATEMENT` | 손익계산서 | `symbol` |
| `BALANCE_SHEET` | 대차대조표 | `symbol` |
| `CASH_FLOW` | 현금흐름표 | `symbol` |
| `CURRENCY_EXCHANGE_RATE` | 환율 | `from_currency`, `to_currency` |
| `DIGITAL_CURRENCY_DAILY` | 크립토 일별 | `symbol`, `market` |
| `RSI` / `MACD` / `SMA` | 기술지표 | `symbol`, `interval`, `time_period` |
| `REAL_GDP` / `CPI` | 경제지표 | `interval` |

## Red Flags

- 무료 key: 일 25회, 분 5회 제한 — 초과 시 `Note` 필드에 에러 메시지
- `outputsize=compact` (기본 100일) vs `full` (20년+)
- 주식 심볼은 미국 시장 기준 (AAPL, MSFT, GOOGL)
- 한국 주식은 지원하지 않음 — OpenDART 스킬 사용
- 재무데이터는 미국 상장사만 제공
