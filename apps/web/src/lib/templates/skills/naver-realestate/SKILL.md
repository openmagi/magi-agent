---
name: naver-realestate
description: Korean real estate market data via Naver 부동산. Use for apartment complex listings, 호가(매매/전세/월세) 분포 분석, 저가 매물 필터링, "다주택 급매" 태그 검색. Korea only.
metadata:
  author: openmagi
  version: "1.0"
---

# Naver 부동산 호가 분석

네이버 부동산 단지/매물 API를 래핑한 플랫폼 서비스. 호가 분포, 저가 매물, 밀집 구간, 태그 통계를 한 번에 뽑는다.

## When to Use

- "양천구 목동 아파트 호가" — 지역 단위 단지 리스트
- "래미안 원베일리 25평 매매 호가" — 특정 단지 매물 분석
- "급매 찾아줘", "다주택 급매" — 태그 필터 + 저가 매물
- 가격 분포(최저/최고/중위값/평균/밀집구간) 필요할 때

## When NOT to Use

- 한국 외 부동산 → 지원 안 함
- 실거래가(국토부 RTMS) — 이건 별도 스킬 (추후)
- 매물 직접 게시/연락 — 이 스킬은 **조회 전용**

## Usage

### 1) 단지 리스트 (동 단위)

네이버 cortarNo(법정동 10자리)로 조회. 동네 이름 → cortarNo 변환은 유저가 주거나 별도 스킬로 해결.

```bash
$BIN_DIR/naver-re.sh region 1147010400   # 양천구 목동 예시
```

응답:
```json
{
  "success": true,
  "cached": false,
  "complexes": [
    { "complexNo": "652", "complexName": "목동신시가지1단지", "totalHouseholdCount": 1882, ... }
  ]
}
```

### 2) 단지명 검색

```bash
$BIN_DIR/naver-re.sh search "래미안 원베일리"
```

응답 `candidates[]`에서 complexNo 추출.

### 3) 단지 매물 분석 (핵심)

```bash
# 매매 기본 (5페이지 수집)
$BIN_DIR/naver-re.sh articles 652

# 25평대 매매 전용
$BIN_DIR/naver-re.sh articles 652 --trade A1 --area-min 80 --area-max 90

# 다주택 급매만
$BIN_DIR/naver-re.sh articles 652 --tags 다주택급매

# 40억 이하 매물 10페이지까지
$BIN_DIR/naver-re.sh articles 652 --price-max 400000 --pages 10
```

응답 포맷 (핵심 필드):
```json
{
  "success": true,
  "cached": false,
  "summary": {
    "count": 101,
    "price_man": { "min": 40900, "max": 50000, "median": 45500, "mean": 45500, "p25": 44000, "p75": 47000 },
    "density_bands": [
      { "range": "4억대", "count": 27 },
      { "range": "4억대", "count": 23 }
    ],
    "tag_breakdown": [
      { "tag": "다주택급매", "count": 12 },
      { "tag": "로열층", "count": 8 }
    ],
    "confirmed_date_range": { "from": "20260324", "to": "20260420" }
  },
  "low_priced": [
    {
      "price_man": 40900,
      "floor": "8/15",
      "dong": "117/118동",
      "tags": ["다주택급매"],
      "desc": "다주택 급매...",
      "realtor": "...",
      "article_no": "...",
      "confirmed_ymd": "20260420"
    }
  ]
}
```

**가격 단위는 만원(10,000원)이다.** 40900만원 = 4.09억.

## Trade Type Codes

| 코드 | 의미 |
|---|---|
| A1 | 매매 |
| B1 | 전세 |
| B2 | 월세 |

(참고용. 잘못된 값은 서버가 거부한다.)

## Cost

- `region`, `search`: 0.3¢
- `articles`: 1¢ (페이지 수 무관)

## Limits

- 유저당 60 req/min (네이버 ToS 고려해 엄격히 제한)
- 단지당 최대 30페이지 (수백 매물)
- 캐시: region 24h, articles 15min

## Red Flags

- **부동산 투자 조언을 하지 마라** — 데이터 전달만
- **실거래가와 호가는 다르다** — 유저에게 "호가(판매자 제시가)" 명시
- 응답에 `error: "upstream"`이 오면 네이버 측 스키마 변경 가능성 — 서포트에 알림
- **다량 반복 조회 피할 것** — 캐시를 믿고, 같은 단지 다시 치지 말 것
