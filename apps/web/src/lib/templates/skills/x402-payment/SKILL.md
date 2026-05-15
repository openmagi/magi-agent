---
name: x402-payment
description: Use when accessing external APIs or services that return HTTP 402 Payment Required. Handles automatic payment via x402 protocol using the agent's USDC wallet on Base chain.
metadata:
  author: openmagi
  version: "3.0"
---

# x402 Payment Protocol

HTTP 402 Payment Required 응답을 받았을 때 USDC 결제를 처리한다.

## x402 프로토콜 원리

x402는 **EIP-3009 authorization 기반** 결제 프로토콜이다.

```
너 → API 요청 → 402 + payment-required 헤더
  ↓
너 → Open Magi /x402/pay에 헤더 전달 → 서명된 paymentHeader 반환 (txHash: null은 정상)
  ↓
너 → 같은 API에 재요청 + Payment-Signature 헤더 → 200 + 데이터
  ↓
서버가 서명을 검증하고 on-chain settlement (transferWithAuthorization) 처리
```

**핵심:** 너는 트랜잭션을 만들지 않는다. 서버가 한다. `txHash: null`은 정상이다.

## Step 0: 환경변수 확인 (최초 1회)

> **INSTRUCTION:** 아래 명령을 실행하여 환경변수가 설정되어 있는지 확인하라.
> **RATIONALE:** `$BOT_ID`와 `$GATEWAY_TOKEN`은 pod 생성 시 자동으로 설정된 환경변수다. 직접 값을 추측하거나 하드코딩하면 안 된다.

```bash
echo "BOT_ID=$BOT_ID" && echo "GATEWAY_TOKEN length=${#GATEWAY_TOKEN}"
```

> **EXPECTED:** BOT_ID는 UUID 형태(예: `186bf3d7-7d00-...`), GATEWAY_TOKEN length는 36.
> **IF EMPTY:** 관리자에게 보고. 직접 값을 만들거나 추측하지 마라.

## Step 1: API 호출 → 402 감지

> **INSTRUCTION:** 대상 URL에 요청을 보내고 HTTP 상태 코드와 응답 헤더를 저장하라.
> **RATIONALE:** 402 응답의 `payment-required` 헤더에 결제 조건(금액, 수신자, nonce)이 인코딩되어 있다. 이것을 추출해야 서명을 요청할 수 있다.

```bash
HEADERS_FILE=$(mktemp) && HTTP_CODE=$(curl -s -o /tmp/x402_body.json -w "%{http_code}" -D "$HEADERS_FILE" "$TARGET_URL") && echo "HTTP: $HTTP_CODE"
```

> **EXPECTED:** `HTTP: 402`
> **IF NOT 402:** x402 결제가 필요없는 API다. 이 스킬을 사용하지 마라.

## Step 2: payment-required 헤더 추출

> **INSTRUCTION:** 응답 헤더에서 `payment-required` 값을 추출하라.
> **RATIONALE:** 이 헤더는 base64로 인코딩된 결제 사양(PaymentRequired)이다. Open Magi 서명 서비스에 전달해야 한다.

```bash
PAYMENT_HEADER=$(grep -i "^payment-required:" "$HEADERS_FILE" | sed 's/[^:]*: //' | tr -d '\r\n') && echo "Header length: ${#PAYMENT_HEADER}"
```

> **EXPECTED:** 길이 500 이상의 긴 base64 문자열.
> **IF LENGTH 0:** 서버가 payment-required 헤더를 포함하지 않았다. 중단.

## Step 3: Open Magi 서명 서비스로 결제 서명 요청

> **INSTRUCTION:** 추출한 payment-required 헤더를 Open Magi에 전달하여 서명을 받아라.
> **RATIONALE:** Open Magi가 너의 지갑 개인키로 EIP-3009 authorization을 서명한다. 너는 개인키를 직접 다루지 않는다. `$BOT_ID`와 `$GATEWAY_TOKEN`은 이미 설정된 환경변수이므로 그대로 사용하라.

```bash
PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"paymentRequiredHeader\": \"$PAYMENT_HEADER\", \"targetUrl\": \"$TARGET_URL\"}") && echo "$PAY_RESULT"
```

> **EXPECTED:** `{"paymentHeader":"...long base64...","txHash":null,"amountUsdc":"2.00"}` — `txHash: null`은 **정상**이다.
> **IF 401 Unauthorized:** `$GATEWAY_TOKEN`이 무효화됨. 관리자에게 보고. BOT_ID를 추측하거나 변경하지 마라.
> **IF 500 Error:** `detail` 필드를 읽고 관리자에게 보고.

## Step 4: 결제 증명으로 원래 요청 재시도

> **INSTRUCTION:** Step 3에서 받은 `paymentHeader`를 `Payment-Signature` 헤더에 넣어 원래 요청을 **동일하게** 다시 보내라.
> **RATIONALE:** 서버는 이 서명을 검증한 후 on-chain에서 `transferWithAuthorization`을 호출하여 USDC를 이체하고, 요청을 처리한다. 이 Step이 결제를 완료시키는 핵심이다.

```bash
X_PAYMENT=$(echo "$PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.paymentHeader||'')") && \
curl -s "$TARGET_URL" -H "Payment-Signature: $X_PAYMENT"
```

> **CRITICAL:** 헤더 이름은 반드시 `Payment-Signature`다. `X-PAYMENT`이 아니다.
> **EXPECTED:** HTTP 200 + 원래 요청한 데이터.
> **IF STILL 402:** 서명이 만료되었을 수 있다. Step 1부터 다시 시작.

## 규칙

1. **환경변수를 추측하지 마라** — `$BOT_ID`와 `$GATEWAY_TOKEN`은 자동 설정된 값을 그대로 사용.
2. **한 Step씩 실행** — 각 Step 실행 후 결과를 확인하고 다음으로 진행.
3. **스크립트 파일 금지** — 모든 명령을 인라인으로 실행.
4. **jq 대신 `node -e`** — JSON 파싱 패턴: `echo '{}' | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.key)"`
5. **402만 처리** — 401, 403 등 다른 에러에는 사용하지 않음.
6. **$10 이상 결제 시 유저에게 확인** — 금액을 반드시 보여주고 동의를 받아라.
7. **헤더 이름은 `Payment-Signature`** — 대소문자 주의. `X-PAYMENT` 절대 사용 금지.
