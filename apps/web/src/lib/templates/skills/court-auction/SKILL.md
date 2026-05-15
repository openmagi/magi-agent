---
name: court-auction
description: 법원경매 물건 검색/상세조회 + 공매(온비드) 검색 + 모니터링. 경매, 공매, 부동산 경매, 법원 경매, 입찰, 낙찰, 감정가, 최저가 관련 질문에 사용.
---

# 법원경매 & 공매 — 검색 · 상세조회 · 모니터링

법원경매(courtauction.go.kr 데이터) 물건 검색/상세조회와 온비드 공매 물건 검색을 제공합니다.

## When to Use
- 유저가 경매/공매 물건을 검색하거나 조회할 때
- 부동산 경매 투자, 입찰 정보를 물을 때
- 경매 모니터링을 설정하거나 확인할 때
- 감정가, 최저가, 매각기일 등 경매 관련 데이터가 필요할 때

## API Access

Platform service — `integration.sh`로 호출. 법원경매는 API 호출마다 크레딧이 차감됩니다. 공매(온비드)는 무료.

응답 형식: `{ "data": {...}, "synced_at": "..." }`

---

## 법원경매 (Court Auction)

### 1. 경매 신건 검색 (새로 접수된 사건)

```
integration.sh "auction/court/search-new" '{"sido":"11","gugun":"680"}'
```

### 2. 경매 진행물건 검색 (현재 진행 중)

```
integration.sh "auction/court/search-ongoing" '{"sido":"11","yongdo":"apt"}'
```

### 3. 용도별 물건 검색

```
integration.sh "auction/court/search-by-type" '{"yongdo":"apt","sido":"11","lowMax":"500000000"}'
```

### 검색 공통 파라미터 (모두 선택사항)

| 파라미터 | 설명 | 예시 |
|---------|------|------|
| `page` | 페이지 번호 (기본 1) | `"1"` |
| `yongdo` | 용도코드 (codes/yongdo 참조) | `"apt"` |
| `court` | 법원코드 (codes/court 참조) | |
| `sido` | 시도코드 (codes/sido 참조) | `"11"` (서울) |
| `gugun` | 구군코드 (codes/gugun 참조) | |
| `dong` | 읍면동코드 (codes/dong 참조) | |
| `barea_min` / `barea_max` | 건물면적 범위 (㎡) | `"60"` / `"85"` |
| `larea_min` / `larea_max` | 토지면적 범위 (㎡) | |
| `lowMin` / `lowMax` | 최저가 범위 (원) | `"100000000"` / `"500000000"` |
| `gamMin` / `gamMax` | 감정가 범위 (원) | |
| `sday_s` / `sday_e` | 매각기일 범위 (YYYY-MM-DD) | `"2026-03-15"` / `"2026-03-30"` |
| `syear` | 사건년도 | `"2026"` |
| `sno` | 사건번호 (알고 있는 경우) | |

### 검색 응답 주요 필드

- `boCd` — 법원코드 (상세조회에 필요)
- `saNo` — 사건번호 full (상세조회에 필요)
- `srnSaNo` — 화면 표시용 사건번호 (예: "2023타경3835")
- `jiwonNm` / `jpDeptNm` — 법원명 / 담당계
- `hjguSido` / `hjguSigu` / `hjguDong` — 소재지 (시도/구군/동)
- `printSt` — 전체 주소 (도로명)
- `buldNm` / `buldList` — 건물명 / 동호수
- `gamevalAmt` / `minmaePrice` — 감정가 / 최저가 (원)
- `maeGiil` — 매각기일 (YYYYMMDD)
- `dspslUsgNm` — 용도명 (아파트, 다세대 등)
- `yuchalCnt` — 유찰 횟수
- `pjbBuldList` — 구조 + 면적 (예: "철근콘크리트구조 51.36㎡")
- `totallist` / `totalpage` / `nowpage` — 페이지네이션

### 4. 경매사건 상세보기

**반드시 검색 결과의 `boCd`와 `saNo` 값을 사용하세요.**

```
integration.sh "auction/court/detail" '{"boCd":"검색결과의 boCd 값","saNo":"검색결과의 saNo 값"}'
```

상세 응답 포함 정보:
- 기본: 사건명칭, 법원명, 담당계, 매각기일, 감정가, 최저가, 보증금
- 소재지: 도로명주소, 지번주소, 대지권면적, 건물면적
- 당사자: 소유자, 채무자, 채권자
- **등기부**: 갑구/을구 권리현황, 말소기준권리, 예상채권총액
- **임차인현황**: 임차인, 보증금, 전입일자, 확정일자
- **배당순서**: 예상배당순서, 권리금액, 배당금액
- **감정평가서**: 감정평가서 요약
- **이미지**: 물건 사진 목록
- **진행과정**: 경매개시일, 감정평가일, 최초경매일
- **기일리스트**: 과거/예정 기일 목록
- **인근물건**: 주변 경매 물건
- **인근매각사례**: 주변 낙찰 사례
- **역세권**: 가까운 지하철역 + 거리
- **개발계획**: 주변 개발 계획
- **아파트정보**: 건설사, 입주년도, 세대수, 주차대수
- **예상명도비용**: 명도 비용 계산

### 5. 코드 조회 (필터 값 확인용)

```
integration.sh "auction/court/codes/sido"
integration.sh "auction/court/codes/gugun" '{"sido":"11"}'
integration.sh "auction/court/codes/dong" '{"gugun":"680"}'
integration.sh "auction/court/codes/court"
integration.sh "auction/court/codes/yongdo"
integration.sh "auction/court/codes/status"
integration.sh "auction/court/codes/location"
```

시도 → 구군 → 동 순서로 하위 코드를 조회합니다.

---

## 공매 (OnBid — 온비드)

공매 = 한국자산관리공사(KAMCO) 및 정부기관의 공매 물건. 법원경매와 다른 제도.

### 1. 캠코 공매물건 검색

```
integration.sh "auction/onbid/kamco/list" '{"page":"1","perPage":"10"}'
```

### 2. 이용기관 공매물건 검색

```
integration.sh "auction/onbid/institution/list" '{"page":"1","perPage":"10"}'
```

### 3. 온비드 코드 조회

```
integration.sh "auction/onbid/codes"
```

---

## 모니터링 설정

유저가 모니터링을 요청하면 MEMORY.md에 아래 형식으로 저장하고 크론잡을 등록합니다.

### MEMORY.md 저장 형식

```markdown
## 경매 모니터링

### 조건 1: [유저가 지정한 이름]
- 유형: court (법원경매) 또는 onbid (공매)
- 검색API: search-ongoing (또는 search-new, search-by-type)
- 시도: [시도코드]
- 구군: [구군코드]
- 용도: [용도코드]
- 감정가 상한: [금액]
- 최저가 상한: [금액]
- 건물면적 최소: [㎡]
- 주기: 매일 09:00 (또는 유저 설정)
- 마지막 체크: [날짜]
- 알림한 경매번호: [목록]
```

### 크론잡 실행 시

1. MEMORY.md에서 모니터링 조건 읽기
2. 조건에 맞는 검색 API 호출
3. 이전에 알림한 경매번호 목록과 비교
4. 새 물건만 필터링하여 요약 메시지 작성:
   - 물건명, 소재지, 감정가, 최저가, 매각기일, 유찰수
5. MEMORY.md에 알림한 경매번호 추가

### 모니터링 권유 사항

- **주기**: 1일 1~2회를 권유합니다. 법원경매는 보통 일 단위로 업데이트됩니다.
- **비용**: "법원경매 API 호출마다 소정의 크레딧이 차감됩니다. 공매(온비드)는 무료입니다."
- 유저가 원하면 더 자주 체크하는 것도 가능합니다.

---

## Workflow Examples

### 예시 1: 경매 물건 탐색

유저: "서울 강남구 아파트 감정가 5억 이하 경매 물건 보여줘"

1. 시도코드 확인: `integration.sh "auction/court/codes/sido"` → 서울 = "11"
2. 구군코드 확인: `integration.sh "auction/court/codes/gugun" '{"sido":"11"}'` → 강남구 코드 확인
3. 검색: `integration.sh "auction/court/search-ongoing" '{"sido":"11","gugun":"[코드]","yongdo":"apt","gamMax":"500000000"}'`
4. 결과 요약 제공 (소재지, 감정가, 최저가, 매각기일, 유찰수)
5. 유저가 관심 물건 선택 → 상세조회

### 예시 2: 모니터링 설정

유저: "서울 강남 아파트 감정가 5억 이하로 매일 아침 체크해줘"

1. 위와 같이 코드 확인
2. MEMORY.md에 모니터링 조건 저장
3. 크론잡 등록 (매일 09:00)
4. "모니터링을 설정했습니다. 매일 아침 9시에 새 물건을 확인하고 알려드리겠습니다."

### 예시 3: 상세 분석

유저: "이 물건 상세정보 보여줘" (검색 결과에서)

1. `integration.sh "auction/court/detail" '{"boCd":"[검색결과의 boCd]","saNo":"[검색결과의 saNo]"}'`
2. 핵심 정보 요약:
   - 권리분석: 말소기준권리, 등기부 주요 권리, 임차인 현황
   - 가격분석: 감정가 vs 최저가, 유찰 횟수, 인근 매각사례
   - 입찰정보: 매각기일, 보증금, 매각조건
   - 부가정보: 역세권, 개발계획, 아파트정보
