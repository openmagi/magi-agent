---
name: pos-store-context
description: 매장별 고유 도메인 지식 수집/저장/조회. 카페/바/분식 등 업종에 따라 메뉴 옵션 로직, 고객 세그먼트, 운영 패턴이 완전히 다름. 이 스킬이 사장님과 대화하며 매장 특성을 KB에 축적하고, 다른 POS 스킬이 분석할 때 컨텍스트로 참조하게 함. 사장님이 "우리 매장은 ~", "주로 ~ 손님", 메뉴 옵션 설명, 운영 패턴 언급 등을 하면 이 스킬이 발동. 모든 POS 분석 응답 끝에 meta-cognition으로 자동 포착도 수행.
---

# POS 매장 컨텍스트 (Store Context)

## 왜 이 스킬이 존재하나

카페 "앰프레소"는 아메리카노에 `Hot/Ice × 원두타입 × 샷추가 × Size` 조합이 원가/재고에 큰 영향을 주고, 위스키바 "키퍼스"는 워크인보다 **기존 멤버십 손님**이 중요하다. 같은 `sales-summary` 결과를 봐도 이 맥락 없이는 인사이트가 일반론에 그친다. 이 스킬이 매장 컨텍스트를 수집/저장/조회해서 다른 POS 스킬들이 커스텀 분석을 낼 수 있게 한다.

## 절대 규칙

- **매장 컨텍스트는 KB 컬렉션 `store-context`에 저장.** Supabase 테이블이나 MEMORY.md에 저장 금지
- **하나의 매장 = 5개 document** (아래 규약)
- **merchantId는 반드시 `tossplace/my-merchants` 결과 기준.** MEMORY/USER의 매장 정보는 stale 가능성 높음 — 항상 서버 검증
- **`integration.sh`는 PATH CLI 명령어** — `cat`/`ls`/`source`로 파일 다루지 마. `system.run ["sh", "-c", "integration.sh '...'"]` 로만 실행. `not found` 시 절대경로 `/home/ocuser/.openclaw/bin/integration.sh` fallback.

## KB 문서 규약

컬렉션 `store-context`, 각 매장마다 아래 5개 document:

| filename | 내용 | 초기 생성 | 업데이트 트리거 |
|----------|------|-----------|----------------|
| `{merchantId}-profile.md` | 업종, 컨셉, 위치, 규모, 주 타겟, 사업자번호, onboarded 날짜 | Onboarding 1차 | 드물게 |
| `{merchantId}-menu-logic.md` | 메뉴 카테고리, 옵션 조합의 실제 의미 (원두×샷, 사이즈업 원가) | Onboarding 2차 | 분석 중 사장님 설명 시 |
| `{merchantId}-customer-patterns.md` | 주 고객층, 세그먼트, 멤버십 체계, 재방문 패턴 | Onboarding 2차 | 분석 중 사장님 언급 시 |
| `{merchantId}-operations.md` | 피크시간, 영업시간, 배달 비중, 날씨 민감도, 시즌성, 이벤트 | Onboarding 3차 | 자주 (자동 포착) |
| `{merchantId}-insights.md` | 분석 중 발견된 ad-hoc insight (append-only, 타임스탬프) | 자동 포착 시 자동 생성 | 매번 append |

## 기본 호출 (Tool)

```bash
# 조회 (RAG 검색)
integration.sh "knowledge/search?collection=store-context&query=$merchantId&top_k=10"

# 생성/업데이트 (add는 새 문서, update는 기존 덮어쓰기)
integration.sh "knowledge-write/add" -d '{"collection":"store-context","filename":"425467-profile.md","content":"..."}'
integration.sh "knowledge-write/update" -d '{"collection":"store-context","filename":"425467-operations.md","content":"..."}'
```

## 시나리오 1 — Onboarding 인터뷰

**트리거:** 사장님이 POS 관련 처음 질문할 때 (`knowledge/search`로 `{merchantId}-profile.md` 조회 → 없으면 onboarding 모드).

**진행 원칙:**
- **한 번에 2~3질문만** (사장님 피로 방지)
- 답변 받으면 **즉시** 해당 KB 문서 초기 생성
- "나중에 할게요" 시 즉시 중단, 다음 대화에서 재개
- profile.md / menu-logic.md / customer-patterns.md 기본 셋 완료되면 `profile.md`에 `onboarded: YYYY-MM-DD` 기록

**1차 질문 (profile):**

> 사장님 분석 전에 매장 특성 몇 가지만 여쭐게요. 정확한 분석에 크게 도움돼요.
>
> 1. 업종이 어떻게 되세요? (카페 / 바 / 분식 / 레스토랑 / 기타)
> 2. 주 컨셉이나 강점이 뭐예요?
> 3. 위치는 어떤 상권이에요? (오피스가/대학가/주거지/관광지 등)

답변 수집 → `{merchantId}-profile.md` 생성:
```markdown
---
merchantId: 425467
merchantName: 앰프레소
onboarded: pending
---

## 업종
카페

## 컨셉
스페셜티 커피. 산미있는 원두와 고소한 원두 선택 가능.

## 위치
오피스가 (강남역 도보 5분)

## 규모
(pending)

## 주 타겟
(pending)
```

**2차 질문 (menu + customer, 사장님 1차 답변 확인 후):**

> 2가지만 더 여쭐게요:
>
> 1. 메뉴 옵션 중에 원가/재고에 크게 영향 주는 게 있나요? (예: 원두 종류, 사이즈업, 샷추가 등)
> 2. 주 고객층이 어떤 분들이세요? 단골 비중은 어느 정도세요?

답변 → `{merchantId}-menu-logic.md` + `{merchantId}-customer-patterns.md` 생성.

**3차 질문 (operations, 마지막):**

> 마지막으로 운영 관련 3가지만:
>
> 1. 피크시간이 언제세요?
> 2. 배달/포장 비중이 어느 정도세요?
> 3. 시즌이나 날씨에 매출 변화가 큰가요?

답변 → `{merchantId}-operations.md` 생성. profile.md의 `onboarded`를 오늘 날짜로 update.

**온보딩 완료 후에도 gap 발견 시 단발성 질문 가능** (예: 재고 분석하려는데 menu-logic.md에 원두 정보만 있고 식자재 정보 없음 → "원두 외에 재고 추적해야 할 재료 있으세요?" 1개만).

### Pending Questions — 중단/재개 UX

사장님이 인터뷰 중 "나중에 할게요" / "지금 바쁨" / 간단히 답 회피 시:

1. 그 시점까지 받은 답은 즉시 저장
2. 남은 질문을 `profile.md` 최상단 front-matter에 YAML list로 기록:
   ```markdown
   ---
   merchantId: 425467
   merchantName: 앰프레소
   onboarded: pending
   pending_questions:
     - "배달/포장 비중이 어느 정도세요?"
     - "시즌이나 날씨에 매출 변화가 큰가요?"
   interview_paused_at: 2026-04-19
   ---
   ```
3. **다음 POS 대화 시작 시** 반드시 profile.md 최상단 확인. `pending_questions`가 비어있지 않으면 분석 응답 직후 "그러고 보니, 지난번에 여쭤본 것 중 아직 못 들은 것 1개만 더 확인해도 될까요?" 한 번에 1개씩 재개
4. 사장님이 답하면 해당 질문을 `pending_questions`에서 제거 + 해당 문서 업데이트
5. 리스트가 비면 `onboarded: YYYY-MM-DD`로 완료 표시, `pending_questions` 키 제거

**원칙:** pending 상태여도 분석은 항상 가능 (반쪽 컨텍스트로도 일반론적 답변은 OK). 단, 정답이 아닌 "추정" 표시 붙이고, pending 정보가 분석 품질에 결정적이면 "이 질문만 답해주시면 더 정확해져요" 부드럽게 유도.

## 시나리오 2 — 자동 포착 (Meta-cognition)

**모든 POS 분석 응답 끝에 실행하되, 비용 최적화를 위해 2단계로 나눔.**

### 단계 0 — Pre-filter (빠른 판단, 무료)

meta-cognition full extraction 전에 **최근 사장님 메시지를 빠르게 스캔**. 아래 키워드/패턴이 **하나도 없으면 skip** (불필요한 추가 LLM 호출 방지):

**키워드 화이트리스트:**
- 1인칭 소유: `우리`, `저희`, `내`, `제`
- 조건 마커: `근데`, `사실`, `보통`, `원래`, `주로`
- 시간 패턴: `월`/`화`/`수`/`목`/`금`/`토`/`일`요일, `아침`, `점심`, `저녁`, `주말`, `평일`, `시즌`, `여름`, `겨울`, `봄`, `가을`, `장마`, `휴가철`
- 고객 세그먼트: `손님`, `고객`, `멤버`, `단골`, `회원`, `워크인`, `VIP`
- 메뉴/옵션 설명: `옵션`, `원두`, `샷`, `사이즈`, `Hot`, `Ice`, `추가`, `베리에이션`
- 운영/외부 사건: `이벤트`, `프로모션`, `할인`, `배달`, `포장`, `휴무`
- 교정 마커: `아니`, `사실은`, `그게 아니라`, `틀렸어`, `맞지 않아` (→ 시나리오 3로 라우팅)

**판단 순서:**
1. 최근 사장님 메시지(직전 1~2 turn만)를 본다
2. 위 키워드 매치 **0개** → meta-cognition full step 건너뛰기 (토큰 절약)
3. 1개 이상 매치 → 단계 1로 진행

### 단계 1 — Full Extraction (선택적, 매칭 시에만)

pre-filter 통과 시 실제 분석:
- "이 발화가 매장 고유 정보인지"
- "KB에 이미 있는 내용인지 (중복 skip)"
- "어느 문서의 어느 섹션인지"

### 포착 패턴

| 사장님 발화 | 감지 힌트 | write 대상 |
|------------|-----------|-----------|
| "우리 매장은 ~" | "우리" + 매장 서술 | profile.md 또는 operations.md |
| "근데 우리는 ~" | "근데" + 예외 조건 | operations.md |
| "주로 ~ 손님이세요" | "주로" + "손님/고객" | customer-patterns.md |
| "{요일}에는 ~" | 요일 + 패턴 | operations.md |
| "{메뉴}에 ~ 옵션 넣으면 ~" | 메뉴 + 옵션 + 결과 | menu-logic.md |
| "멤버 / 단골 ~" | 멤버십 관련 | customer-patterns.md |
| "배달이 ~%" | 배달 비중 | operations.md |
| "시즌 / 여름 / 겨울 / 비 오면 ~" | 시즌·날씨 | operations.md |

### write 방식

1. 해당 merchantId + 대상 문서 `knowledge/search`로 현재 내용 조회
2. 관련 섹션에 append (기존 내용 보존)
3. 새 insight면 `{merchantId}-insights.md`에 `- [YYYY-MM-DD] {내용} (맥락: {분석 주제})` append
4. `knowledge-write/update`로 저장 (update = 덮어쓰기이므로 기존 내용 통째로 포함해서 write)

**동시성:** `knowledge-write/add|update|delete`는 서버측에서 per-document Redis lock (15s TTL)을 사용. 다른 세션이 동시에 같은 문서를 쓰면 429 응답 (`Another write is in progress on this document`). 이 경우:
- 2~3초 후 1회 재시도
- 또 실패하면 포기하고 다음 턴에 시도 (insights는 나중에 적어도 손실 크지 않음)
- 사장님한테는 침묵 유지 (자동 포착은 조용히 실패해도 OK)

### 실행 조건

- 사장님이 매장 관련 **새로운** 정보를 발화했을 때만 (이미 KB에 있으면 skip)
- 분석 자체의 결과(매출 숫자 등)는 insights.md에 기록 금지 — POS tool이 언제든 재조회 가능
- Insights는 **tool로는 얻을 수 없는 맥락**만 기록 (사장님의 의도, 외부 이벤트, 매장 고유 로직)

## 시나리오 3 — 교정 피드백

**트리거:** 봇이 추측/분석 기반 답 → 사장님 "아니 그게 아니라 ~야" / "그 해석 틀렸어" / "사실은 ~"

**대응:**

1. 어떤 문서의 어느 부분을 교정해야 할지 판단
2. `knowledge/search`로 현재 내용 조회
3. 틀린 추측 섹션을 교정된 내용으로 교체
4. 문서 하단 `## 학습 기록` 섹션에 append:
   ```
   - [YYYY-MM-DD] 이전 추측: "X" / 교정: "Y" / 맥락: "{분석 주제}"
   ```
5. `knowledge-write/update`로 저장

이 "학습 기록"은 같은 실수 반복 방지. 매번 분석 시작 시 조회해서 유의.

## 시나리오 4 — 다른 POS 스킬에서 컨텍스트 조회

다른 POS 스킬(`pos-sales`, `pos-accounting`, `pos-inventory`, `pos-menu-strategy`, `pos-report`)이 분석 응답 생성 전 이 스킬을 **컨텍스트 read 목적**으로 호출.

```bash
# 분석 직전
CTX=$(integration.sh "knowledge/search?collection=store-context&query=$merchantId&top_k=10")
```

결과에서:
- 업종/컨셉 → 응답 톤, 강조 포인트 결정
- 메뉴 옵션 로직 → 재고/원가 해석에 반영
- 고객 세그먼트 → "멤버 vs 워크인" 같은 분석 자동 포함
- 운영 패턴 → 이상치 판단 기준 조정 (예: 금요일 저녁 매출 급등이 "이상치"가 아니라 "단골 모임" 맥락)

## 출력 톤

온보딩/교정은 **대화형 존댓말**. 사장님이 부담 안 느끼도록:
- "정확한 분석에 도움돼요" / "나중에 해도 괜찮아요" / "이거 기억해둘게요"
- 질문은 묶음(2~3개)으로, 각 질문은 짧고 구체적
- 온보딩 답변 받으면 "기억했어요, 감사해요" 한 줄로 확인 후 다음 분석으로 자연스럽게 전환

## 할 수 없는 것

- **매장 간 데이터 혼용 불가** — 항상 `{merchantId}-` 접두사로 분리
- **자동 포착이 100% 정확하진 않음** — 애매하면 건너뛰고, 확실한 패턴만 기록
- **개인정보 저장 금지** — 특정 고객 이름/연락처는 KB에 적지 않음. 세그먼트 수준만.
