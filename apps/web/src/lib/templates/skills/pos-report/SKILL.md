---
name: pos-report
description: POS 리포트 오케스트레이터 — 일간 브리핑(daily briefing) / 주간 리포트(weekly review) / 월간 마감(monthly closing) 템플릿 기반 리포트 생성 + 크론 자동 등록 + KB 아카이브. 사장님이 "일간 브리핑 매일 아침 9시" / "주간 리포트 만들어줘" / "월말 정리 자동으로" 같이 요청하면 이 스킬이 크론 등록, 템플릿 채우기, 채널 전송, 아카이브 저장까지 일괄 처리. 다른 POS 스킬(pos-sales, pos-accounting, pos-inventory, pos-menu-strategy)의 tool을 조합 호출하고, pos-store-context로 매장 컨텍스트 반영.
---

# POS 리포트

## 왜 이 스킬이 존재하나

사장님은 바쁘다. 매일 아침 "오늘 매출 얼마야" 묻기보다 자동으로 3줄 브리핑 받는 게 낫고, 주말엔 전주 종합 리뷰, 월초엔 전월 회계 마감이 자동으로 오면 좋다. 이 스킬이:
- 크론 등록 (openclaw cron)
- 3가지 리포트 템플릿 (daily / weekly / monthly)
- 다른 POS 스킬 조합 호출
- 매장 컨텍스트 반영 (pos-store-context)
- 채널 전송 + KB 저장
를 일괄 오케스트레이션한다.

## 절대 규칙

1. **크론 등록 전 중복 확인.** `openclaw cron list --json`으로 같은 이름 있으면 "이미 있어요. 시간 바꾸실래요?" 질문
2. **크론 발동 시 채널 이름은 크론 등록 시점의 채널**. 사장님이 #general에서 요청했으면 그 채널에 전송
3. **리포트 생성 전 반드시 `pos-store-context`로 매장 컨텍스트 read**. 업종에 따라 템플릿 슬롯 다르게 채움
4. **생성한 리포트는 KB 컬렉션 `store-reports`에 저장** (`{merchantId}-{type}-{date}.md`)
5. **merchantId는 `tossplace/my-merchants` 기준**. MEMORY 신뢰 금지
6. **`integration.sh`는 PATH CLI 명령어** — 파일 다루기 금지. `system.run ["sh", "-c", "integration.sh '...'"]` 로만 실행. `not found` 시 절대경로 `/home/ocuser/.openclaw/bin/integration.sh` fallback. 조회 실패 시 "시뮬레이션" 생성 금지, 재시도 or 안내.

## 크론 등록

**기본 스케줄:**

| 리포트 타입 | 기본 cron | 기본 name |
|------------|-----------|-----------|
| daily briefing | `0 9 * * *` (매일 09:00 KST) | `pos-daily-briefing` |
| weekly review | `0 9 * * 6` (토요일 09:00 KST) | `pos-weekly-review` |
| monthly closing | `0 10 1 * *` (매월 1일 10:00 KST) | `pos-monthly-closing` |

**등록 예시 (사장님 "일간 브리핑 매일 아침 9시"):**

```bash
# 1. 중복 확인
openclaw cron list --json | grep pos-daily-briefing

# 2. 없으면 등록 (채널명은 현재 대화 채널)
openclaw cron add \
  --name "pos-daily-briefing" \
  --cron "0 9 * * *" \
  --tz "Asia/Seoul" \
  --message "skills/pos-report/SKILL.md의 'daily briefing 실행 프로토콜'을 따라 어제 데이터 기준으로 리포트를 생성하고 '#${CURRENT_CHANNEL}' 채널에 전송하세요. merchantId는 my-merchants로 확인." \
  --announce \
  --session isolated
```

사장님이 시간 바꾸고 싶어하면 `--cron` 값만 변경. 요일/시간 자연어 해석해서 cron expression 생성.

## Daily Briefing 실행 프로토콜

**목표:** 3~5줄, 핵심만.

**Tool 시퀀스:**

1. `integration.sh "tossplace/my-merchants"` → merchantId 확정
2. `integration.sh "knowledge/search?collection=store-context&query=$merchantId&top_k=10"` → 컨텍스트
3. `integration.sh "tossplace/sales-summary?merchantId=$mid&period=yesterday"` → 어제 매출
4. `integration.sh "tossplace/anomaly-detect?merchantId=$mid&lookbackDays=30&threshold=2.0"` → 이상치
5. (선택) `integration.sh "tossplace/top-items?merchantId=$mid&period=yesterday&limit=3"` → 인기 메뉴
6. 업종이 카페면 `integration.sh "tossplace/weather-sales?merchantId=$mid&days=3"` + web-search로 오늘 날씨
7. 업종이 바/주점이면 `integration.sh "tossplace/dine-ratio?merchantId=$mid&period=yesterday"` → 멤버 비중 추정

**템플릿 (기본):**

```
사장님, 어제 매출 {총매출}이에요. {전일_또는_전주_비교_결과}.
{인기_메뉴_1줄_또는_특이사항}
{매장_컨텍스트_기반_오늘_팁}
```

**카페 예시:**

```
사장님, 어제 매출 482,000원이에요. 평소 수준이에요.
아메리카노(38잔)이 1위였고, 아이스 비율 68%였어요.
오늘 최저 8도 예보 — 따뜻한 메뉴 비중 올릴 재료 여유 있는지 확인해보세요.
```

**위스키바 예시:**

```
사장님, 어제 매출 1,280,000원이에요. 주중 평균보다 18% 높아요.
주문 42건 중 멤버 비중 절반 이상으로 보여요. 객단가 30,476원.
금요일 단골 모임 전날이라 오늘도 수요 이어질 가능성 있어요.
```

**전송 + 저장:**

```bash
# 채널 전송 (chat-proxy app_channel_messages POST, 봇 내부 send 함수)
# → 등록 시점에 기록된 CHANNEL 사용

# KB 아카이브
integration.sh "knowledge-write/add" -d '{
  "collection": "store-reports",
  "filename": "425467-daily-20260418.md",
  "content": "...(리포트 전문)..."
}'
```

## Weekly Review 실행 프로토콜

**목표:** 5섹션 all-in-one.

**Tool 시퀀스:**

1. merchantId 확정 + 매장 컨텍스트 read
2. `sales-summary?period=week` + `sales-summary?period=2026-04-05,2026-04-11` (전주)
3. `order-trend?period=week&groupBy=day`
4. `menu-engineering?period=week` → BCG
5. `inventory-tracker?action=alert&merchantId=$mid`
6. `cashflow-report?period=week`
7. `card-fee-estimate?period=week`

**템플릿:**

```markdown
## 이번주 한눈에 ({from} ~ {to})
- 매출: {total} ({WoW_delta})
- 피크/한산: {peak_day} | {quiet_day}
- 객단가: {avg_order_value}

## 메뉴 BCG
- ⭐ Star: {stars_list}
- 💰 Cash Cow: {cash_cows_list}
- ❓ Puzzle: {puzzles_list}
- 🐕 Dog: {dogs_list}

## 재고 알림
{low_stock_items_bulleted or "재고 이슈 없음"}

## 현금흐름
- 카드 {card_pct}% / 현금 {cash_pct}% / 간편 {easypay_pct}%
- 카드수수료 예상 ~{estimated_fee}원

## 다음주 추천 액션
{context_aware_recommendations_2_to_3}
```

**"다음주 추천 액션" 생성 규칙:**
- 매장 컨텍스트 기반으로 사장님이 **실행 가능한** 2~3개 제안
- 예(카페): "Dog 메뉴 '허브티' 1주 빼고 반응 체크" / "주말 피크가 토 15시대 집중 — 샌드위치 프리컷 준비"
- 예(바): "금요일 단골 모임 혼잡 대응 — 17시 추가 스태프 검토" / "멤버 재방문 간격이 지난주보다 4일 늘어남 — 리마인드 메시지?"

## Monthly Closing 실행 프로토콜

**목표:** 회계 중심 deep dive.

**Tool 시퀀스:**

1. merchantId + 매장 컨텍스트
2. `tax-summary?period=month` (이미 지난 달)
3. `cashflow-report?period=month`
4. `monthly-pnl?months=3` (MoM 추세)
5. `discount-analysis?period=month`
6. `card-fee-estimate?period=month`

**템플릿:**

```markdown
## {YYYY}년 {M}월 마감

### 총괄
- 총매출: {total} (공급가액 {supply} + 부가세 {vat})
- 주요 비용: 카드수수료 {fee} / 할인 {discount}
- 전월비: {MoM_delta} | 일평균 {daily_avg}

### 세무 체크리스트
- [ ] 부가세 신고 기한: {vat_deadline} (D-{days_left})
- 현금영수증 발급: {cash_receipt_count}건 / {cash_receipt_amount}
- 주의사항: {tax_warnings_if_any}

### 3개월 추세
| 월 | 매출 | 주문수 | 객단가 | MoM |
|----|------|--------|--------|-----|
{3_row_table}

### 할인 분석
- 총 할인: {total_discount} ({discount_rate}% of 매출)
- 주요 할인 사유: {top_discount_reasons}

### 인사이트 (매장 컨텍스트 반영)
{context_aware_insights_2_to_3}
```

**"인사이트" 생성 규칙:**
- 매장 컨텍스트의 시즌성/고객패턴과 이번달 수치를 연결
- 예(카페): "기온 하락 구간(3째주)에 아이스 비율 58% → 42% 하락. 원두 발주 비중 조정 여지"
- 예(바): "멤버 재방문 간격 지난달보다 2.3일 짧아짐 — 신규 멤버 유입이 원인인지 기존 멤버 활성화인지 확인 필요"

## 사장님 수동 요청

사장님: "주간 리포트 지금 만들어줘" → 크론 없이 즉시 1회 생성 + 현재 채널 전송 + KB 저장.

사장님: "이번주말 브리핑 꺼줘" → `openclaw cron remove pos-daily-briefing` 후 확인.

## 시나리오 라우팅

| 사장님 발화 | skill 동작 |
|------------|-----------|
| "일간 브리핑 매일 아침 9시" | daily 크론 등록 |
| "주간 리포트 매주 토요일" | weekly 크론 등록 |
| "월간 마감 자동으로" | monthly 크론 등록 |
| "지금 리포트 만들어줘" | 맥락에 맞는 타입 즉시 실행 |
| "리포트 꺼줘" / "자동화 중단" | 해당 cron remove |
| "지난주 리포트 다시 보여줘" | KB `store-reports`에서 조회 |

## meta-cognition 훅

리포트 생성 후 사장님이 반응하면 (예: "이 메뉴 Dog 아닌데, 원가 보면 Cash Cow야"), `pos-store-context`의 **교정 피드백** 시나리오 트리거. 교정된 정보가 다음 리포트에 자동 반영되게.

**비용:** pre-filter 키워드(교정 마커 포함 — "아니", "사실은", "그게 아니라")가 사장님 반응에 **없으면** skip. 단순 "고마워" 같은 승인 반응엔 추가 처리 불필요.

## 할 수 없는 것

- **실시간 알림** → "매일 정해진 시간에 알려드려요"
- **SMS/이메일 전송** → 현재는 해당 채널 메시지만 (추후 채널 확장 가능)
- **그래프 이미지 생성** → markdown 숫자 나열 위주. 필요 시 `web-search`로 차트 라이브러리 검색은 별개
