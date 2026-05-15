---
name: agentcard
description: Use when the user asks to create a virtual Visa card, make online purchases, pay for SaaS/API subscriptions, or needs a payment card for any online transaction. Also use when the user mentions "AgentCard", "virtual card", "Visa card", "prepaid card", "buy something online", or needs to pay for something that requires a credit/debit card (not x402/crypto). For x402 protocol payments, use the x402-payment skill instead.
metadata:
  author: openmagi
  version: "1.0"
---

# AgentCard — 가상 Visa 카드

> **x402 결제와 AgentCard는 다른 시스템이다.**
>
> | 구분 | x402 결제 (Privy Wallet) | AgentCard (이 스킬) |
> |------|-------------------------|---------------------|
> | 결제 수단 | USDC on Base chain | 가상 Visa 카드 (USD) |
> | 사용처 | x402 프로토콜 지원 서비스만 | **Visa 받는 곳 어디든** |
> | 충전 | 지갑 USDC 잔액 | 유저가 Stripe로 카드 충전 |
> | 비용 | 가스비 + 서비스 요금 | 충전 금액만 (수수료 없음, 베타) |
> | 스킬 | `x402-payment` | `agentcard` (이 스킬) |
>
> 유저가 "API 키 사줘", "SaaS 구독해줘", "도메인 구매해줘" 등 **일반 온라인 결제**를 요청하면 **이 스킬**을 사용하라.
> 유저가 x402, AgentMail, 온체인 결제를 언급하면 `x402-payment` 스킬을 사용하라.

선불 가상 Visa 카드를 발급하여 온라인 결제에 사용한다. Visa가 되는 곳이면 어디든 가능하다.

## Step 0: 환경변수 확인 (최초 1회)

> **INSTRUCTION:** 환경변수를 확인하라.
> **RATIONALE:** `$BOT_EMAIL_ADDRESS`는 magic link 인증에, `$GATEWAY_TOKEN`은 인박스 읽기에 필요하다.

```bash
echo "BOT_EMAIL=$BOT_EMAIL_ADDRESS" && echo "GATEWAY_TOKEN length=${#GATEWAY_TOKEN}"
```

> **EXPECTED:** BOT_EMAIL은 `xxx@mail.openmagi.ai` 형태, GATEWAY_TOKEN length는 36.
> **IF BOT_EMAIL EMPTY:** 유저에게 Settings에서 이메일을 활성화하라고 안내. 이메일 없이는 AgentCard 가입 불가.

## Step 1: agent-cards CLI 설치 확인

> **INSTRUCTION:** CLI가 설치되어 있는지 확인하고, 없으면 설치하라.
> **RATIONALE:** 워크스페이스에 로컬 설치한다. 루트 파일시스템은 read-only이므로 글로벌 설치(`-g`)는 불가능하다.

```bash
if command -v agent-cards &>/dev/null; then
  echo "agent-cards already installed: $(agent-cards --version 2>/dev/null || echo 'unknown')"
elif [ -f "$HOME/node_modules/.bin/agent-cards" ]; then
  export PATH="$HOME/node_modules/.bin:$PATH"
  echo "agent-cards found in workspace: $(agent-cards --version 2>/dev/null || echo 'unknown')"
else
  cd "$HOME" && npm install agent-cards 2>&1 | tail -3
  export PATH="$HOME/node_modules/.bin:$PATH"
  echo "Installed: $(agent-cards --version 2>/dev/null || echo 'check install')"
fi
```

> **EXPECTED:** 버전 출력 (예: `0.4.5`).
> **IF npm install 실패:** `npx agent-cards --version`으로 대체 시도.

## Step 2: 인증 상태 확인

> **INSTRUCTION:** 이미 로그인되어 있는지 확인하라.
> **RATIONALE:** JWT가 이미 저장되어 있으면 signup을 건너뛴다. JWT는 30일 유효.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards whoami 2>&1
```

> **IF 이메일 주소 출력:** 이미 인증됨. **Step 5로 건너뛰어라.**
> **IF "not logged in" 또는 에러:** Step 3으로 진행.

## Step 3: AgentCard 가입 (최초 1회)

> **INSTRUCTION:** `agent-cards signup`을 실행하고 봇의 이메일 주소를 입력하라.
> **RATIONALE:** magic link가 봇의 플랫폼 이메일로 전송된다. 봇은 이 이메일을 읽을 수 있다.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
echo "$BOT_EMAIL_ADDRESS" | agent-cards signup 2>&1 &
SIGNUP_PID=$!
echo "Signup started (PID: $SIGNUP_PID), waiting for magic link email..."
sleep 8
```

> **EXPECTED:** "Enter your email" 후 magic link 이메일 전송됨.
> **RATIONALE:** 백그라운드로 실행하는 이유는 CLI가 magic link 클릭을 대기(polling)하기 때문이다. 봇이 이메일을 읽고 링크를 클릭해야 인증이 완료된다.

## Step 4: Magic Link 읽기 + 클릭

> **INSTRUCTION:** 플랫폼 이메일 인박스에서 AgentCard magic link를 찾아 클릭하라.
> **RATIONALE:** AgentCard가 보낸 이메일에 인증 링크가 포함되어 있다. 이 링크를 curl로 요청하면 서버에서 JWT를 발급하고, 백그라운드의 CLI가 이를 감지하여 로그인을 완료한다.

```bash
MAGIC_LINK=$(curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-email/inbox?limit=5&offset=0" | \
  node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
const msgs=JSON.parse(d);
for(const m of (Array.isArray(msgs)?msgs:msgs.messages||[])){
  const body=(m.text||'')+(m.html||'');
  const match=body.match(/https:\/\/[^\s\"'<>]+agentcard[^\s\"'<>]*/i);
  if(match){console.log(match[0]);process.exit(0);}
}
console.error('No magic link found');process.exit(1);
") && echo "Found magic link: ${MAGIC_LINK:0:60}..."
```

> **EXPECTED:** `Found magic link: https://...agentcard...`
> **IF "No magic link found":** 5초 더 기다린 후 재시도. 2번 실패하면 유저에게 이메일을 확인하라고 요청.

```bash
curl -s -L "$MAGIC_LINK" > /dev/null 2>&1 && echo "Magic link clicked successfully"
```

> **EXPECTED:** `Magic link clicked successfully`

```bash
sleep 3 && wait $SIGNUP_PID 2>/dev/null
agent-cards whoami 2>&1
```

> **EXPECTED:** 봇의 이메일 주소 출력.
> **IF 실패:** `agent-cards login`으로 재시도하고 Step 4를 반복.

## Step 4-ALT: 수동 인증 (자동 인증 실패 시)

> **INSTRUCTION:** 자동 인증이 실패하면, 유저에게 직접 인증을 요청하라.
> **RATIONALE:** 이메일 인박스 접근이 안 되거나, magic link 형식이 변경되었을 수 있다.

유저에게 다음을 안내하라:

```
AgentCard 자동 인증에 실패했습니다. 수동으로 진행해주세요:

1. 터미널에서: npm install -g agent-cards && agent-cards signup
2. 이메일로 받은 magic link를 클릭하세요
3. 인증 완료 후, 아래 명령어를 실행해서 JWT를 알려주세요:
   cat ~/.agent-cards/config.json
```

유저가 JWT를 제공하면:

```bash
mkdir -p ~/.agent-cards && echo '{"token":"유저가_제공한_JWT"}' > ~/.agent-cards/config.json && chmod 600 ~/.agent-cards/config.json
agent-cards whoami 2>&1
```

## Step 5: MEMORY.md에 AgentCard 설정 기록

> **INSTRUCTION:** AgentCard 가입 완료를 MEMORY.md에 기록하라.
> **RATIONALE:** 다음 세션에서 재인증 없이 바로 사용할 수 있도록 한다. JWT 자체는 저장하지 않는다 (CLI가 관리).

```bash
cat >> MEMORY.md << 'EOFMEM'

## AgentCard (Virtual Visa)
- Status: Authenticated
- Setup date: $(date +%Y-%m-%d)
- Auth: JWT stored in ~/.agent-cards/ (30-day expiry, re-login with `agent-cards login`)
- Cards: Use `agent-cards cards list` to see all cards
- Create: `agent-cards cards create --amount <cents>` → Stripe checkout URL → user pays
EOFMEM
echo "Saved to MEMORY.md"
```

---

## 카드 생성 — 유저가 요청할 때

### Card Step 1: 카드 발급 + Stripe 결제 URL

> **INSTRUCTION:** 유저가 요청한 금액으로 카드를 생성하라.
> **RATIONALE:** CLI가 Stripe 체크아웃 URL을 반환한다. 유저가 이 URL에서 결제를 완료해야 카드가 활성화된다.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards cards create --amount <금액_달러> 2>&1
```

> **EXPECTED:** Stripe 체크아웃 URL 또는 카드 상세 정보 출력.
> **IMPORTANT:** 유저에게 결제 URL을 즉시 전달하라. 예: "아래 링크에서 $50을 결제해주세요: https://checkout.stripe.com/..."
> **IF 인증 에러:** `agent-cards login` 실행 후 Step 4 반복.

### Card Step 2: 결제 완료 확인

> **INSTRUCTION:** 유저가 결제를 완료했는지 확인하라.
> **RATIONALE:** 결제가 완료되면 카드가 활성화되고, 전체 카드 정보(PAN, CVV, 만료일)를 조회할 수 있다.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards cards list 2>&1
```

> **EXPECTED:** 카드 목록에 새 카드가 `active` 상태로 표시.
> **IF "pending" 상태:** 유저에게 결제를 완료했는지 물어보라. 5초 후 재시도.

### Card Step 3: 카드 상세 조회

> **INSTRUCTION:** 결제가 필요할 때 카드 상세를 조회하라.
> **RATIONALE:** PAN(카드번호), CVV, 만료일은 필요할 때만 조회한다.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards cards details <card-id> 2>&1
```

> **EXPECTED:** 전체 카드번호(16자리), CVV(3자리), 만료일 출력.
> **보안:** 카드 정보를 MEMORY.md에 저장하지 마라. 필요할 때마다 조회하라.

---

## 카드 사용 — 온라인 결제

카드 정보를 조회한 후, 결제 대상 사이트에서 카드를 사용한다.

### 직접 결제 (웹 폼)

카드 정보를 유저에게 전달하거나, 봇이 직접 입력:

```
카드번호: 4242 XXXX XXXX XXXX
만료일: 12/27
CVV: 123
이름: AgentCard Holder (또는 유저 이름)
```

### API 결제 (프로그래매틱)

API 키 구매 등 프로그래매틱 결제가 가능한 경우, curl로 직접 결제:

```bash
# 예: 서비스의 결제 API
curl -s -X POST "https://api.example.com/v1/payment" \
  -H "Content-Type: application/json" \
  -d '{"card_number":"<PAN>","exp_month":<MM>,"exp_year":<YY>,"cvc":"<CVV>","amount":<금액>}'
```

### x402 결제 (AgentCard 경유)

x402 프로토콜 지원 서비스에 AgentCard로 결제 (Privy 지갑 대신):

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards x402-fetch --url "https://x402-service.com/api/resource" --card-id <card-id> 2>&1
```

> **NOTE:** x402 결제는 보통 Privy 지갑(`x402-payment` 스킬)을 사용한다. AgentCard의 x402_fetch는 Privy 지갑에 USDC가 부족하거나, 유저가 카드 결제를 선호할 때 사용하라.

---

## 카드 관리

### 잔액 확인

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards balance <card-id> 2>&1
```

### 모든 카드 목록

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards cards list 2>&1
```

### 카드 닫기

> **INSTRUCTION:** 카드를 닫으면 되돌릴 수 없다. 유저에게 확인을 받아라.

```bash
export PATH="$HOME/node_modules/.bin:$PATH"
agent-cards cards close <card-id> 2>&1
```

---

## 비용

| 항목 | 비용 |
|------|------|
| 가입 | 무료 (베타) |
| 카드 발급 | 무료 (충전 금액만 결제) |
| 월 구독 | 없음 |
| 카드 사용 | 충전한 금액에서 차감 |

## 규칙

1. **카드 정보를 MEMORY.md에 저장하지 마라** — PAN, CVV는 보안 데이터다. 필요할 때 `agent-cards cards details`로 조회하라.
2. **$50 이상 카드 생성 시 유저에게 확인** — 금액을 반드시 보여주고 동의를 받아라.
3. **환경변수를 추측하지 마라** — `$BOT_EMAIL_ADDRESS`와 `$GATEWAY_TOKEN`은 자동 설정된 값을 사용.
4. **한 Step씩 실행** — 각 Step 실행 후 결과를 확인하고 다음으로 진행.
5. **스크립트 파일 금지** — 모든 명령을 인라인으로 실행.
6. **jq 대신 `node -e`** — JSON 파싱 패턴: `echo '{}' | node -e "..."`
7. **카드는 선불(prepaid)** — 충전된 금액 이상 사용 불가. 잔액 부족 시 유저에게 새 카드 생성을 요청.
8. **3DS/SMS 인증이 필요한 결제는 불가** — 선불 카드는 interactive auth를 지원하지 않을 수 있다. 유저에게 안내하라.
9. **JWT 만료 시 재로그인** — `agent-cards login`을 실행하고 magic link 인증을 반복하라 (Step 3-4와 동일).
10. **Stripe 결제 URL은 유저에게 즉시 전달** — 결제는 유저가 직접 한다. 봇이 대신 결제하지 않는다.
