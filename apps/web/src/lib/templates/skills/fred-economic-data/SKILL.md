---
name: fred-economic-data
description: Use when looking up US economic indicators like GDP, unemployment, inflation, interest rates, or any macroeconomic time series data. Also use for economic trend analysis or FRED data queries.
---

# FRED Economic Data API

미 연준(Federal Reserve) FRED API로 80만+ 경제 시계열 데이터를 조회한다. GDP, 실업률, 인플레이션, 금리 등.

## When to Use

- 미국 GDP, 실업률, CPI 등 경제 지표 조회
- 금리 (연방기금금리, 국채 수익률) 추적
- 주가지수 (S&P 500) 히스토리
- 경제 시계열 검색 및 분석
- 국가 간 경제 지표 비교

## API Endpoint

**Base URL**: `https://api.stlouisfed.org/fred`

**인증**: `api_key` 파라미터 (fred.stlouisfed.org 가입 후 무료 발급)

### 1. 시계열 데이터 조회

```
web_fetch "https://api.stlouisfed.org/fred/series/observations?series_id=GDP&api_key=YOUR_KEY&file_type=json&observation_start=2023-01-01"
```

Parameters:
- `series_id`: 시계열 ID (필수)
- `observation_start` / `observation_end`: 기간 (YYYY-MM-DD)
- `frequency`: `d` (일), `w` (주), `m` (월), `q` (분기), `a` (연)
- `units`: `lin` (원값), `chg` (변동), `ch1` (전년비), `pch` (% 변동), `pc1` (전년비%)
- `file_type`: `json` or `xml`

Response: `observations[]` → `date`, `value`

### 2. 시계열 검색

```
web_fetch "https://api.stlouisfed.org/fred/series/search?search_text=consumer+price+index&api_key=YOUR_KEY&file_type=json"
```

Response: `seriess[]` → `id`, `title`, `frequency`, `units`, `observation_start`, `observation_end`

### 3. 시계열 정보

```
web_fetch "https://api.stlouisfed.org/fred/series?series_id=GDP&api_key=YOUR_KEY&file_type=json"
```

Response: `title`, `frequency`, `units`, `seasonal_adjustment`, `last_updated`

## 주요 시계열 ID

| Series ID | 지표 | 주기 |
|-----------|------|------|
| `GDP` | 미국 실질 GDP | 분기 |
| `UNRATE` | 실업률 | 월 |
| `CPIAUCSL` | 소비자물가지수 (CPI) | 월 |
| `FEDFUNDS` | 연방기금금리 | 월 |
| `DGS10` | 10년 국채 수익률 | 일 |
| `DGS2` | 2년 국채 수익률 | 일 |
| `T10Y2Y` | 10Y-2Y 스프레드 (역전 주의) | 일 |
| `SP500` | S&P 500 지수 | 일 |
| `DEXKOUS` | 원/달러 환율 | 일 |
| `DEXJPUS` | 엔/달러 환율 | 일 |
| `M2SL` | M2 통화량 | 월 |
| `HOUST` | 주택착공건수 | 월 |
| `UMCSENT` | 미시간 소비자심리지수 | 월 |
| `VIXCLS` | VIX 변동성 지수 | 일 |

## Workflow

1. **시계열 검색**: `/fred/series/search` 로 키워드 검색
2. **ID 확인**: 결과에서 `id` 추출 (또는 위 레퍼런스 테이블 참고)
3. **데이터 조회**: `/fred/series/observations` 로 시계열 데이터 가져오기
4. **단위 변환**: `units=pch` 등으로 변동률 직접 계산 가능

## Red Flags

- API key 필요 — fred.stlouisfed.org 무료 가입
- 일일 요청 제한 있음 (일반적으로 충분)
- `value`가 `"."` 인 경우 = 데이터 없음 (공휴일 등)
- `file_type=json` 명시 필요 (기본값은 XML)
- 한국 경제 데이터는 제한적 — `DEXKOUS` (환율) 정도만 있음
