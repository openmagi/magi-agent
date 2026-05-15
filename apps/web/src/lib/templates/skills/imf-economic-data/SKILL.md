---
name: imf-economic-data
description: Use when looking up global macroeconomic data from the IMF, including GDP growth, inflation rates, government debt, unemployment, or current account balances across countries. Also use for cross-country economic comparisons or emerging market analysis.
---

# IMF DataMapper API

IMF DataMapper API로 200+ 국가의 GDP, 인플레이션, 실업률, 정부부채 등 거시경제 지표를 조회한다. API key 불필요.

## When to Use

- 국가별 GDP 성장률, 1인당 GDP 비교
- 인플레이션율, 실업률 국제 비교
- 정부부채/GDP 비율 추적
- 경상수지 분석
- IMF 전망치 (향후 5년 예측 포함)
- World Bank보다 거시경제 전망에 특화

## API Endpoint

**Base URL**: `https://www.imf.org/external/datamapper/api/v1`

**인증**: 불필요 (공개 API)

### 1. 지표별 국가 데이터 조회

```
web_fetch "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/USA?periods=2023,2024,2025"
```

Response:
```json
{
  "values": {
    "NGDP_RPCH": {
      "USA": { "2023": 2.9, "2024": 2.8, "2025": 2.0 }
    }
  }
}
```

### 2. 여러 국가 비교

```
web_fetch "https://www.imf.org/external/datamapper/api/v1/PCPIPCH/USA/CHN/DEU?periods=2023,2024,2025"
```

국가 코드를 `/`로 구분. Response: `values.{indicator}.{country}.{year}` 구조.

### 3. 전체 히스토리 (기간 미지정)

```
web_fetch "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/USA"
```

`periods` 생략 시 1980~2030 전체 데이터 반환 (IMF 전망치 포함).

### 4. 지표 목록

```
web_fetch "https://www.imf.org/external/datamapper/api/v1/indicators"
```

### 5. 국가 목록

```
web_fetch "https://www.imf.org/external/datamapper/api/v1/countries"
```

## 주요 지표 코드

| Code | 지표 | 단위 |
|------|------|------|
| `NGDP_RPCH` | 실질 GDP 성장률 | % |
| `NGDPD` | GDP (경상 달러) | 10억 USD |
| `NGDPDPC` | 1인당 GDP (경상 달러) | USD |
| `PPPGDP` | GDP (PPP 기준) | 10억 국제$ |
| `PCPIPCH` | 인플레이션율 (CPI 평균) | % |
| `PCPIEPCH` | 인플레이션율 (CPI 기말) | % |
| `LUR` | 실업률 | % |
| `GGXWDG_NGDP` | 정부 총부채/GDP | % |
| `GGXCNL_NGDP` | 재정수지/GDP | % |
| `BCA_NGDPD` | 경상수지/GDP | % |
| `LP` | 인구 | 백만 명 |

## 주요 국가 코드 (ISO alpha-3)

| Code | 국가 | Code | 국가 |
|------|------|------|------|
| `KOR` | 한국 | `USA` | 미국 |
| `JPN` | 일본 | `CHN` | 중국 |
| `DEU` | 독일 | `GBR` | 영국 |
| `IND` | 인도 | `BRA` | 브라질 |
| `FRA` | 프랑스 | `CAN` | 캐나다 |

## Quick Reference

| 용도 | URL 패턴 |
|------|----------|
| 단일 지표/국가 | `/api/v1/{indicator}/{country}?periods=YYYY,YYYY` |
| 다국가 비교 | `/api/v1/{indicator}/{c1}/{c2}/{c3}?periods=YYYY` |
| 전체 히스토리 | `/api/v1/{indicator}/{country}` (periods 생략) |
| 지표 검색 | `/api/v1/indicators` |
| 국가 검색 | `/api/v1/countries` |

## Workflow

1. **지표 확인**: 위 레퍼런스 테이블 또는 `/indicators` 조회
2. **데이터 조회**: `/{indicator}/{country}?periods=YYYY,YYYY`
3. **비교 분석**: 여러 국가 코드를 `/`로 구분하여 동시 조회
4. **전망 확인**: `periods` 생략으로 미래 전망치까지 포함

## Red Flags

- 국가 코드는 ISO alpha-3 (3글자) — `KOR` (O), `KR` (X)
- World Bank는 alpha-2, IMF는 alpha-3 — 혼동 주의
- 부동소수점 표기 (`2.899...` → 소수점 1-2자리로 반올림 필요)
- IMF 전망치는 World Economic Outlook 기준 — 실제와 차이 가능
- SDMX API (`dataservices.imf.org`)는 느리고 타임아웃 빈번 — DataMapper 사용 권장
- `values`가 비어있으면 해당 지표/국가 조합 데이터 없음
