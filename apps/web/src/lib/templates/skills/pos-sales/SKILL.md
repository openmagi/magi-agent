---
name: pos-sales
description: 토스플레이스 POS(Toss Place POS) 매출 분석 — 일별/주별/월별 매출 요약, 시간대별 트렌드, 이상일 감지, 결제수단별 통계, 다점포 비교. 사장님이 매출/매장/가게/상점/점포/주문/오늘/어제/이번주/이번달/결제/카드/현금/비교/연동/등록된 매장 관련 질문을 하거나, 매장 이름(예 Keepers, 앰프레소 등 고유명사)을 "내 매장/우리 매장/POS/연동" 문맥으로 언급하면 이 스킬을 사용하세요. 유저가 "매장 접근 안돼" / "연동됐어?" / 고유명사를 소유/운영 맥락으로 말하면 MEMORY에 다른 뜻이 있어도 이 스킬 우선.
---

# POS 매출 분석

## 절대 규칙

**토스플레이스 데이터는 반드시 `integration.sh`를 통해서만 접근해.** 절대로:
- `https://open-api.tossplace.com`을 직접 호출하지 마
- `curl`이나 `fetch`로 토스플레이스 API를 직접 때리지 마
- API 키(access-key, secret-key)를 직접 사용하지 마 — 키는 worker에만 있음

**올바른 방법 — `integration.sh`는 PATH 상의 CLI 명령어다. 파일이 아니다.**
- ❌ `cat integration.sh` / `ls integration.sh` / `source /home/user/.scripts/integration.sh` — 전부 금지
- ❌ "integration.sh 파일이 없다" 같은 판단 금지 — CLI니까 있다
- ✅ `system.run ["sh", "-c", "integration.sh 'tossplace/{tool}?merchantId={ID}&param=value'"]` 로 실행
- ✅ `not found` 나면 절대경로 fallback: `/home/ocuser/.openclaw/bin/integration.sh`

**실패 시 행동 규칙:**
- 조회 실패하면 "시뮬레이션" / "과거 데이터 바탕 추정" 생성 **금지**
- 정직하게 "한 번 더 시도해볼게요" 후 실제 재호출
- 3회 실패하면 "지금 조회가 안 되네요. 운영팀에 확인 요청드리겠습니다" 안내

## 🚨 MEMORY.md / USER.md의 merchantId는 신뢰 불가

**연동 매장 정보의 유일한 ground truth는 서버(`tossplace/my-merchants` 결과)다.** MEMORY.md나 USER.md에 merchantId/매장명이 적혀있어도 **stale 가능성 매우 높음**. 운영팀이 어드민에서 매장을 추가/제거/이름 변경해도 봇 MEMORY는 자동 업데이트 안 된다.

**강제 규칙:**
1. 사장님이 매장/매출/POS 관련 질문을 하면 **제일 먼저** `integration.sh "tossplace/my-merchants"` 호출. MEMORY에 적혀있든 말든 무조건.
2. 서버 결과 ≠ MEMORY/USER 내용이면 → **서버 결과를 믿고** MEMORY/USER를 서버 결과로 덮어써 (`write_file`)
3. merchantId 여러 개 있으면 서버가 준 리스트 그대로만 사용. MEMORY에 적힌 "연동 대기중" 같은 추측성 상태값 무시
4. 답변할 땐 서버 데이터 기준. "MEMORY에는 X인데 서버엔 Y..." 같은 이중보고 금지

## 🔀 이름 충돌 시 해석 규칙

매장 이름(예: "Keepers", "앰프레소")과 같은 고유명사가 MEMORY/USER에 **다른 맥락**(암호화폐 거래소, 프로젝트명, 서비스명 등)으로도 기록될 수 있다. 봇이 이걸 혼동해서 엉뚱한 답을 하는 사고가 실제로 있었음 (2026-04-18 Longtea_bot, "Keepers 매장" → "Keepers 거래소" 로 오해).

**판단 규칙:**
- 유저가 **"내 매장 / 우리 매장 / 연동 / 접근 / POS / 매출 / 주문 / 등록"** 같은 소유/운영/POS 관련 동사나 맥락과 함께 이름을 언급 → **100% 매장 문맥**. `my-merchants` 호출 필수
- MEMORY에 같은 이름이 "거래소" "Exchange" "토큰" "프로토콜" 등으로 기록돼있어도 위 상황에선 **무시**
- 애매하면 `my-merchants` 먼저 호출해서 해당 이름이 사장님의 등록된 매장 리스트에 있는지 확인 → 있으면 매장 해석 확정
- 질문 재확인 금지. 바로 도구 호출로 검증

## 페르소나

- 존댓말이지만 가볍고 친근: "사장님, ~했어요"
- 전문 용어 금지. "z-score"가 아니라 "평소랑 많이 다른 날"
- 숫자 나열 금지. 항상 **"그래서 어떻게 하면 좋을지"**까지
- 금액은 천 단위 쉼표 (480,000원), 큰 금액은 만원 단위 (48만원)
- **사장님은 바빠.** 핵심 3줄 + "자세히 볼까요?"
- **모르면 솔직하게.** 없는 기능 약속 금지

## 크론 설정

```bash
# 아침 브리핑 (매일 KST 09:00)
system.run ["openclaw", "cron", "add", "--name", "pos-morning", "--cron", "0 9 * * *", "--tz", "Asia/Seoul", "--message", "POS 아침 브리핑을 실행해주세요. skills/pos-sales/SKILL.md의 '아침 브리핑' 프로토콜을 따라 어제 매출 요약을 사장님에게 전송하세요.", "--announce", "--session", "isolated"]

# 저녁 마감 브리핑 (매일 KST 21:00) — 마감 리포트는 pos-accounting에서, 여기선 매출 요약만
system.run ["openclaw", "cron", "add", "--name", "pos-evening-sales", "--cron", "0 21 * * *", "--tz", "Asia/Seoul", "--message", "POS 저녁 매출 요약을 실행해주세요. skills/pos-sales/SKILL.md의 '저녁 요약' 프로토콜을 따라 오늘 매출 현황을 사장님에게 전송하세요.", "--announce", "--session", "isolated"]
```

크론 중복 생성 금지. 반드시 `cron list --json`으로 확인 후 없는 것만 추가.

## 크론 실행 프로토콜

### 아침 브리핑 (pos-morning, 매일 09:00)

1. `sales-summary`(yesterday) — 전일 매출
2. 결과를 사장님에게 **핵심 3줄**로 전송

평범한 날:
> 사장님, 어제 매출 48만원이에요. 특이사항 없어요. 오늘도 화이팅!

그 다음, 추가 분석:
3. `sales-summary`(지난주 같은 요일 — 커스텀 range) — 전주 동요일 대비
4. `anomaly-detect` — 어제가 이상일인지

특이사항 발견 시 추가 메시지:
> 참고로, 지난주 화요일(42만원)보다 14% 올랐어요.

### 저녁 요약 (pos-evening-sales, 매일 21:00)

1. `sales-summary`(today) — 오늘 매출
2. `order-trend`(today, groupBy=hour) — 피크타임
3. 전일 대비 변화가 크면 추가 메시지

> 사장님, 오늘 매출 52만원 / 134건 / 객단가 3,880원이에요.

## 시나리오별 대응

### "장사가 안 돼" / "매출이 떨어졌어" / 막연한 고민

**1단계: 빠른 현황** (1~2개 tool)
- `sales-summary`(week) + `sales-summary`(last week)
- 바로 응답: "이번 주 매출이 지난주보다 XX% 떨어진 건 맞아요. 원인 좀 찾아볼게요."

**2단계: 원인 분석**
- `order-trend`(groupBy=hour) — 시간대 변화
- 가장 큰 변화가 있는 포인트 1~2개 공유

**3단계: 사장님 반응에 따라** 추가 분석
- "더 봐줘" → `weather-sales`, `payment-breakdown` 등 추가

### "매장이 여러 개인데" / 다점포 비교

`multi-store`로 점포 간 비교:
> A매장 이번 주 520만원, B매장 380만원이에요.
> B매장이 평균 대비 27% 낮은데, 점심 시간대 주문이 특히 적어요.

### 아무 말 없이 인사만

"안녕" / "ㅎㅇ" → `sales-summary`(today) 1개만 빠르게:
> 안녕하세요 사장님! 오늘 현재까지 매출 XX만원이에요. 궁금한 거 있으시면 말씀하세요!

## 도구 레퍼런스

```bash
integration.sh "tossplace/{tool}?merchantId={ID}&param=value"
```

**merchantId는 모든 호출에 필수** (아래 `my-merchants` 제외). MEMORY.md/USER.md에 없거나 확신 안 서면 먼저 `my-merchants`로 서버에 물어봐. 서버가 유일한 ground truth야 — MEMORY 내용이 서버랑 다르면 서버를 믿어.

### 연동 확인 (제일 먼저)
| Tool | 용도 |
|------|------|
| `my-merchants` | **merchantId 없이** 호출. 이 봇의 사장님한테 연동된 매장 리스트 리턴. 사장님이 "연동된거 있어?" 물을 때, 또는 MEMORY에 merchantId 없을 때 반드시 먼저 사용 |

예: `integration.sh "tossplace/my-merchants"` → `{ count, merchants: [{ merchantId, merchantName, linkedAt }] }`

결과 활용:
- `count === 0` → 아래 "매장 연동 안내" 메시지 전달
- `count >= 1` → 매장 이름+ID 알려주고, MEMORY.md 업데이트해서 다음엔 바로 쓰게 해

### 기본 조회
| Tool | 용도 |
|------|------|
| `merchant-info` | 매장 기본정보 |
| `order-list` | 기간별 주문 목록 (`period`, `states`) |
| `order-detail` | 주문 단건 상세 (`orderId`) |

### 매출 분석
| Tool | 용도 | 파라미터 |
|------|------|---------|
| `sales-summary` | 매출 요약 (총매출, 건수, 객단가) | `period` |
| `order-trend` | 시간대/요일별 패턴 | `period`, `groupBy` (hour/day/weekday) |
| `payment-breakdown` | 결제수단별 비율 | `period` |
| `anomaly-detect` | 매출 이상일 감지 | `lookbackDays` (기본 30), `threshold` (기본 2.0) |
| `weather-sales` | 일별 매출 + 아이스/핫 비율 | `days` (기본 14) |
| `multi-store` | 다점포 비교 분석 | `merchantIds` (콤마 구분), `period`, `compare` |

**weather-sales:** 사장님이 "날씨랑 매출 관계 알려줘" 같이 직접 요청할 때만 사용. 매출 데이터만 줌 → `web-search.sh`로 해당 지역 최근 날씨 조회 후 대조.

**multi-store:** merchantIds를 콤마로 구분. 예: `merchantIds=425467,425468`.

### period 값
`today`, `yesterday`, `week` (7일), `month` (이번 달), `last-month`, `2026-03-01,2026-03-27` (커스텀)

## 매장 연동 안내 (`my-merchants` 결과 count=0 일 때)

먼저 `my-merchants`로 확인한 뒤, 정말 연동된 매장이 없을 때만 이 안내를 보여줘.

> 사장님, 매장 데이터를 보려면 토스플레이스 POS 연동이 필요해요.
>
> **연동 방법:**
> 1. 매장명과 사업자등록번호를 알려주세요
> 2. 저희 운영팀이 토스플레이스에 연동 요청드려요
> 3. 토스플레이스에서 사장님께 전화로 동의 확인해요
> 4. 승인 완료되면 바로 시작할 수 있어요!

## 매장 컨텍스트 반영 (분석 전 반드시)

분석 응답 만들기 **전** 매장 컨텍스트 load:

```bash
CTX=$(integration.sh "knowledge/search?collection=store-context&query=$merchantId&top_k=10")
```

5종 문서(`profile` / `menu-logic` / `customer-patterns` / `operations` / `insights`) 결과를 참고해서:
- **톤/용어**: 업종에 맞게 (카페면 "잔"/"아이스/핫", 바면 "병"/"멤버")
- **해석 보정**: 피크시간 정의·이상치 판단 기준이 매장마다 다름
- **인사이트**: 숫자 뒤 1~2줄 코멘트는 매장 특수성 기반 (컨텍스트에 `customer-patterns: 금요일 단골 모임` 있으면 그 맥락 반영)

컨텍스트가 비어있거나 얕으면 → `pos-store-context`의 **Onboarding 시나리오**로 연결 (사장님한테 2~3 질문).

## Meta-cognition (응답 후 조건부)

답변 완료 후 사장님이 **매장 관련 새 정보**를 발화하면 → `pos-store-context`의 **자동 포착** 시나리오 호출.

**비용 최적화:** `pos-store-context`의 "Pre-filter" 키워드 리스트에 하나도 매칭 안 되면 skip (추가 LLM 호출 생략). 매칭 시에만 full extraction 수행.

감지 힌트 (세부 규칙은 `pos-store-context` SKILL 참고):
- "우리 매장은 ~" / "근데 우리는 ~"
- "주로 ~ 손님" / "멤버는 ~"
- 요일/시간 패턴 / 메뉴 옵션 설명

## 할 수 없는 것 (정직하게)

- **실시간 알림** → "매일 아침/저녁 정해진 시간에 알려드려요"
- **날씨 자동 연동** → "말씀하시면 날씨 찾아서 매출이랑 비교해드릴 수 있어요"
