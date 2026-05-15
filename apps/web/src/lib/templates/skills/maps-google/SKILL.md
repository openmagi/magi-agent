---
name: maps-google
description: Use when the user asks about places, directions, addresses, or distances globally. Covers Google Place Search, Place Details, Geocoding, Directions, Distance Matrix. Also use for 장소 검색, 주소 변환, 글로벌 경로, 해외 맛집, distance.
---

# Maps (Google)

Google Maps API를 통한 글로벌 장소 검색, 경로 안내, 주소 변환 기능.

## API Access

`integration.sh maps/...`로 사용. **$0.005/call** (2회당 1센트).

응답 형식: `{ "data": <JSON 응답>, "synced_at": "..." }`

---

## 1. Place Search (장소 검색)

키워드로 장소 검색.

```
integration.sh "maps/places/search?query=coffee near Gangnam&language=ko"
integration.sh "maps/places/search?query=Tokyo ramen&language=ja&location=35.6762,139.6503&radius=2000"
```

### Parameters
- `query` (필수): 검색 키워드
- `language` (선택): 응답 언어 (ko, en, ja, zh, es 등)
- `location` (선택): 중심 좌표 `lat,lng`
- `radius` (선택): 검색 반경 (m, location과 함께 사용)
- `type` (선택): 장소 유형 (restaurant, cafe, hospital 등)

### Response 주요 필드
- `results[].name` — 장소명
- `results[].formatted_address` — 주소
- `results[].geometry.location.lat/lng` — 좌표
- `results[].rating` — 평점 (1~5)
- `results[].user_ratings_total` — 리뷰 수
- `results[].place_id` — Place ID (상세 조회용)
- `results[].opening_hours.open_now` — 현재 영업 여부
- `results[].price_level` — 가격대 (0~4)

---

## 2. Place Details (장소 상세)

Place ID로 상세 정보 조회.

```
integration.sh "maps/places/details?place_id=ChIJN1t_tDeuEmsRUsoyG83frY4&language=ko"
integration.sh "maps/places/details?place_id=ChIJN1t_tDeuEmsRUsoyG83frY4&fields=name,rating,reviews,opening_hours"
```

### Parameters
- `place_id` (필수): Place Search에서 획득한 ID
- `fields` (선택): 반환할 필드 (기본: name, formatted_address, geometry, rating, opening_hours, formatted_phone_number, website, reviews, photos)
- `language` (선택): 응답 언어

### Response 주요 필드
- `result.name` — 장소명
- `result.formatted_address` — 주소
- `result.formatted_phone_number` — 전화번호
- `result.website` — 웹사이트
- `result.rating` — 평점
- `result.reviews[]` — 리뷰 목록
- `result.opening_hours.weekday_text[]` — 영업시간
- `result.photos[].photo_reference` — 사진 참조 ID

---

## 3. Geocoding (주소 → 좌표)

주소를 좌표로 변환.

```
integration.sh "maps/geocode?address=서울시 강남구 역삼동&language=ko"
integration.sh "maps/geocode?address=1600 Amphitheatre Parkway, Mountain View, CA"
```

### Parameters
- `address` (필수): 변환할 주소
- `language` (선택): 응답 언어

### Response 주요 필드
- `results[].formatted_address` — 정규화된 주소
- `results[].geometry.location.lat/lng` — 좌표
- `results[].address_components[]` — 주소 구성 요소

---

## 4. Reverse Geocoding (좌표 → 주소)

좌표를 주소로 변환.

```
integration.sh "maps/geocode/reverse?latlng=37.5665,126.978&language=ko"
```

### Parameters
- `latlng` (필수): `lat,lng` 형식
- `language` (선택): 응답 언어

### Response 주요 필드
- `results[].formatted_address` — 주소
- `results[].address_components[]` — 주소 구성 요소

---

## 5. Directions (경로)

출발지→도착지 경로 안내 (자동차, 대중교통, 도보, 자전거).

```
integration.sh "maps/directions?origin=37.5665,126.978&destination=37.4979,127.0276&mode=transit&language=ko"
integration.sh "maps/directions?origin=Tokyo Station&destination=Shibuya&mode=walking&language=ja"
```

### Parameters
- `origin` (필수): 출발지 (좌표 또는 주소)
- `destination` (필수): 도착지 (좌표 또는 주소)
- `mode` (선택): `driving` (기본), `transit`, `walking`, `bicycling`
- `language` (선택): 응답 언어
- `alternatives` (선택): `true`이면 대안 경로 포함
- `waypoints` (선택): 경유지 (주소 또는 좌표, `|`로 구분)

### Response 주요 필드
- `routes[].legs[].distance.text/value` — 거리
- `routes[].legs[].duration.text/value` — 소요시간
- `routes[].legs[].steps[]` — 각 구간
  - `steps[].html_instructions` — 안내 텍스트
  - `steps[].travel_mode` — 이동 수단
  - `steps[].transit_details` — 대중교통 상세 (노선, 정류장 등)

---

## 6. Distance Matrix (거리 행렬)

여러 출발지↔도착지 간 거리/시간 계산.

```
integration.sh "maps/distance-matrix?origins=37.5665,126.978&destinations=37.4979,127.0276&mode=driving&language=ko"
integration.sh "maps/distance-matrix?origins=37.5665,126.978|37.50,127.03&destinations=37.4979,127.0276|37.55,126.97&mode=transit"
```

### Parameters
- `origins` (필수): 출발지 (`|`로 여러 개 구분)
- `destinations` (필수): 도착지 (`|`로 여러 개 구분)
- `mode` (선택): `driving` (기본), `transit`, `walking`, `bicycling`
- `language` (선택): 응답 언어

### Response 주요 필드
- `rows[].elements[].distance.text/value` — 거리
- `rows[].elements[].duration.text/value` — 소요시간
- `rows[].elements[].status` — `OK`, `ZERO_RESULTS`, `NOT_FOUND`

---

## Workflow

### 해외 여행 장소 찾기
1. **장소 검색**: `integration.sh "maps/places/search?query=Tokyo ramen&language=ko"`
2. **상세 조회**: `integration.sh "maps/places/details?place_id=ChIJ...&language=ko"`
3. **경로 안내**: `integration.sh "maps/directions?origin=hotel address&destination=ChIJ...&mode=transit&language=ko"`

### 주소↔좌표 변환
한국 지도 서비스(maps-kr)에 좌표가 필요할 때:
1. `integration.sh "maps/geocode?address=서울시 강남구 역삼동&language=ko"` → 좌표 획득
2. 획득한 좌표로 `maps-kr/kakao/directions` 또는 `maps-kr/tmap/transit` 호출

### 여러 장소 비교
1. 후보 장소 검색
2. Distance Matrix로 한번에 거리/시간 비교: `origins=현재위치&destinations=장소1|장소2|장소3`

## Red Flags

- **과금**: $0.005/call — 불필요한 API 호출 최소화. 한 번에 필요한 정보를 모두 요청
- 한국 내 경로는 `maps-kr` (카카오/TMAP) 사용 추천 — 무료이고 한국 도로/대중교통에 더 정확
- Google Maps는 해외 장소/경로 또는 주소↔좌표 변환에 주로 활용
- Place Search 결과의 `place_id`를 Place Details에 사용 — 두 단계 호출 필요
- `opening_hours`가 없는 장소도 있음 — 영업 여부 불확실하면 사용자에게 안내
- Distance Matrix는 origins × destinations 조합마다 1회 과금 — 10×10 = 100회 과금 주의
