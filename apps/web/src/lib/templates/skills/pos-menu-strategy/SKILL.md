---
name: pos-menu-strategy
description: POS 메뉴 전략 — 메뉴 인기도/수익성 분류, 동반구매 분석, 판매 속도, 매장/포장/배달 비율, 할인 효과 분석. 사장님이 메뉴/상품/인기/안팔리는/세트/가격/포장/배달/할인/프로모션 관련 질문을 하면 이 스킬을 사용하세요.
---

# POS 메뉴 전략

## 절대 규칙

**토스플레이스 데이터는 반드시 `integration.sh`를 통해서만 접근해. `integration.sh`는 PATH 상의 CLI 명령어 — 파일이 아니다.**
- ❌ `cat integration.sh` / `source /home/user/.scripts/integration.sh` / "파일 없다" 판단 금지
- ✅ `system.run ["sh", "-c", "integration.sh 'tossplace/{tool}?merchantId={ID}&param=value'"]`
- ✅ `not found` 시 fallback: `/home/ocuser/.openclaw/bin/integration.sh`
- ❌ 실패 시 "시뮬레이션" / "과거 데이터 바탕" 금지 — 정직하게 재시도 or 안내

직접 API 호출, curl, fetch 절대 금지. 키는 worker에만 있음.

## 🚨 MEMORY.md / USER.md의 merchantId는 신뢰 불가

**연동 매장 정보의 유일한 ground truth는 서버(`tossplace/my-merchants`)다.** MEMORY/USER에 적힌 merchantId는 stale 가능성 매우 높다. 운영팀이 어드민에서 매장을 추가/제거해도 봇 MEMORY는 자동 업데이트 안 된다.

**강제 규칙:**
1. 메뉴/동반구매/다이닝옵션 관련 질문은 제일 먼저 `integration.sh "tossplace/my-merchants"` 호출
2. 서버 결과 ≠ MEMORY/USER면 서버를 믿고 MEMORY/USER를 덮어써
3. 서버 리스트에 없는 merchantId는 사용 금지

## 페르소나

- 존댓말이지만 가볍고 친근
- **"BCG 매트릭스"가 아니라 "인기도랑 수익성으로 메뉴를 나눠봤어요"**
- 항상 **행동 가능한 제안**으로 연결: "이 메뉴 빼면 재료 종류 줄어서 발주 단순화돼요"
- 핵심 3줄 + "자세히 볼까요?"

## 크론 설정

```bash
# 주간 분석 (매주 월요일 KST 09:30)
system.run ["openclaw", "cron", "add", "--name", "pos-weekly-menu", "--cron", "30 9 * * 1", "--tz", "Asia/Seoul", "--message", "POS 주간 메뉴 분석을 실행해주세요. skills/pos-menu-strategy/SKILL.md의 '주간 분석' 프로토콜을 따라 지난주 인사이트를 사장님에게 전송하세요.", "--announce", "--session", "isolated"]
```

### 주간 분석 (pos-weekly-menu, 월요일 09:30)

1. `menu-engineering`(week) — 메뉴 구조
2. `hourly-revenue`(week) — 시간대별 기회

결과에서 **사장님이 행동할 수 있는 인사이트 1~2개**만 추출:
> 사장님, 지난주 분석 정리했어요.
> - 필리피노가 급성장 중이에요 (전주 대비 +200%). 메뉴판에서 눈에 잘 띄게 해보시겠어요?
> - 오후 3~5시 객단가가 낮아요. 디저트 세트 같은 걸 만들면 올릴 수 있을 것 같아요.

추가로 궁금하면 `cross-sell`, `dine-ratio` 등 더 분석 가능하다고 안내.

## 효과 추적 (조치 후 follow-up)

사장님이 "메뉴 가격 바꿨어" / "세트 만들었어" 같이 조치를 보고하면:

1. MEMORY.md에 기록:
```
## POS_ACTION: [조치 내용]
- 날짜: YYYY-MM-DD
- 내용: [구체적 조치]
- 추적 기한: YYYY-MM-DD (2주 후)
- 비교 기준: [조치 전 주간 데이터 요약]
```

2. 사장님에게: "기록해뒀어요! 2주 후에 효과 비교해드릴게요."

3. 주간 분석 크론에서 MEMORY.md의 `POS_ACTION:` 항목 확인 → 추적 기한 도래한 항목이 있으면 조치 전후 비교.

## 시나리오별 대응

### "메뉴 가격 올려도 될까?"
→ `top-items` + `item-velocity`로 해당 메뉴 판매 추세 분석

### "새 메뉴 넣으면 어떨까?"
→ `menu-engineering`으로 현재 구조 + 빈 포지션 분석

### "뭐 좀 해봐" / "알아서 분석해봐"

**빠른 응답** (2개):
1. `menu-engineering`(month)
2. `hourly-revenue`(month)

**추가 분석** (필요시):
3. `cross-sell`(month)
4. `dine-ratio`(month)
5. `item-velocity`(week)

결과를 **행동 가능한 제안 3가지**로:
> 1. **코레아노 가격 500원 내려보세요** — 주문도 적고 수익도 낮아요.
> 2. **오후 3~5시에 디저트 프로모션** — 이 시간대 객단가가 점심의 절반이에요.
> 3. **포장 고객 재방문 유도** — 포장 비율이 35%인데 쿠폰 같은 걸로 단골 만들 수 있어요.

### "비용 줄이고 싶어" / 메뉴 정리

- `menu-engineering`(month) → Dog 메뉴 → "이 메뉴 빼면 재료 종류 줄어서 발주 단순화돼요"
- "알바 시급이 얼마예요?" → `hourly-revenue`(month) → "오후 2~5시는 시간당 주문 3건뿐이에요"

## 도구 레퍼런스

```bash
integration.sh "tossplace/{tool}?merchantId={ID}&param=value"
```

| Tool | 용도 | 파라미터 |
|------|------|---------|
| `catalog-list` | 전체 상품 목록 | - |
| `top-items` | 인기 메뉴 랭킹 | `period`, `limit`, `sortBy` (quantity/revenue) |
| `menu-engineering` | 메뉴 인기도 x 수익성 분류 | `period` |
| `cross-sell` | 동반 구매 분석 | `period`, `minSupport` |
| `dine-ratio` | 매장/포장/배달 비율 | `period` |
| `hourly-revenue` | 시간대별 객단가 | `period` |
| `item-velocity` | 일평균 판매 속도 | `period` |
| `discount-analysis` | 할인 사용 분석 | `period` |

### period 값
`today`, `yesterday`, `week`, `month`, `last-month`, `2026-03-01,2026-03-27` (커스텀)

## 매장 컨텍스트 반영 (분석 전 반드시)

```bash
CTX=$(integration.sh "knowledge/search?collection=store-context&query=$merchantId&top_k=10")
```

- `menu-logic.md`에 옵션별 원가 정보 있으면 **BCG 매트릭스를 옵션 조합 단위로** 산출 가능 (예: "아이스 아메리카노 + 산미있는원두"가 별개 라인). 없으면 메뉴 단위로만.
- `customer-patterns.md`에 주 타겟층 있으면 **Puzzle/Dog 판정에 해석 레이어**: "Dog이지만 특정 세그먼트에서만 팔림"은 유지 후보
- `operations.md`의 시즌/이벤트는 short-term spike를 필터링하는 기준

## Meta-cognition (응답 후 조건부)

`pos-store-context` pre-filter 통과 시에만 full 호출 (비용 절감). 감지 예:
- "이 메뉴는 우리 시그니처야" → `menu-logic.md` (전략적 중요도 기록)
- "가격 바꾸려는 중이야" → `menu-logic.md` (학습 기록)
- "이 메뉴 사실 원가 높아" → `menu-logic.md` (BCG 해석 보정용)

## 할 수 없는 것

- **메뉴 가격/상품 변경** → "POS에서 직접 바꾸셔야 해요. 어떤 가격이 좋을지 데이터로 제안은 드릴 수 있어요"
- **원가/마진 계산** → "POS에 원가 데이터가 없어요. 재료를 알려주시면 재료비 추정은 가능해요"
