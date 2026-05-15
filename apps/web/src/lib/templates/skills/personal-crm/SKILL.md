---
name: personal-crm
description: Track people mentioned in conversations. Auto-detect names and context, store in knowledge/people.json, answer relationship queries.
metadata:
  author: openmagi
  version: "1.0"
---

# Personal CRM — People Tracker

대화에서 언급되는 사람들의 정보를 `knowledge/people.json`에 자동으로 기록하고, 나중에 물어보면 찾아준다.

## 파일 위치

`knowledge/people.json` — 없으면 첫 기록 시 생성한다.

## 스키마

```json
{
  "people": {
    "민수": {
      "name": "김민수",
      "relation": "대학 동기, 절친",
      "org": "네이버",
      "role": "백엔드 개발자",
      "context": [
        "이직 준비 중 (2026-03)",
        "러닝 같이 하자고 했음",
        "강아지 이름 초코"
      ],
      "last_contact": "2026-03-08",
      "next_action": "다음주 저녁 약속 잡기",
      "tags": ["개발자", "친구", "러닝"]
    }
  },
  "_meta": {
    "version": 1,
    "updated": "2026-03-09"
  }
}
```

### 필드 규칙

- **키**: 유저가 보통 부르는 이름 ("민수", "사라", "팀장님")
- **name**: 풀네임 (알면 기록, 모르면 생략)
- **relation**: 나와의 관계 (자유 텍스트)
- **org / role**: 소속, 직함 (알면 기록)
- **context**: 시간순 배열. 새 정보는 끝에 추가. 날짜 표기 권장 (예: "이직 준비 중 (2026-03)")
- **last_contact**: 마지막으로 이 사람 얘기가 나온 날 (YYYY-MM-DD)
- **next_action**: 다음에 해야 할 일 (없으면 생략)
- **tags**: 검색용 태그 (자유롭게)
- **필수 필드 없음** — 아는 만큼만 채운다. 키 + 아무 정보 1개면 충분.

## 자동 감지 규칙

대화에서 사람 이름이 나오면 **자동으로** people.json을 업데이트한다.

### 감지 대상

- "오늘 민수 만났는데..." → 민수 항목 업데이트, last_contact 갱신
- "지영이가 구글로 이직했대" → 지영 org/role 업데이트 + context 추가
- "다음주에 사장님이랑 미팅" → 사장님 next_action 기록

### 감지 제외

- 유명인, 공인 (뉴스/검색 맥락에서 나온 사람)
- 일회성 언급으로 관계가 없는 사람 (배달기사, 고객센터 등)
- 유저가 명시적으로 "기록하지 마" 라고 한 사람

### 업데이트 방식

1. people.json 읽기
2. 해당 인물 항목 찾기 (없으면 새로 생성)
3. 새 정보 반영 (context 배열에 추가, 필드 업데이트)
4. `last_contact`를 오늘 날짜로 갱신
5. `_meta.updated`를 오늘 날짜로 갱신
6. 파일 저장
7. `qmd --index {{BOT_NAME}} update` 실행 (RAG 인덱스 갱신)

**중요:** 업데이트는 조용히 한다. "기록했습니다" 같은 알림 불필요. 유저가 물어보지 않는 한 CRM 동작을 언급하지 않는다.

## 조회 패턴

유저가 사람에 대해 물어보면 people.json을 먼저 확인한다.

| 질문 예시 | 동작 |
|-----------|------|
| "민수가 누구였지?" | people.json에서 민수 항목 전체 반환 |
| "개발자 친구 누구 있어?" | tags에 "개발자" 있는 사람들 목록 |
| "요즘 안 만난 사람 있어?" | last_contact 기준 오래된 순 정렬 |
| "다음에 할 일 뭐 있어?" | next_action이 있는 사람들 목록 |
| "지영이 회사 어디야?" | people.json에서 지영 → org 반환 |
| "내 인맥 정리해줘" | 전체 people 요약 (이름, 관계, 마지막 연락) |

### 조회 순서

1. `knowledge/people.json` 읽기
2. 질문에 맞는 필터/정렬 수행
3. 자연스럽게 답변 (JSON 그대로 보여주지 않기)

## 초기화

people.json이 없을 때 첫 인물 기록 시:

```json
{
  "people": {},
  "_meta": {
    "version": 1,
    "updated": "YYYY-MM-DD"
  }
}
```

인물 추가 후 저장.
