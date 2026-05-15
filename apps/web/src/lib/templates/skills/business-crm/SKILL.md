---
name: business-crm
description: Use when user asks about CRM, sales pipeline, deals, business contacts, client management, follow-ups, or wants to track business relationships and opportunities. Provides structured SQLite-backed CRUD operations.
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Business CRM

SQLite 기반 비즈니스 CRM. 연락처, 회사, 딜 파이프라인, 활동 로그, 후속 조치를 구조화된 데이터로 관리한다.

## personal-crm과의 차이

- **personal-crm**: 대화 중 자동 감지, 패시브, 인맥 메모 (`knowledge/people.json`)
- **business-crm**: 명시적 요청 시 동작, 액티브, 비즈니스 데이터 (`knowledge/crm.db`)

둘은 공존한다. 겹치지 않는다.

## 데이터

SQLite DB: `knowledge/crm.db` (없으면 첫 사용 시 자동 생성)

### 테이블

| 테이블 | 용도 |
|--------|------|
| contacts | 비즈니스 연락처 (이름, 이메일, 전화, 회사, 역할, 태그) |
| companies | 회사 (이름, 업종, 웹사이트) |
| deals | 딜/기회 (제목, 금액, 파이프라인 스테이지, 예상 마감일) |
| activities | 활동 로그 (통화, 미팅, 이메일, 메모) |
| tasks | 후속 조치 (할 일, 기한, 우선순위) |

### 딜 파이프라인 스테이지

`lead` → `qualified` → `proposal` → `negotiation` → `won` / `lost`

## 사용법

모든 CRM 작업은 `scripts/crm.sh` 스크립트로 실행한다.

### 연락처

```bash
# 연락처 추가
bash scripts/crm.sh add-contact "김민수" "minsu@example.com" "010-1234-5678" "네이버" "백엔드 리드" '["개발자","VIP"]'

# 연락처 검색
bash scripts/crm.sh find-contacts "김민수"
bash scripts/crm.sh find-contacts "네이버"

# 연락처 수정
bash scripts/crm.sh update-contact 1 email "newemail@example.com" role "CTO"

# 연락처 목록
bash scripts/crm.sh list-contacts 20
bash scripts/crm.sh list-contacts 20 "VIP"
```

### 회사

```bash
# 회사 추가
bash scripts/crm.sh add-company "네이버" "IT" "https://naver.com"

# 회사 검색
bash scripts/crm.sh find-companies "네이버"

# 회사 수정
bash scripts/crm.sh update-company 1 industry "AI" website "https://naver.com"
```

### 딜

```bash
# 딜 추가 (title, contact_id, company_id, amount, stage, expected_close)
bash scripts/crm.sh add-deal "API 통합 계약" 1 1 50000000 "proposal" "2026-06-30"

# 딜 스테이지 변경
bash scripts/crm.sh move-deal 1 "negotiation"
bash scripts/crm.sh move-deal 1 "won"

# 딜 목록 (스테이지별, 연락처별)
bash scripts/crm.sh list-deals "proposal"
bash scripts/crm.sh list-deals "" 1

# 파이프라인 요약
bash scripts/crm.sh pipeline-summary
```

### 활동 로그

```bash
# 활동 기록 (type: call|meeting|email|note)
bash scripts/crm.sh log-activity "call" "API 가격 논의, 다음주 미팅 잡기로" 1 1
bash scripts/crm.sh log-activity "meeting" "계약 조건 협상 완료" 1 1
bash scripts/crm.sh log-activity "note" "경쟁사 제안도 받고 있다고 함" 1

# 최근 활동 조회
bash scripts/crm.sh recent-activities
bash scripts/crm.sh recent-activities 1 5
```

### 후속 조치

```bash
# 태스크 추가 (title, due_date, contact_id, deal_id, priority)
bash scripts/crm.sh add-task "계약서 초안 보내기" "2026-04-10" 1 1 "high"

# 태스크 완료
bash scripts/crm.sh complete-task 1

# 미완료 태스크 조회
bash scripts/crm.sh pending-tasks
bash scripts/crm.sh pending-tasks 1
bash scripts/crm.sh pending-tasks "" "overdue"
```

### 대시보드

```bash
# 전체 CRM 요약 (contacts, companies, pipeline, overdue tasks, recent activities)
bash scripts/crm.sh dashboard
```

## 응답 규칙

1. **CRM 데이터는 자연어로 답한다** — 테이블이나 JSON 그대로 보여주지 않는다
2. **여러 건이면 핵심만 요약**하고, 상세가 필요하면 추가로 보여준다
3. **딜 금액은 통화 단위와 함께** 표시한다 (예: "5,000만원")
4. **오버듀 태스크가 있으면 proactive하게 알려준다** — dashboard 호출 시 overdue 먼저 언급
5. **연락처 추가 시 회사가 없으면 자동 생성**한다 (add-contact의 company 파라미터)

## 트리거 패턴

유저가 아래와 같이 말하면 이 스킬을 사용한다:

| 유저 발화 예시 | 동작 |
|---------------|------|
| "거래처 추가해줘" | add-contact 또는 add-company |
| "파이프라인 보여줘" | pipeline-summary |
| "이번 달 딜 어떻게 돼?" | list-deals + pipeline-summary |
| "김대리한테 전화했어" | log-activity call |
| "다음주까지 계약서 보내야 해" | add-task |
| "할 일 뭐 있어?" | pending-tasks |
| "CRM 현황" | dashboard |
| "이 딜 성사됐어" | move-deal won |
