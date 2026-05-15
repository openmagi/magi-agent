---
name: korean-life
description: Use when the user asks about Korean stores, products, inventory, movie showtimes, or nearby locations for Daiso, CU, Olive Young, Emart24, Megabox, CGV, or Lotte Cinema. Also use for 다이소, 편의점, 올리브영, 이마트24, 메가박스, 롯데시네마, 영화 시간표, 재고 확인, 매장 찾기.
---

# 한국 생활 비서 (Korean Life Assistant)

한국 오프라인 매장 상품/재고 검색, 영화관 시간표/좌석 조회 기능. 7개 서비스 지원.

## API Access

플랫폼이 무료로 제공하므로 `integration.sh korean-life/...` 로 바로 사용 가능. 크레딧 차감 없음.

응답 형식: `{ "data": <JSON 응답>, "synced_at": "..." }`

---

## 1. 다이소 (Daiso)

### 상품 검색
```
integration.sh korean-life/daiso/products?q=수납박스
integration.sh korean-life/daiso/products?q=USB충전기&page=2&pageSize=10
```

### 매장 찾기
```
integration.sh korean-life/daiso/stores?keyword=강남역
integration.sh korean-life/daiso/stores?sido=서울&gugun=강남구
```

### 재고 확인
```
integration.sh korean-life/daiso/inventory?productId=1034604
integration.sh korean-life/daiso/inventory?productId=1034604&lat=37.50&lng=127.03
```
- `productId`: 상품 검색 결과에서 획득
- `lat`/`lng`: 생략 시 서울시청 기준 (37.5665, 126.978)

### 진열 위치
```
integration.sh korean-life/daiso/display-location?productId=1034604&storeCode=04515
```
- `storeCode`: 매장 검색 결과에서 획득

---

## 2. CU 편의점

### 매장 찾기
```
integration.sh korean-life/cu/stores?keyword=강남
```

### 상품 재고 확인
```
integration.sh korean-life/cu/inventory?keyword=두바이초콜릿
integration.sh korean-life/cu/inventory?keyword=과자&storeKeyword=강남&lat=37.50&lng=127.03
```

---

## 3. 올리브영 (Olive Young)

### 매장 찾기
```
integration.sh korean-life/oliveyoung/stores?keyword=명동
integration.sh korean-life/oliveyoung/stores?lat=37.5665&lng=126.978
```

### 상품 재고 확인
```
integration.sh korean-life/oliveyoung/inventory?keyword=선크림
integration.sh korean-life/oliveyoung/inventory?keyword=선크림&lat=37.56&lng=126.98&size=10
```

---

## 4. 이마트24 (Emart24)

### 매장 찾기
```
integration.sh korean-life/emart24/stores?keyword=강남
integration.sh korean-life/emart24/stores?keyword=홍대&service24h=true
```

### 상품 검색
```
integration.sh korean-life/emart24/products?keyword=두바이
integration.sh korean-life/emart24/products?keyword=과자&sortType=PRICE_ASC&pageSize=20
```
sortType: `SALE` (인기순), `LATEST` (최신순), `PRICE_ASC` (낮은가격순), `PRICE_DESC` (높은가격순)

### 재고 확인
```
integration.sh korean-life/emart24/inventory?pluCd=8800244010504&storeKeyword=강남
integration.sh korean-life/emart24/inventory?keyword=두바이초콜릿&storeKeyword=역삼
```
- `pluCd`: 상품 검색 결과에서 획득 (바코드)
- `keyword`: pluCd 없을 때 상품명으로 자동 검색

---

## 5. 메가박스 (Megabox)

### 지점 찾기
```
integration.sh korean-life/megabox/theaters
integration.sh korean-life/megabox/theaters?keyword=강남
```

### 영화 + 시간표 + 좌석
```
integration.sh korean-life/megabox/movies?theaterId=1351&playDate=20260312
integration.sh korean-life/megabox/movies?playDate=20260312
```
- `theaterId`: 지점 검색 결과의 `brchNo`
- `playDate`: YYYYMMDD (생략 시 오늘)
- 응답에 `movies` (영화 목록) + `showtimes` (상영 시간표, 잔여석 포함)

---

## 6. CGV

### 극장 찾기
```
integration.sh korean-life/cgv/theaters
integration.sh korean-life/cgv/theaters?regionCode=01&playDate=20260312
```
regionCode: `01`=서울, `02`=경기, `03`=인천, `04`=대전/충청, `05`=대구, `06`=부산/울산, `07`=경상, `08`=광주/전라/제주

### 영화 목록
```
integration.sh korean-life/cgv/movies?playDate=20260312
```

### 시간표 + 좌석
```
integration.sh korean-life/cgv/timetable?theaterCode=0056&playDate=20260312
integration.sh korean-life/cgv/timetable?movieCode=20045&playDate=20260312
integration.sh korean-life/cgv/timetable?theaterCode=0056&movieCode=20045&playDate=20260312
```
- `theaterCode`: 극장 검색 결과에서 획득
- `movieCode`: 영화 목록에서 획득
- 둘 중 하나는 필수

---

## 7. 롯데시네마 (Lotte Cinema)

### 지점 찾기
```
integration.sh korean-life/lottecinema/theaters
```

### 영화 + 시간표
```
integration.sh korean-life/lottecinema/movies
integration.sh korean-life/lottecinema/movies?theaterCode=1013&movieCode=19860&playDate=20260312
```
- theaterCode + movieCode 둘 다 주면 해당 조합의 상세 상영 시간표 (잔여석 포함)
- 생략 시 전체 상영 영화 목록

---

## Workflow

### 매장 상품 재고 확인
1. **매장 찾기**: `integration.sh korean-life/{service}/stores?keyword=강남`
2. **상품 검색**: `integration.sh korean-life/{service}/products?keyword=...` (다이소/이마트24)
3. **재고 확인**: `integration.sh korean-life/{service}/inventory?productId=...&lat=...&lng=...`

### 영화 예매 정보 확인
1. **극장 찾기**: `integration.sh korean-life/{cinema}/theaters?keyword=강남`
2. **영화+시간표 조회**: `integration.sh korean-life/{cinema}/movies?theaterId=...&playDate=YYYYMMDD`
3. 잔여석 정보는 showtimes 응답의 `restSeatCnt`/`remainingSeats`/`RemainSeat` 필드

### 여러 서비스 비교
사용자가 "강남에서 수납박스 사고싶어"라고 하면:
1. 다이소 상품 검색 + 재고
2. 이마트24 상품 검색 + 재고
3. 올리브영 재고 (해당 시)
→ 가격, 재고, 거리 비교하여 추천

## Red Flags

- 모든 응답은 `{ data: <...>, synced_at: "..." }` 형태 — `data` 필드에서 실제 데이터 추출
- `lat`/`lng` 생략 시 서울시청 기준 — 사용자 위치를 모르면 물어볼 것
- 재고 데이터는 3분 캐시 — "방금 확인" 정도의 정확도
- 비공식 API 사용 — 간헐적으로 응답이 없을 수 있음 (에러 시 사용자에게 직접 확인 권유)
- 영화 시간표는 당일 기준 — 미래 날짜 조회 시 `playDate` 파라미터 필수 (YYYYMMDD)
