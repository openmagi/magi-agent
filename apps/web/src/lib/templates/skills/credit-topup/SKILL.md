---
name: credit-topup
description: Use when your platform credits are low or you want to top up credits using your wallet's USDC. Checks credit balance and charges via x402 payment protocol.
metadata:
  author: openmagi
  version: "1.0"
---

# Credit Top-up (Agent Wallet → Platform Credits)

USDC 잔고로 Open Magi 플랫폼 크레딧을 충전한다. 크레딧이 부족하면 이 스킬로 자율 충전.

## 환율

1 USDC = 100 credit cents ($1.00)

## Step 1: 잔고 확인

> **INSTRUCTION:** 현재 플랫폼 크레딧 잔고를 조회하라.

```bash
curl -s -X GET "https://openmagi.ai/api/bots/$BOT_ID/credits/balance" \
  -H "Authorization: Bearer $GATEWAY_TOKEN"
```

> **EXPECTED:** `{"credits": 850, "plan": "pro", "renewalDate": "2026-04-01"}`
> - `credits`: 현재 잔고 (credit cents). 100 = $1.00
> - `plan`: 현재 구독 플랜
> - `renewalDate`: 다음 갱신일 (nullable)

## Step 2: 충전 필요성 판단

> **INSTRUCTION:** 잔고를 확인하고 충전이 필요한지 판단하라.

판단 기준 (자율):
- 잔고 < 500 ($5.00) → 충전 권장
- 잔고 < 100 ($1.00) → 충전 필수 (API 호출 불가 위험)
- 유저가 충전을 요청한 경우 → 무조건 충전

충전 금액 결정:
- 기본: $10 USDC (적당한 여유)
- 최소: $1 USDC
- 최대: $500 USDC
- **$10 이상 충전 시 유저에게 금액 확인 필수**

## Step 3: 충전 요청 → 402 수신

> **INSTRUCTION:** 충전 금액을 결정하고 topup 엔드포인트에 요청을 보내라.
> **RATIONALE:** 첫 요청은 402 Payment Required를 반환한다. 여기서 `payment-required` 헤더를 추출해야 한다.

```bash
TOPUP_AMOUNT=10
HEADERS_FILE=$(mktemp) && HTTP_CODE=$(curl -s -o /tmp/topup_body.json -w "%{http_code}" -D "$HEADERS_FILE" \
  -X POST "https://openmagi.ai/api/bots/$BOT_ID/credits/topup" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"amount\": $TOPUP_AMOUNT}") && echo "HTTP: $HTTP_CODE"
```

> **EXPECTED:** `HTTP: 402`
> **IF NOT 402:** 에러 메시지를 확인하고 유저에게 보고.

## Step 4: payment-required 헤더 추출

> **INSTRUCTION:** 402 응답에서 `payment-required` 헤더를 추출하라.

```bash
PAYMENT_HEADER=$(grep -i "^payment-required:" "$HEADERS_FILE" | sed 's/[^:]*: //' | tr -d '\r\n') && echo "Header length: ${#PAYMENT_HEADER}"
```

> **EXPECTED:** 길이 200 이상의 base64 문자열.

## Step 5: x402 결제 서명

> **INSTRUCTION:** x402-payment 스킬과 동일한 방식으로 서명을 받아라.

```bash
PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"paymentRequiredHeader\": \"$PAYMENT_HEADER\", \"targetUrl\": \"https://openmagi.ai/api/bots/$BOT_ID/credits/topup\"}") && echo "$PAY_RESULT"
```

> **EXPECTED:** `{"paymentHeader":"...","txHash":null,"amountUsdc":"10.00"}` — `txHash: null`은 정상.

## Step 6: 결제 증명으로 충전 완료

> **INSTRUCTION:** Step 5에서 받은 paymentHeader로 topup을 다시 요청하라. **반드시 같은 body를 포함하라.**

```bash
X_PAYMENT=$(echo "$PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.paymentHeader||'')") && \
curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/credits/topup" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Payment-Signature: $X_PAYMENT" \
  -d "{\"amount\": $TOPUP_AMOUNT}"
```

> **EXPECTED:** `{"success":true,"amountCents":1000,"amountUsdc":"10.00","txHash":"0x..."}`
> **IF 409:** 이미 처리된 결제. 정상.
> **IF 400/500:** 에러 메시지를 유저에게 보고.

## Step 7: 충전 확인

> **INSTRUCTION:** 잔고를 다시 조회하여 충전이 반영되었는지 확인하라.

```bash
curl -s -X GET "https://openmagi.ai/api/bots/$BOT_ID/credits/balance" \
  -H "Authorization: Bearer $GATEWAY_TOKEN"
```

## 규칙

1. **환경변수를 추측하지 마라** — `$BOT_ID`와 `$GATEWAY_TOKEN`은 자동 설정된 값을 그대로 사용.
2. **한 Step씩 실행** — 각 Step 실행 후 결과를 확인하고 다음으로 진행.
3. **$10 이상 충전 시 유저 확인** — 금액을 보여주고 동의를 받아라.
4. **스크립트 파일 금지** — 모든 명령을 인라인으로 실행.
5. **jq 대신 `node -e`** — JSON 파싱 패턴 사용.
6. **충전 실패 시 재시도 1회** — 2회 이상 실패 시 유저에게 보고.
