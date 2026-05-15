---
name: fmp-financial-data
description: Use when looking up company fundamentals, financial statements, stock screener, DCF valuations, analyst estimates, or SEC filings. Also use for detailed company financial analysis beyond basic stock quotes.
---

# Financial Modeling Prep (FMP) API

FMP Stable API로 기업 재무제표, 밸류에이션, 애널리스트 추정치 등 상세 기업 분석 데이터를 조회한다.

## When to Use

- 기업 재무제표 (손익계산서, 대차대조표, 현금흐름표)
- 기업 밸류에이션 (DCF, 주요 지표, 재무비율)
- 애널리스트 추정치
- Alpha Vantage보다 상세한 기업 분석이 필요할 때

## API Access

플랫폼이 API key를 제공하므로 `integration.sh fmp/stable/...` 로 바로 사용 가능. 호출당 $0.001 (크레딧 차감).

응답 형식: `{ "data": <FMP JSON 응답>, "synced_at": "..." }`

**중요**: 모든 엔드포인트에서 종목은 `symbol` 쿼리 파라미터로 전달 (예: `?symbol=AAPL`).

### 1. 기업 프로필

```
integration.sh fmp/stable/profile?symbol=AAPL
```

Response data: `[{symbol, companyName, industry, sector, marketCap, price, beta, volAvg, description, ceo, country, ...}]`

### 2. 재무제표

```
integration.sh "fmp/stable/income-statement?symbol=AAPL&period=annual&limit=5"
integration.sh "fmp/stable/balance-sheet-statement?symbol=AAPL&period=annual&limit=5"
integration.sh "fmp/stable/cash-flow-statement?symbol=AAPL&period=annual&limit=5"
```

Parameters:
- `period`: `annual` 또는 `quarter`
- `limit`: 반환할 기간 수

### 3. 주요 지표 & 비율

```
integration.sh "fmp/stable/key-metrics?symbol=AAPL&period=annual&limit=5"
integration.sh "fmp/stable/ratios?symbol=AAPL&period=annual&limit=5"
```

key-metrics: `revenuePerShare`, `netIncomePerShare`, `peRatio`, `debtToEquity`, `roe`, `roic`, `freeCashFlowPerShare`, ...
ratios: `grossProfitMargin`, `operatingProfitMargin`, `netProfitMargin`, `currentRatio`, `quickRatio`, `debtEquityRatio`, ...

### 4. DCF (Discounted Cash Flow)

```
integration.sh fmp/stable/discounted-cash-flow?symbol=AAPL
```

Response data: `[{symbol, date, dcf, "Stock Price"}]`

### 5. 주식 시세

```
integration.sh fmp/stable/quote?symbol=AAPL
```

Response data: `[{symbol, name, price, change, changePercentage, volume, marketCap, pe, eps, ...}]`

### 6. 애널리스트 추정치

```
integration.sh "fmp/stable/analyst-estimates?symbol=AAPL&period=annual&limit=4"
```

Response data: `[{symbol, date, revenueLow/High/Avg, ebitdaLow/High/Avg, epsLow/High/Avg, ...}]`

### 7. 종목 검색

```
integration.sh "fmp/stable/search-name?query=apple&limit=10"
```

## Quick Reference

| 용도 | 명령어 |
|------|--------|
| 기업 프로필 | `integration.sh fmp/stable/profile?symbol={SYMBOL}` |
| 손익계산서 | `integration.sh "fmp/stable/income-statement?symbol={SYMBOL}&period=annual&limit=5"` |
| 대차대조표 | `integration.sh "fmp/stable/balance-sheet-statement?symbol={SYMBOL}&period=annual&limit=5"` |
| 현금흐름표 | `integration.sh "fmp/stable/cash-flow-statement?symbol={SYMBOL}&period=annual&limit=5"` |
| 주요 지표 | `integration.sh "fmp/stable/key-metrics?symbol={SYMBOL}&period=annual"` |
| 재무비율 | `integration.sh "fmp/stable/ratios?symbol={SYMBOL}&period=annual"` |
| DCF | `integration.sh fmp/stable/discounted-cash-flow?symbol={SYMBOL}` |
| 시세 조회 | `integration.sh fmp/stable/quote?symbol={SYMBOL}` |
| 종목 검색 | `integration.sh "fmp/stable/search-name?query=...&limit=10"` |

## Workflow

1. **종목 확인**: `integration.sh fmp/stable/search-name?query=apple`로 티커 검색
2. **기업 개요**: `integration.sh fmp/stable/profile?symbol=AAPL`로 기본 정보 확인
3. **재무 분석**: income-statement + balance-sheet + cash-flow 조합
4. **밸류에이션**: key-metrics + discounted-cash-flow
5. **전망 분석**: analyst-estimates로 컨센서스 확인

## Red Flags

- **중요**: `&`가 포함된 URL은 반드시 따옴표로 감싸야 함 (예: `integration.sh "fmp/stable/quote?symbol=AAPL&period=annual"`)
- 모든 응답은 `{ data: [...] }` 형태 — `data` 필드에서 실제 배열 추출
- `period=quarter`는 분기별, `period=annual`은 연간
- 한국 주식은 지원하지 않음 — 미국/글로벌 주식 위주 (한국은 DART 사용)
- `analyst-estimates`는 `period` 파라미터 필수 (annual 또는 quarter)
