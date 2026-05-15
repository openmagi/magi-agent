---
name: maps-korea
description: Use when the user asks about directions, routes, nearby places, or locations in Korea. Covers 카카오 자동차 경로, TMAP 대중교통, 네이버/카카오 장소 검색. Also use for 길찾기, 경로, 내비, 대중교통, 맛집, 카페, 근처, 주변.
---

# 지도 — 한국 (Maps Korea)

카카오 자동차 경로, TMAP 대중교통 경로, 네이버/카카오 장소 검색. 4개 서비스 지원.

## API Access

플랫폼이 무료로 제공하므로 `integration.sh maps-kr/...`로 바로 사용 가능. 크레딧 차감 없음.

응답 형식: `{ "data": <JSON 응답>, "synced_at": "..." }`

---

## 1. 카카오 자동차 경로

출발지→도착지 자동차 경로 안내 (거리, 시간, 톨게이트 비용).

```
integration.sh "maps-kr/kakao/directions?origin=37.5665,126.978&destination=37.4979,127.0276"
integration.sh "maps-kr/kakao/directions?origin=37.5665,126.978&destination=37.4979,127.0276&priority=DISTANCE"
```

### Parameters
- `origin` (필수): 출발지 `lat,lng`
- `destination` (필수): 도착지 `lat,lng`
- `waypoints` (선택): 경유지 `lng,lat|lng,lat` (최대 5개, 주의: 경유지는 lng,lat 순서)
- `priority` (선택): `RECOMMEND` (기본), `TIME`, `DISTANCE`

### Response 주요 필드
- `routes[].summary.distance` — 총 거리 (m)
- `routes[].summary.duration` — 총 시간 (초)
- `routes[].summary.fare.toll` — 톨게이트 비용 (원)
- `routes[].summary.fare.taxi` — 택시 예상 요금 (원)

---

## 2. 카카오 장소 검색

키워드로 장소 검색. 음식점, 카페, 병원 등.

```
integration.sh "maps-kr/kakao/places?query=강남역 카페"
integration.sh "maps-kr/kakao/places?query=홍대 맛집&x=126.9237&y=37.5563&size=10"
```

### Parameters
- `query` (필수): 검색 키워드
- `x` (선택): 중심 좌표 경도 (longitude)
- `y` (선택): 중심 좌표 위도 (latitude)
- `page` (선택): 페이지 번호 (1~45)
- `size` (선택): 결과 수 (기본 5, 최대 15)

### Response 주요 필드
- `documents[].place_name` — 장소명
- `documents[].address_name` — 지번 주소
- `documents[].road_address_name` — 도로명 주소
- `documents[].phone` — 전화번호
- `documents[].category_name` — 카테고리
- `documents[].x`, `documents[].y` — 경도, 위도
- `documents[].place_url` — 카카오맵 URL
- `documents[].distance` — 중심 좌표로부터 거리 (m, x/y 제공 시)

---

## 3. TMAP 대중교통 경로

지하철 + 버스 환승 경로 안내.

```
integration.sh "maps-kr/tmap/transit?startX=126.978&startY=37.5665&endX=127.0276&endY=37.4979"
```

### Parameters
- `startX` (필수): 출발지 경도 (longitude)
- `startY` (필수): 출발지 위도 (latitude)
- `endX` (필수): 도착지 경도
- `endY` (필수): 도착지 위도

### Response 주요 필드
- `metaData.plan.itineraries[]` — 경로 목록
- `itineraries[].totalTime` — 총 소요시간 (초)
- `itineraries[].totalWalkTime` — 도보 시간 (초)
- `itineraries[].transferCount` — 환승 횟수
- `itineraries[].totalWalkDistance` — 도보 거리 (m)
- `itineraries[].fare.regular.totalFare` — 총 요금 (원)
- `itineraries[].legs[]` — 각 구간 (도보/버스/지하철)
  - `legs[].mode` — `WALK`, `BUS`, `SUBWAY`
  - `legs[].route` — 노선명 (예: "2호선", "146번")
  - `legs[].start/end.name` — 정류장/역 이름

---

## 4. 네이버 지역 검색

네이버 검색으로 맛집, 카페, 병원 등 장소 검색.

```
integration.sh "maps-kr/naver/local?query=강남역 맛집"
integration.sh "maps-kr/naver/local?query=판교 카페&display=10&sort=comment"
```

### Parameters
- `query` (필수): 검색 키워드
- `display` (선택): 결과 수 (기본 5, 최대 5)
- `start` (선택): 시작 위치 (기본 1, 최대 1000)
- `sort` (선택): `random` (기본, 정확도순), `comment` (리뷰 많은순)

### Response 주요 필드
- `items[].title` — 장소명 (HTML 태그 포함, 제거 필요)
- `items[].link` — 네이버 플레이스 URL
- `items[].category` — 카테고리
- `items[].address` — 지번 주소
- `items[].roadAddress` — 도로명 주소
- `items[].mapx`, `items[].mapy` — 좌표 (카텍 좌표, WGS84 아님)
- `items[].telephone` — 전화번호

---

## Workflow

### 경로 찾기
1. 사용자가 "강남에서 홍대까지 어떻게 가?" 라고 하면:
2. **자동차**: `integration.sh "maps-kr/kakao/directions?origin=37.4979,127.0276&destination=37.5563,126.9237"`
3. **대중교통**: `integration.sh "maps-kr/tmap/transit?startX=127.0276&startY=37.4979&endX=126.9237&endY=37.5563"`
4. 두 결과 비교하여 안내 (시간, 비용, 환승 횟수)

### 주변 장소 찾기
1. 사용자가 "강남역 근처 맛집 알려줘" 라고 하면:
2. 카카오 장소 검색: `integration.sh "maps-kr/kakao/places?query=강남역 맛집&x=127.0276&y=37.4979"`
3. 네이버 지역 검색: `integration.sh "maps-kr/naver/local?query=강남역 맛집&sort=comment"`
4. 두 결과를 종합하여 추천

### 좌표 모르는 경우
주소만 알고 좌표를 모르면 Google Maps geocoding 사용:
```
integration.sh "maps/geocode?address=서울시 강남구 역삼동&language=ko"
```

## Red Flags

- 좌표 순서 주의: 카카오 directions는 `lat,lng` 입력 → 내부에서 `lng,lat`로 변환. 카카오 places는 `x=lng, y=lat`.
- TMAP은 `startX=lng, startY=lat` (경도가 X, 위도가 Y)
- 네이버 지역 검색 좌표(mapx/mapy)는 카텍 좌표 — WGS84(GPS)와 다름. 경로 검색에 직접 사용 불가.
- 네이버 title에 HTML 태그(`<b>` 등) 포함 — 표시 전 제거 필요
- 사용자가 위치를 안 알려주면 물어볼 것 (기본값 없음)
