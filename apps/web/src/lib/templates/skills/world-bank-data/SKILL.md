---
name: world-bank-data
description: Use when looking up global development indicators, country economic statistics, GDP comparisons across nations, or World Bank development data. Also use for international economic research.
---

# World Bank Data API

World Bank Open Data API로 200+ 국가의 경제/사회 지표를 조회한다. API key 불필요, 완전 무료.

## When to Use

- 국가별 GDP, 인구, GNI 등 경제 지표 비교
- 글로벌 개발 지표 (빈곤율, 문해율, 기대수명 등)
- 국가 간 시계열 비교 분석
- 신흥시장/개발도상국 경제 데이터

## API Endpoint

**Base URL**: `https://api.worldbank.org/v2`

**인증**: 불필요 (공개 API)

### 1. 국가별 지표 조회

```
web_fetch "https://api.worldbank.org/v2/country/KR/indicator/NY.GDP.MKTP.CD?date=2018:2023&format=json"
```

Parameters:
- `country`: ISO 3166-1 alpha-2 (KR, US, JP, CN) 또는 `all`
- `indicator`: 지표 코드 (필수)
- `date`: 연도 범위 (`2020:2024`) 또는 단일 연도
- `format`: `json` (필수, 기본은 XML)
- `per_page`: 결과 수 (기본 50)
- `page`: 페이지 번호

Response: `[{page info}, [{country, indicator, value, date}, ...]]`

### 2. 여러 국가 비교

```
web_fetch "https://api.worldbank.org/v2/country/KR;US;JP;CN/indicator/NY.GDP.MKTP.CD?date=2023&format=json"
```

국가 코드를 `;`로 구분하여 여러 국가 동시 조회.

### 3. 지표 검색

```
web_fetch "https://api.worldbank.org/v2/indicator?format=json&per_page=50"
```

특정 키워드 검색:
```
web_search "site:data.worldbank.org indicator GDP per capita"
```

### 4. 국가 정보

```
web_fetch "https://api.worldbank.org/v2/country/KR?format=json"
```

Response: `name`, `region`, `incomeLevel`, `capitalCity`, `longitude`, `latitude`

## 주요 지표 코드

| Indicator Code | 지표 | 단위 |
|---------------|------|------|
| `NY.GDP.MKTP.CD` | GDP (경상 달러) | USD |
| `NY.GDP.MKTP.KD.ZG` | GDP 성장률 | % |
| `NY.GDP.PCAP.CD` | 1인당 GDP | USD |
| `NY.GNP.PCAP.CD` | 1인당 GNI | USD |
| `SP.POP.TOTL` | 총 인구 | 명 |
| `SP.DYN.LE00.IN` | 기대수명 | 년 |
| `SL.UEM.TOTL.ZS` | 실업률 | % |
| `FP.CPI.TOTL.ZG` | 인플레이션 (CPI) | % |
| `NE.EXP.GNFS.ZS` | 수출/GDP 비율 | % |
| `BX.KLT.DINV.CD.WD` | FDI 유입 | USD |
| `SE.ADT.LITR.ZS` | 성인 문해율 | % |
| `SI.POV.DDAY` | 빈곤율 ($2.15/일 기준) | % |
| `EG.USE.PCAP.KG.OE` | 1인당 에너지 사용량 | kg 석유환산 |

## 주요 국가 코드

| Code | 국가 | Code | 국가 |
|------|------|------|------|
| `KR` | 한국 | `US` | 미국 |
| `JP` | 일본 | `CN` | 중국 |
| `DE` | 독일 | `GB` | 영국 |
| `IN` | 인도 | `BR` | 브라질 |
| `VN` | 베트남 | `ID` | 인도네시아 |

지역: `EAS` (동아시아), `NAC` (북미), `ECS` (유럽), `SSF` (사하라 이남 아프리카)

## Workflow

1. **지표 확인**: 위 레퍼런스 테이블 또는 `/v2/indicator` 검색
2. **데이터 조회**: `/v2/country/{code}/indicator/{code}?date=YYYY:YYYY&format=json`
3. **비교 분석**: 여러 국가 코드를 `;`로 구분하여 동시 조회
4. **시각화**: 시계열 데이터를 차트로 표현

## Red Flags

- `format=json` 필수 — 기본은 XML
- 최신 데이터는 1-2년 지연될 수 있음 (국가별 보고 시점 차이)
- `value`가 `null`인 경우 = 해당 연도 데이터 없음
- 일부 지표는 특정 국가에만 있음 (검색 후 확인)
- 대량 조회 시 `per_page=1000` + 페이지네이션 필요
