---
name: siwa-auth
description: Use when authenticating to external services that support Sign In With Agent (SIWA) or EIP-4361 (Sign In With Ethereum). Handles nonce retrieval, message signing, and credential submission.
metadata:
  author: openmagi
  version: "1.0"
---

# SIWA (Sign In With Agent)

SIWA로 외부 서비스에 에이전트 지갑으로 인증한다. EIP-4361 (Sign In With Ethereum) 표준 기반.

## When to Use

- 외부 서비스가 "Sign In With Ethereum" 또는 SIWA 인증을 요구할 때
- 에이전트 신원을 증명해야 할 때
- on-chain identity로 API 접근이 필요할 때

## Workflow

### Step 1: 외부 서비스에서 Nonce 받기

```bash
NONCE=$(curl -s "$SERVICE_URL/auth/nonce" | jq -r '.nonce')
```

### Step 2: SIWA 메시지 서명 요청

Open Magi platform API를 통해 에이전트 지갑으로 서명:

```bash
RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/siwa/sign" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"domain\": \"$SERVICE_DOMAIN\",
    \"uri\": \"$SERVICE_URL\",
    \"nonce\": \"$NONCE\",
    \"statement\": \"Sign in as agent\"
  }")

MESSAGE=$(echo "$RESULT" | jq -r '.message')
SIGNATURE=$(echo "$RESULT" | jq -r '.signature')
ADDRESS=$(echo "$RESULT" | jq -r '.address')
```

### Step 3: 외부 서비스에 인증 제출

```bash
curl -X POST "$SERVICE_URL/auth/verify" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"$MESSAGE\",
    \"signature\": \"$SIGNATURE\"
  }"
```

## Parameters

| 필드 | 필수 | 설명 |
|------|------|------|
| `domain` | Yes | 외부 서비스 도메인 (예: `example.com`) |
| `uri` | Yes | 외부 서비스 URI (예: `https://example.com/auth`) |
| `nonce` | Yes | 외부 서비스에서 받은 일회용 nonce |
| `statement` | No | 서명 메시지에 포함할 설명 (기본: "Sign in with agent wallet") |

## Important Rules

1. **Nonce 필수**: 항상 외부 서비스에서 fresh nonce를 받아서 사용
2. **Domain 정확히**: 외부 서비스의 실제 도메인과 일치해야 함
3. **결과 캐싱 금지**: SIWA 서명은 매번 새로 요청 (replay 방지)
4. **Base Chain**: 에이전트 지갑은 Base (8453) 기본

## Error Handling

| 에러 | 원인 | 해결 |
|------|------|------|
| `Bot does not have a wallet` | 지갑 미생성 | 관리자에게 지갑 생성 요청 |
| `Invalid nonce` | Nonce 만료/재사용 | 새 nonce 요청 |
| `Signature verification failed` | 서명 불일치 | domain/uri 재확인 |
