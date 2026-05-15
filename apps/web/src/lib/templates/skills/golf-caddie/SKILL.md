---
name: golf-caddie
description: 골프장 검색/상세정보, 빈자리(취소티) 조회 및 알림, 예약 딥링크. 골프, 골프장, 티타임, 부킹, 그린피, 예약, tee time, golf course 관련 질문에 사용.
---

# 골프 캐디 — 검색 · 빈자리 알림 · 예약 연결

골프장 정보 조회, 실시간 취소티 확인, 빈자리 알림, 예약 플랫폼 딥링크를 제공합니다.

## When to Use
- 유저가 골프장을 검색하거나 정보를 물을 때
- 취소티/빈자리를 확인하고 싶을 때
- 빈자리 알림을 설정하고 싶을 때
- 골프장 예약을 하고 싶을 때 (딥링크로 예약 플랫폼 연결)
- 그린피, 코스 정보, 골프장 시설을 물을 때

## API Access

Platform service — `integration.sh`로 호출. 무료 (크레딧 차감 없음).

응답 형식: `{ "data": {...}, "synced_at": "..." }`

**데이터 소스:**
- 한국: 더블이글(280개 CC, 1일 1회 캐싱) + 네이버 Local Search
- 글로벌: GolfCourseAPI.com

---

## 1. 골프장 검색

이름 또는 지역으로 골프장을 검색합니다. 네이버 Local(한국) + GolfCourseAPI(글로벌) 병합 결과.

```
integration.sh "golf/search?query=용인 골프장"
integration.sh "golf/search?query=이천"
integration.sh "golf/search?query=Pebble Beach&country=US"
```

| 파라미터 | 필수 | 설명 | 예시 |
|---------|------|------|------|
| `query` | ✅ | 검색어 (골프장명, 지역명) | `"용인 골프장"`, `"Pebble Beach"` |
| `country` | | 국가 코드 (ISO 2자리) | `"KR"`, `"US"` |
| `limit` | | 결과 수 (기본 10) | `"5"` |

한국 골프장 검색 시 `query`에 "골프장"을 포함하면 결과가 더 정확합니다.

## 2. 코스 상세 정보

GolfCourseAPI ID로 코스 상세 조회 (스코어카드, 티/슬로프 레이팅, 좌표). 주로 해외 코스용.

```
integration.sh "golf/course/12345"
```

## 3. 취소티(빈자리) 조회

더블이글에서 크롤링한 취소 티타임 정보를 조회합니다. 서버에서 1분마다 캐싱 중.

```
integration.sh "golf/availability?course=레이크사이드"
integration.sh "golf/availability?course=이천&date=2026-03-20"
```

| 파라미터 | 필수 | 설명 | 예시 |
|---------|------|------|------|
| `course` | ✅ | 골프장명 (더블이글 기준) | `"레이크사이드"`, `"이천"` |
| `date` | | 날짜 (YYYY-MM-DD) | `"2026-03-20"` |

응답에 포함되는 정보:
- `cancelTeeTimes`: 취소된 티타임 목록 (날짜, 시간, 코스, 그린피, D-day)
- `teeTimeTabs`: 카테고리별 티타임 (최근 취소티, 3인 가능 등)
- `booking_links`: 예약 플랫폼 딥링크

## 4. 빈자리 알림 설정

유저가 빈자리 알림을 요청하면 아래 순서로 설정합니다.

### Step 0: 취소티 조회 가능 여부 확인 (필수)

**알림 등록 전에 반드시** `golf/availability`로 해당 골프장이 더블이글 DB에 있는지 확인합니다.
응답의 `course` 필드에 `note: "Course not found in cache"`가 있으면 해당 골프장은 취소티 모니터링이 불가능합니다.
이 경우 유저에게 "해당 골프장은 현재 취소티 모니터링 대상이 아닙니다. 예약 플랫폼에서 직접 확인해주세요."라고 안내하고, 더블이글에 있는 **유사 골프장을 추천**해주세요.

취소티 모니터링이 가능한 골프장만 알림을 등록합니다.

### Step 1: 서버에 알림 등록

```
integration.sh "golf/alerts" --post '{"course":"레이크사이드CC","date":"2026-03-20","time_range":"morning"}'
```

| 파라미터 | 필수 | 설명 | 예시 |
|---------|------|------|------|
| `course` | ✅ | 골프장명 | `"레이크사이드CC"` |
| `date` | ✅ | 희망 날짜 | `"2026-03-20"` |
| `time_range` | | 시간대 | `"morning"`, `"afternoon"`, `"all"` (기본) |

### Step 2: 크론잡으로 30초 간격 조회

알림 등록 후, **반드시 크론잡을 등록**하여 30초마다 취소티를 확인합니다:

```
# 30초 간격 크론잡 등록
integration.sh "golf/availability?course=레이크사이드CC&date=2026-03-20"
```

서버에서 1분마다 더블이글 취소티를 캐싱하고 있으므로, 봇이 30초마다 조회하면 캐시에서 즉시 응답합니다.
새 취소티가 발견되면 유저에게 알림 메시지를 보냅니다.

### 크론잡 실행 시 동작 (중복 알림 방지 필수)

1. `golf/availability` 호출 → 서버 캐시에서 취소티 목록 반환
2. 응답의 각 취소티에서 `id` 값을 추출
3. **MEMORY.md에 저장된 `알림완료_ID` 목록과 비교** → 이미 알림한 ID는 제외
4. **새로운 ID만** 유저에게 알림 (날짜, 시간, 코스, 그린피, 예약 딥링크)
5. **알림한 ID를 즉시 MEMORY.md의 `알림완료_ID` 목록에 추가**
6. 새 취소티가 없으면 **아무것도 보내지 않음** (조용히 넘어감)

**주의: 이전에 알림한 취소티를 다시 알림하면 안 됩니다. 반드시 MEMORY.md의 ID 목록을 확인하세요.**

### 서버 Push 알림 (자동)

서버(health-monitor)가 1분마다 취소티 변동을 감지하고 봇에 webhook으로 알림을 보냅니다:
- **⛳ 새 취소티** (`change: "new"`): 새로운 취소 티타임이 등장했을 때
- **❌ 취소티 마감** (`change: "gone"`): 기존 취소티가 사라졌을 때 (누군가 예약했거나 마감)

서버 push 알림은 별도 처리 없이 유저에게 그대로 전달하면 됩니다.

### MEMORY.md 저장 형식

```markdown
## 골프 빈자리 모니터링

### 조건 1: [유저가 지정한 이름]
- 골프장: 레이크사이드CC (더블이글 ID: 347)
- 날짜: 2026-03-20
- 시간대: morning
- 주기: 30초
- 알림완료_ID: 32639114, 32411558, 32758558
```

`알림완료_ID`에 있는 취소티는 절대 다시 알림하지 않습니다.

### 알림 목록 조회

```
integration.sh "golf/alerts"
```

### 알림 삭제

```
integration.sh "golf/alerts" --post '{"action":"delete","alertId":"알림ID"}'
```

알림은 30일 후 자동 만료됩니다. 날짜가 지난 알림은 자동 삭제됩니다.

## 5. 예약 딥링크

예약 플랫폼으로 바로 이동하는 링크를 생성합니다.

```
integration.sh "golf/book?course=레이크사이드CC&date=2026-03-20"
```

응답에 포함되는 링크:
- 카카오골프예약, XGOLF, 더블이글 (한국)
- GolfNow, TeeOff (미국)

---

## Workflow Examples

### 예시 1: 골프장 찾기

유저: "경기도 용인 근처 골프장 추천해줘"

1. `integration.sh "golf/search?query=용인 골프장"`
2. 네이버 Local 결과에서 골프장 목록 정리 (이름, 주소, 카테고리)
3. 관심 코스 취소티 확인: `integration.sh "golf/availability?course=레이크사이드"`

### 예시 2: 취소티 확인 + 예약

유저: "레이크사이드CC 이번 주말 빈자리 있어?"

1. `integration.sh "golf/availability?course=레이크사이드CC&date=2026-03-15"`
2. 취소티 결과 정리 (날짜, 시간, 코스, 그린피, D-day)
3. 예약 링크 안내: "더블이글에서 예약하기: [링크]"

### 예시 3: 빈자리 알림

유저: "레이크사이드CC 3월 20일 오전 빈자리 나면 알려줘"

1. 서버 알림 등록: `integration.sh "golf/alerts" --post '{"course":"레이크사이드CC","date":"2026-03-20","time_range":"morning"}'`
2. 크론잡 등록: 30초 간격으로 `integration.sh "golf/availability?course=레이크사이드CC&date=2026-03-20"` 호출
3. "빈자리 알림을 설정했습니다. 30초마다 취소티를 확인하고 새 자리가 나면 알려드리겠습니다."
4. (크론잡 실행 시 MEMORY.md의 이전 취소티 ID와 비교 → 새 취소티만 알림 + 예약 딥링크)

### 예시 4: 미국 골프장

유저: "Pebble Beach tee time 확인해줘"

1. `integration.sh "golf/search?query=Pebble Beach&country=US"`
2. `integration.sh "golf/book?course=Pebble Beach&date=2026-03-21"`
3. GolfNow/TeeOff 딥링크 안내
