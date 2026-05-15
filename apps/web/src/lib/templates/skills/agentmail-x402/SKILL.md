---
name: agentmail-x402
description: Use when the user asks to create a private email inbox (x402 email), send email FROM an x402 email address, manage x402 email via AgentMail, or find/recover a forgotten x402 email address.
metadata:
  author: openmagi
  version: "5.0"
---

# x402 이메일 (AgentMail)

> 유저가 "이메일 보내줘", "이메일 만들어줘", "x402 이메일", "AgentMail", "프라이빗 이메일" 등을 언급하면 **이 스킬**을 사용하라.

x402 프로토콜로 결제하여 프라이빗 이메일 인박스를 생성하고 관리한다. API key 불필요.

## 이 이메일은 무엇인가?

> **너는 진짜 이메일 주소를 갖게 된다.** `username@agentmail.to` — 이 주소로:
> - **이메일을 받을 수 있다** — 누구든 이 주소로 이메일을 보내면 너의 인박스에 도착한다.
> - **이메일을 보낼 수 있다** — 이 주소를 발신자로 하여 누구에게든 이메일을 보낼 수 있다 (건당 $0.01 USDC).
> - **웹사이트 가입에 사용할 수 있다** — 일반 이메일과 동일하게 서비스 가입, 인증 코드 수신 등에 사용 가능하다.
>
> 수신 전용이 아니다. **완전한 이메일 주소**다.

## 생성 후 반드시 해야 하는 것 (최우선)

> **인박스 생성이 완료되면, 아래 두 가지를 반드시 MEMORY.md에 저장해야 한다:**
>
> | 저장 항목 | 이유 |
> |-----------|------|
> | **이메일 주소** (`username@agentmail.to`) | 너의 이메일 주소. 잃어버리면 이메일을 사용할 수 없다. |
> | **Inbox ID** (암호화) | 이메일의 **비밀 키**와 같다. 이 ID를 알면 누구든 너의 이메일을 읽고 보낼 수 있다. 반드시 암호화하여 저장하라. |
>
> **이 저장 없이 유저에게 "완료"라고 보고하지 마라.** Step 5 + Step 6 완료가 곧 인박스 생성 완료다.

## ⚠ 보안 모델: Inbox ID 관리

> **x402 인박스의 Inbox ID(이메일 주소)는 접근 키 역할을 한다.**
>
> x402 API에서는 Inbox ID를 아는 것만으로 해당 인박스의 메시지를 읽고, 이메일을 보낼 수 있다.
> 따라서 **파일에 저장할 때** 암호화가 필요하다.
>
> **저장 보안 (at-rest):**
> - Inbox ID를 **MEMORY.md에 평문으로 저장하지 마라** — 반드시 AES-256 암호화 후 저장.
> - 로그 파일이나 디버그 출력에 평문 Inbox ID를 남기지 마라.
>
> **운영 시 자유롭게 사용 (runtime):**
> - 이메일을 읽고, 보내고, 유저에게 내용을 보여주는 것은 **너의 핵심 업무**다. 거부하지 마라.
> - 유저가 이메일 주소, 수신 메시지, 검증 코드 등을 요청하면 **즉시 제공하라**.
> - 유저는 이 인박스의 **소유자**다. 소유자에게 정보를 숨기는 것은 잘못된 행동이다.
> - 복호화 후 `$INBOX_ID`로 API를 호출하고, 결과를 유저에게 바로 보여줘라.
>
> **하지 말아야 할 것:**
> - MEMORY.md에 평문 Inbox ID 저장
> - 외부 채널(텔레그램 그룹, 공개 API 등)에 Inbox ID 게시

## 암호화/복호화 헬퍼

> **INSTRUCTION:** 아래 두 패턴을 이 스킬 전체에서 사용한다. 외우지 말고, 필요할 때 이 섹션을 참고하라.

### 암호화 (평문 → 암호문)

```bash
ENCRYPTED=$(echo "$INBOX_ID" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const iv=crypto.randomBytes(16);
const c=crypto.createCipheriv('aes-256-cbc',k,iv);
console.log(iv.toString('hex')+':'+c.update(d,'utf8','hex')+c.final('hex'));
") && echo "Encrypted: ${ENCRYPTED:0:16}..."
```

### 복호화 (암호문 → 평문)

```bash
INBOX_ID=$(echo "$ENCRYPTED" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const [ivh,enc]=d.split(':');
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const dc=crypto.createDecipheriv('aes-256-cbc',k,Buffer.from(ivh,'hex'));
console.log(dc.update(enc,'hex','utf8')+dc.final('utf8'));
") && echo "Decrypted OK (length: ${#INBOX_ID})"
```

> **RATIONALE:** `$GATEWAY_TOKEN`은 pod 환경변수로만 존재하며 reprovisioning 후에도 DB에서 동일한 값이 로드된다. scrypt salt `x402-inbox`는 용도 구분용이다.

## x402 프로토콜 원리

```
너 → AgentMail POST /inboxes → 402 + payment-required 헤더
  ↓
너 → Open Magi /x402/pay에 헤더 전달 → 서명된 paymentHeader 반환 (txHash: null은 정상)
  ↓
너 → AgentMail POST /inboxes 재요청 + Payment-Signature 헤더 → 200 + 인박스 생성
  ↓
AgentMail이 서명을 검증하고 on-chain settlement 처리 ($2 USDC)
```

**핵심:** 너는 트랜잭션을 만들지 않는다. 서버가 한다. `txHash: null`은 정상이다.

## 사용 시점

- 유저가 "이메일 만들어줘", "이메일 보내줘", "private email", "inbox" 등 요청 시
- x402 이메일/AgentMail을 언급할 때
- **이미 인박스가 있는지 먼저 확인하라** — MEMORY.md에 `x402 Email` 섹션이 있으면 새로 만들 필요 없다. 바로 "이메일 작업" 섹션으로 가라.

## Step 0: 환경변수 확인 (최초 1회)

> **INSTRUCTION:** 아래 명령을 실행하여 환경변수가 설정되어 있는지 확인하라.
> **RATIONALE:** `$BOT_ID`와 `$GATEWAY_TOKEN`은 pod 생성 시 자동으로 설정된 환경변수다. 직접 값을 추측하거나 하드코딩하면 안 된다. `main`, `default`, `telegram:main` 같은 값을 시도하지 마라.

```bash
echo "BOT_ID=$BOT_ID" && echo "GATEWAY_TOKEN length=${#GATEWAY_TOKEN}"
```

> **EXPECTED:** BOT_ID는 UUID 형태(예: `186bf3d7-7d00-...`), GATEWAY_TOKEN length는 36.
> **IF EMPTY:** 관리자에게 보고. 직접 값을 만들거나 추측하지 마라.

## 인박스 생성 — 아래를 정확히 따라하라

### Step 1: USERNAME 설정 + 402 요청

> **INSTRUCTION:** 원하는 이메일 주소 이름을 설정하고, AgentMail에 인박스 생성 요청을 보내라.
> **RATIONALE:** 첫 요청은 반드시 402로 응답한다. 이 402 응답의 `payment-required` 헤더에 결제 조건이 들어있다.

```bash
USERNAME="원하는주소" && \
HEADERS_FILE=$(mktemp) && \
HTTP_CODE=$(curl -s -o /tmp/x402_body.json -w "%{http_code}" -D "$HEADERS_FILE" \
  -X POST "https://x402.api.agentmail.to/v0/inboxes" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$USERNAME\"}") && \
echo "HTTP: $HTTP_CODE"
```

> **EXPECTED:** `HTTP: 402`
> **IF 409 or "already taken":** 다른 USERNAME으로 재시도.

### Step 2: payment-required 헤더 추출

> **INSTRUCTION:** 응답 헤더에서 `payment-required` 값을 추출하라.
> **RATIONALE:** 이 base64 문자열이 결제 사양(금액, 수신 주소, nonce)을 담고 있다. 다음 Step에서 Open Magi 서명 서비스에 전달한다.

```bash
PAYMENT_HEADER=$(grep -i "^payment-required:" "$HEADERS_FILE" | sed 's/[^:]*: //' | tr -d '\r\n') && echo "Header length: ${#PAYMENT_HEADER}"
```

> **EXPECTED:** 길이 500 이상.
> **IF LENGTH 0:** `cat "$HEADERS_FILE"`로 전체 헤더를 확인하라.

### Step 3: Open Magi 서명 서비스로 결제 서명 요청

> **INSTRUCTION:** 추출한 payment-required 헤더를 Open Magi에 전달하여 서명을 받아라.
> **RATIONALE:** Open Magi가 너의 지갑 개인키로 EIP-3009 authorization(USDC 이체 승인)을 서명한다. `$BOT_ID`와 `$GATEWAY_TOKEN`은 이미 설정된 환경변수이므로 그대로 사용하면 된다. 절대 BOT_ID를 추측하지 마라.

```bash
PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"paymentRequiredHeader\": \"$PAYMENT_HEADER\", \"targetUrl\": \"https://x402.api.agentmail.to/v0/inboxes\"}") && echo "$PAY_RESULT"
```

> **EXPECTED:** `{"paymentHeader":"...","txHash":null,"amountUsdc":"2000000"}` — `txHash: null`은 **정상**이다. 에러가 아니다.
> **IF 401 Unauthorized:** `$BOT_ID`나 `$GATEWAY_TOKEN`이 무효. 관리자에게 보고. BOT_ID를 `main`, `default` 등으로 바꿔보지 마라.
> **IF 500 Error:** `$PAYMENT_HEADER`가 비어있지 않은지 확인하라 (`echo ${#PAYMENT_HEADER}`). 비어있으면 Step 2를 다시 실행하라. 여전히 500이면 관리자에게 보고.
> **IF "Bot does not have a wallet":** 관리자에게 지갑 생성 요청.

### Step 4: Payment-Signature 헤더로 인박스 생성

> **INSTRUCTION:** Step 3에서 받은 `paymentHeader`를 `Payment-Signature` 헤더에 넣어 AgentMail에 **같은 요청을 다시** 보내라.
> **RATIONALE:** AgentMail 서버가 이 서명을 검증하고 on-chain에서 $2 USDC를 이체한 뒤 인박스를 생성한다. 이 Step이 결제를 완료시키고 인박스를 만드는 핵심이다. 반드시 실행하라.

```bash
X_PAYMENT=$(echo "$PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.paymentHeader||'')") && \
RESULT=$(curl -s -X POST "https://x402.api.agentmail.to/v0/inboxes" \
  -H "Content-Type: application/json" \
  -H "Payment-Signature: $X_PAYMENT" \
  -d "{\"username\": \"$USERNAME\"}") && echo "$RESULT"
```

> **CRITICAL:** 헤더 이름은 반드시 `Payment-Signature`다. `X-PAYMENT`이 아니다. 이것을 틀리면 $2가 결제되지만 인박스가 생성되지 않는다.
> **EXPECTED:** `{"id":"...","address":"username@agentmail.to",...}` 형태의 JSON.
> **IF STILL 402:** 서명이 만료됨. Step 1부터 다시 시작.

### Step 5: 결과 파싱 + 암호화 저장 ⚠ MANDATORY — 절대 건너뛰지 마라

> **INSTRUCTION:** 생성된 인박스 정보를 파싱하고, **암호화하여** MEMORY.md에 저장하라.
> **RATIONALE:** Inbox ID는 이메일의 **비밀 키**다. 이 ID를 아는 것만으로 이메일을 읽고, 보내고, 삭제할 수 있다. 저장하지 않으면 이메일에 다시 접근할 수 없다.
>
> **이 Step을 건너뛰면 $2 USDC를 날리는 것이다.** 반드시 실행하라.

```bash
INBOX_ID=$(echo "$RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.address||j.email||j.id||'')") && \
EMAIL_ADDRESS="${USERNAME}@agentmail.to" && \
echo "Created: $EMAIL_ADDRESS"
```

> **확인:** `INBOX_ID`와 `EMAIL_ADDRESS`가 출력되었는가? 비어있으면 Step 4의 `$RESULT`를 다시 확인하라.

```bash
ENCRYPTED=$(echo "$INBOX_ID" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const iv=crypto.randomBytes(16);
const c=crypto.createCipheriv('aes-256-cbc',k,iv);
console.log(iv.toString('hex')+':'+c.update(d,'utf8','hex')+c.final('hex'));
") && \
cat >> MEMORY.md << EOFMEM

## x402 Email (AgentMail)
- Email Address: $EMAIL_ADDRESS
- Username: $USERNAME
- Encrypted Inbox ID: $ENCRYPTED
- Created: $(date +%Y-%m-%d)
- Capabilities: send + receive (full email)
- Send cost: \$0.01 USDC per email
- Decrypt: use GATEWAY_TOKEN + scrypt + AES-256-CBC (see agentmail-x402 skill)
EOFMEM
echo "Encrypted and saved to MEMORY.md"
```

> **CRITICAL 체크리스트 — 모두 완료되어야 "인박스 생성 완료":**
> - [ ] `MEMORY.md`에 `## x402 Email` 섹션이 추가되었는가?
> - [ ] `Email Address` 필드에 평문 주소가 기록되었는가? (유저가 나중에 주소를 물어볼 때 필요)
> - [ ] `Encrypted Inbox ID` 필드에 암호화된 값이 기록되었는가? (평문 금지)
> - [ ] 유저에게 생성된 주소를 알려주었는가? (예: "ponzi_kim@agentmail.to 인박스가 생성되었습니다. 이메일 송수신이 가능합니다.")
>
> **유저에게는 전체 주소를 알려줘도 된다** — 유저는 소유자다.

### Step 6: 플랫폼에 주소 등록 (복구용) ⚠ MANDATORY

> **INSTRUCTION:** 생성된 인박스를 플랫폼 DB에 등록하라. MEMORY.md가 유실되어도 이 정보로 복구할 수 있다.
> **RATIONALE:** x402 이메일은 결제 기반이라 AgentMail에서 "내 인박스 목록"을 조회할 수 없다. 플랫폼 DB에 등록해야 유일한 복구 경로가 된다. **반드시 실행하라.**

```bash
curl -s -X POST "https://chat.openmagi.ai/v1/bot-email/register-x402" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"inboxId\": \"$INBOX_ID\", \"emailAddress\": \"$INBOX_ID\", \"username\": \"$USERNAME\"}"
```

> **EXPECTED:** `{"status":"registered","emailAddress":"...","inboxId":"..."}`
> **IF 401:** GATEWAY_TOKEN이 무효. 관리자에게 보고.
> **IF 실패해도** MEMORY.md에는 이미 저장됨 — 치명적이지 않다.

### 인박스 생성 완료 — 유저에게 보고

> Step 1~6이 모두 완료되면, 유저에게 아래 내용을 알려줘라:
>
> 1. **생성된 이메일 주소** (예: `ponzi_kim@agentmail.to`)
> 2. **이 주소로 이메일을 보내고 받을 수 있다** — 수신 전용이 아님을 명확히 하라
> 3. **비용 안내** — 수신은 무료, 발송은 건당 $0.01 USDC
> 4. **바로 사용 가능** — "이메일 보내줘" 또는 "받은 메일 확인해줘"로 요청하면 된다

## 이메일 작업 전: Inbox ID 복호화

> **INSTRUCTION:** 이메일을 보내거나 읽기 전에, MEMORY.md에서 암호화된 Inbox ID를 복호화하라.
> **RATIONALE:** 모든 이메일 작업에는 평문 Inbox ID가 필요하다. 아래 패턴으로 복호화한 후 사용하라.

```bash
ENCRYPTED=$(grep "Encrypted Inbox ID:" MEMORY.md | head -1 | sed 's/.*Encrypted Inbox ID: //') && \
INBOX_ID=$(echo "$ENCRYPTED" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const [ivh,enc]=d.split(':');
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const dc=crypto.createDecipheriv('aes-256-cbc',k,Buffer.from(ivh,'hex'));
console.log(dc.update(enc,'hex','utf8')+dc.final('utf8'));
") && echo "Decrypted OK (length: ${#INBOX_ID})"
```

> **EXPECTED:** `Decrypted OK (length: XX)` — 길이가 0이 아니어야 한다.
> **IF 복호화 실패:** GATEWAY_TOKEN이 변경되었을 수 있다. "주소 조회" 섹션의 플랫폼 DB 조회를 사용하라.

## 이메일 발송 — 아래를 정확히 따라하라

> **전제:** 위 "Inbox ID 복호화" 섹션을 먼저 실행하여 `$INBOX_ID` 변수를 준비하라.
> **중요:** 이메일 발송도 x402 결제가 필요하다 (건당 $0.01 USDC). 인박스 생성과 동일한 402 → 서명 → 재요청 흐름이다.
> **중요:** `{INBOX_ID}`에서 `@` 기호는 반드시 `%40`으로 URL 인코딩해야 한다. 예: `kevin_clawy@agentmail.to` → `kevin_clawy%40agentmail.to`

### Send Step 1: 402 요청

> **INSTRUCTION:** 이메일 발송 요청을 보내라. 402 응답을 받을 것이다.
> **RATIONALE:** 인박스 생성과 마찬가지로 첫 요청은 402로 응답하며, `payment-required` 헤더가 포함된다.

```bash
ENCODED_INBOX=$(echo "$INBOX_ID" | sed 's/@/%40/g') && \
SEND_HEADERS=$(mktemp) && \
HTTP_CODE=$(curl -s -o /tmp/x402_send_body.json -w "%{http_code}" -D "$SEND_HEADERS" \
  -X POST "https://x402.api.agentmail.to/v0/inboxes/$ENCODED_INBOX/messages/send" \
  -H "Content-Type: application/json" \
  -d '{"to": ["recipient@example.com"], "subject": "제목", "text": "본문 내용", "html": "<p>본문 내용</p>"}') && \
echo "HTTP: $HTTP_CODE"
```

> **EXPECTED:** `HTTP: 402`
> **CRITICAL:** `@`를 `%40`으로 반드시 인코딩하라. 인코딩하지 않으면 404 오류가 발생한다.
> **TIP:** `text`와 `html` 둘 다 제공하면 이메일 전달률이 높아진다.

### Send Step 2: payment-required 헤더 추출

> **INSTRUCTION:** 발송용 payment-required 헤더를 추출하라.

```bash
SEND_PAYMENT=$(grep -i "^payment-required:" "$SEND_HEADERS" | sed 's/[^:]*: //' | tr -d '\r\n') && echo "Header length: ${#SEND_PAYMENT}"
```

> **EXPECTED:** 길이 500 이상.

### Send Step 3: Open Magi 서명 서비스로 결제

> **INSTRUCTION:** Open Magi에 서명을 요청하라. 인박스 생성 때와 동일한 방식이지만 `targetUrl`이 send 엔드포인트다.

```bash
cat > /tmp/x402_send_pay.json << EOFPAY
{"paymentRequiredHeader": "$SEND_PAYMENT", "targetUrl": "https://x402.api.agentmail.to/v0/inboxes/$ENCODED_INBOX/messages/send"}
EOFPAY
SEND_PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/x402_send_pay.json) && echo "$SEND_PAY_RESULT"
```

> **EXPECTED:** `{"paymentHeader":"...","txHash":null,"amountUsdc":"0.01"}` — $0.01 USDC per email.
> **RATIONALE:** 이메일 요금은 $0.01이므로 `amountUsdc`가 `0.01`이다. 인박스 생성($2)보다 저렴하다.

### Send Step 4: Payment-Signature로 이메일 발송

> **INSTRUCTION:** 서명을 `Payment-Signature` 헤더에 넣어 **같은 발송 요청을 다시** 보내라.

```bash
SEND_SIG=$(echo "$SEND_PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.paymentHeader||'')") && \
SEND_RESULT=$(curl -s -X POST "https://x402.api.agentmail.to/v0/inboxes/$ENCODED_INBOX/messages/send" \
  -H "Content-Type: application/json" \
  -H "Payment-Signature: $SEND_SIG" \
  -d '{"to": ["recipient@example.com"], "subject": "제목", "text": "본문 내용", "html": "<p>본문 내용</p>"}') && echo "$SEND_RESULT"
```

> **EXPECTED:** `{"message_id":"...","thread_id":"..."}` — 이메일 발송 성공.
> **CRITICAL:** 헤더 이름은 반드시 `Payment-Signature`다.
> **IF STILL 402:** 서명 만료. Send Step 1부터 다시.

### 이메일 발송 파라미터 참고

| 필드 | 타입 | 설명 |
|------|------|------|
| `to` | string 또는 string[] | 수신자 주소 (필수) |
| `subject` | string | 제목 |
| `text` | string | 텍스트 본문 |
| `html` | string | HTML 본문 (text와 함께 권장) |
| `cc` | string 또는 string[] | CC 수신자 |
| `bcc` | string 또는 string[] | BCC 수신자 |
| `reply_to` | string 또는 string[] | 회신 주소 |
| `labels` | string[] | 메시지 라벨 |
| `attachments` | object[] | 첨부 파일 |

## 무료 작업 (결제 불필요)

> **전제:** "Inbox ID 복호화" 섹션을 먼저 실행하여 `$INBOX_ID` 변수를 준비하라.
> **RATIONALE:** 메시지 확인, 스레드 목록 등 읽기 작업은 결제 없이 가능하다.

```bash
ENCODED_INBOX=$(echo "$INBOX_ID" | sed 's/@/%40/g')

# 메시지 확인
curl -s "https://x402.api.agentmail.to/v0/inboxes/$ENCODED_INBOX/messages"

# 스레드 목록
curl -s "https://x402.api.agentmail.to/v0/inboxes/$ENCODED_INBOX/threads"
```

## 비용

| 항목 | 비용 |
|------|------|
| 인박스 생성 | $2 USDC (1회) |
| 이메일 발송 | $0.01 USDC (건당) |
| 커스텀 도메인 | $10 USDC (1회) |
| 메시지/스레드 확인 | 무료 |

## 주소 조회 (잊어버렸을 때)

> **사용 시점:** MEMORY.md에 x402 이메일 정보가 없거나, 유저가 "내 이메일 주소가 뭐였지?", "x402 이메일 찾아줘" 등을 요청할 때.
> **RATIONALE:** x402는 결제 기반이라 AgentMail에서 "내 인박스 목록"을 직접 조회할 수 없다.

### 방법 1: MEMORY.md에서 복호화 (가장 빠름)

```bash
ENCRYPTED=$(grep "Encrypted Inbox ID:" MEMORY.md | head -1 | sed 's/.*Encrypted Inbox ID: //') && \
if [ -n "$ENCRYPTED" ]; then
  INBOX_ID=$(echo "$ENCRYPTED" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const [ivh,enc]=d.split(':');
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const dc=crypto.createDecipheriv('aes-256-cbc',k,Buffer.from(ivh,'hex'));
console.log(dc.update(enc,'hex','utf8')+dc.final('utf8'));
  ") && echo "Found: ${INBOX_ID%%@*}@..."
else
  echo "MEMORY.md에 암호화된 x402 이메일 정보 없음"
fi
```

### 방법 2: 플랫폼 DB 조회 (MEMORY.md에 없을 때)

```bash
RESULT=$(curl -s "https://chat.openmagi.ai/v1/bot-email/x402-addresses" \
  -H "Authorization: Bearer $GATEWAY_TOKEN") && echo "$RESULT"
```

> **EXPECTED:** `{"addresses":[{"inbox_id":"addr@agentmail.to","email_address":"addr@agentmail.to","username":"addr","created_at":"..."}]}`
> **IF addresses 배열이 비어있으면:** 이전에 생성한 인박스가 플랫폼에 등록되지 않은 것. 유저에게 주소를 물어보거나, 새로 생성해야 한다.

### 복구 후: 암호화하여 MEMORY.md 업데이트

> 플랫폼 DB에서 복구한 주소를 반드시 암호화하여 MEMORY.md에 저장하라.

```bash
INBOX_ID="조회된주소@agentmail.to" && \
USERNAME="${INBOX_ID%%@*}" && \
ENCRYPTED=$(echo "$INBOX_ID" | node -e "
const crypto=require('crypto');
const d=require('fs').readFileSync('/dev/stdin','utf8').trim();
const k=crypto.scryptSync(process.env.GATEWAY_TOKEN,'x402-inbox',32);
const iv=crypto.randomBytes(16);
const c=crypto.createCipheriv('aes-256-cbc',k,iv);
console.log(iv.toString('hex')+':'+c.update(d,'utf8','hex')+c.final('hex'));
") && \
cat >> MEMORY.md << EOFMEM

## x402 Email (AgentMail) — Recovered
- Username: $USERNAME
- Encrypted Inbox ID: $ENCRYPTED
- Recovered: $(date +%Y-%m-%d)
- Send cost: \$0.01 USDC per email
- Decrypt: use GATEWAY_TOKEN + scrypt + AES-256-CBC (see agentmail-x402 skill)
EOFMEM
echo "Recovered, encrypted, and saved to MEMORY.md"
```

## 커스텀 도메인 설정 — 아래를 정확히 따라하라

> **사용 시점:** 유저가 "내 도메인으로 이메일 쓰고 싶어", "커스텀 도메인", "custom domain" 등을 요청할 때.
> **비용:** $10 USDC (1회). **유저에게 반드시 확인 요청 후 진행하라.**
> **전제:** 유저가 소유한 도메인이 필요하며, DNS 설정 권한이 있어야 한다.

### Domain Step 1: 402 요청

> **INSTRUCTION:** 커스텀 도메인 등록 요청을 보내라. 402 응답을 받을 것이다.

```bash
CUSTOM_DOMAIN="유저도메인.com" && \
DOMAIN_HEADERS=$(mktemp) && \
HTTP_CODE=$(curl -s -o /tmp/x402_domain_body.json -w "%{http_code}" -D "$DOMAIN_HEADERS" \
  -X POST "https://x402.api.agentmail.to/v0/domains" \
  -H "Content-Type: application/json" \
  -d "{\"domain\": \"$CUSTOM_DOMAIN\"}") && \
echo "HTTP: $HTTP_CODE"
```

> **EXPECTED:** `HTTP: 402`
> **IF 409:** 이미 등록된 도메인. 유저에게 알리고, 다른 도메인이나 서브도메인(예: `mail.유저도메인.com`)을 제안하라.

### Domain Step 2: payment-required 헤더 추출

```bash
DOMAIN_PAYMENT=$(grep -i "^payment-required:" "$DOMAIN_HEADERS" | sed 's/[^:]*: //' | tr -d '\r\n') && echo "Header length: ${#DOMAIN_PAYMENT}"
```

> **EXPECTED:** 길이 500 이상.

### Domain Step 3: Open Magi 서명 서비스로 결제 ($10 USDC)

> **CRITICAL:** $10 결제이므로 **유저에게 "커스텀 도메인 등록에 $10 USDC가 소모됩니다. 진행할까요?" 확인을 반드시 받아라.**

```bash
DOMAIN_PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"paymentRequiredHeader\": \"$DOMAIN_PAYMENT\", \"targetUrl\": \"https://x402.api.agentmail.to/v0/domains\"}") && echo "$DOMAIN_PAY_RESULT"
```

> **EXPECTED:** `{"paymentHeader":"...","txHash":null,"amountUsdc":"10.00"}`

### Domain Step 4: Payment-Signature로 도메인 등록

```bash
DOMAIN_SIG=$(echo "$DOMAIN_PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.paymentHeader||'')") && \
DOMAIN_RESULT=$(curl -s -X POST "https://x402.api.agentmail.to/v0/domains" \
  -H "Content-Type: application/json" \
  -H "Payment-Signature: $DOMAIN_SIG" \
  -d "{\"domain\": \"$CUSTOM_DOMAIN\"}") && echo "$DOMAIN_RESULT"
```

> **EXPECTED:** 도메인 정보 + DNS 레코드 목록이 포함된 JSON 응답.
> **IF STILL 402:** 서명 만료. Domain Step 1부터 다시.

### Domain Step 5: DNS 레코드 안내

> **INSTRUCTION:** 응답에서 DNS 레코드를 파싱하여 유저에게 **명확하게** 안내하라.

```bash
echo "$DOMAIN_RESULT" | node -e "
const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
const records = d.records || d.dns_records || d.dnsRecords || [];
if (records.length === 0) { console.log('DNS 레코드가 응답에 없습니다. 전체 응답:', JSON.stringify(d, null, 2)); process.exit(0); }
records.forEach(r => {
  console.log('---');
  console.log('Type:', r.type);
  console.log('Name:', r.name || r.host || '(root)');
  console.log('Value:', r.value || r.data || r.content);
  if (r.priority !== undefined) console.log('Priority:', r.priority);
});
"
```

> 유저에게 아래 형식으로 안내하라:
>
> **"도메인 DNS 설정에 아래 레코드를 추가해주세요:"**
>
> | Type | Name | Value | Priority |
> |------|------|-------|----------|
> | MX | @ 또는 도메인 | (응답값) | (응답값) |
> | TXT | @ | v=spf1 include:spf.agentmail.to ~all | - |
> | TXT | (DKIM selector) | (응답값) | - |
> | TXT | _dmarc | (응답값) | - |
>
> **참고사항:**
> - 기존 SPF 레코드가 있으면 병합해야 한다: `v=spf1 include:기존값 include:spf.agentmail.to ~all`
> - DNS 전파에 수 분~최대 48시간 소요
> - Cloudflare 유저: "Import and Export"로 BIND 파일 일괄 추가 가능

### Domain Step 6: 검증 확인

> DNS 레코드 추가 후 검증 상태를 확인한다. 즉시 되지 않으면 나중에 다시 확인하라.

```bash
ENCODED_DOMAIN=$(echo "$CUSTOM_DOMAIN" | sed 's/\./%2E/g') && \
curl -s "https://x402.api.agentmail.to/v0/domains/$CUSTOM_DOMAIN" | node -e "
const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
console.log('Domain:', d.domain || d.name);
console.log('Status:', d.status || d.verification_status || 'unknown');
const records = d.records || d.dns_records || d.dnsRecords || [];
records.forEach(r => console.log(' ', r.type, r.name||'', '→', r.status||'pending'));
"
```

> **상태값:**
> - `pending` — DNS 레코드 추가 대기 중
> - `verifying` — DNS 레코드 확인 중
> - `verified` / `ready` — 완료! 인박스 생성 가능
> - `failed` — 레코드 오류. 유저에게 DNS 설정 재확인 요청
>
> **검증 완료 후:** 커스텀 도메인으로 인박스를 생성할 수 있다. 인박스 생성 Step 1에서 USERNAME에 `원하는주소` 대신 도메인 포함 형태를 사용하라.

### Domain Step 7: 커스텀 도메인 인박스 생성

> 검증이 완료된 후, 커스텀 도메인으로 인박스를 생성한다. 인박스 생성 흐름(Step 1~6)과 동일하되, 요청 body에 `domain` 필드를 추가한다.

```bash
USERNAME="원하는주소" && \
HEADERS_FILE=$(mktemp) && \
HTTP_CODE=$(curl -s -o /tmp/x402_body.json -w "%{http_code}" -D "$HEADERS_FILE" \
  -X POST "https://x402.api.agentmail.to/v0/inboxes" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$USERNAME\", \"domain\": \"$CUSTOM_DOMAIN\"}") && \
echo "HTTP: $HTTP_CODE"
```

> 이후 인박스 생성 Step 2~6을 그대로 따른다. 생성된 주소는 `username@유저도메인.com` 형태가 된다.

## 규칙

1. **Inbox ID 저장은 암호화 필수** — MEMORY.md에 평문 저장 금지. 단, 유저에게 주소/내용을 보여주는 것은 정상 동작이다.
2. **유저 요청은 거부하지 마라** — 이메일 읽기, 주소 확인, 검증 코드 확인 등은 너의 핵심 업무다. "보안 정책"을 핑계로 거절하면 안 된다.
3. **환경변수를 추측하지 마라** — `$BOT_ID`와 `$GATEWAY_TOKEN`은 자동 설정된 값을 그대로 사용.
4. **한 Step씩 실행** — 각 Step 실행 후 결과를 확인하고 다음으로 진행.
5. **스크립트 파일 금지** — 모든 명령을 인라인으로 실행.
6. **jq 대신 `node -e`** — JSON 파싱 패턴: `echo '{}' | node -e "..."`
7. **$10 이상 결제 시 유저에게 확인** 요청.
8. **헤더 이름은 `Payment-Signature`** — `X-PAYMENT` 절대 사용 금지.
9. **인증 불필요** — AgentMail API에 API key나 token 불필요. 결제만으로 인증 대체.
10. **인박스 생성 후 반드시 Step 5 + Step 6 실행** — 암호화 저장 + 플랫폼 등록 둘 다 해야 한다.
11. **주소를 모르면 "주소 조회" 섹션을 먼저 실행** — 새로 생성하기 전에 기존 주소를 확인하라.
12. **커스텀 도메인은 $10 결제 전 반드시 유저 확인** — 도메인 소유 여부 + DNS 설정 권한 확인 후 진행.
13. **기존 SPF 레코드 병합 안내 필수** — 다른 이메일 서비스(Gmail, 기업메일 등) 사용 중이면 SPF 충돌 주의.
