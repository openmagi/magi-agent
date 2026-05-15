---
name: privy
description: Use when managing Privy embedded wallets, checking wallet info, sending EVM transactions, signing messages, or interacting with Web3 blockchain operations. Supports Ethereum, Base, Polygon, Arbitrum, and Optimism.
metadata:
  author: openmagi
  version: "1.0"
---

# Privy Wallet Integration

Privy Wallet API로 임베디드 지갑 관리, EVM 트랜잭션 송신, 메시지 서명을 수행한다. 다중 체인 지원 (Ethereum, Base, Polygon, Arbitrum, Optimism).

## When to Use

- 지갑 정보 조회 (잔액, 주소)
- EVM 트랜잭션 전송 (전송, 스마트컨트랙트 상호작용)
- 메시지 서명 (personal_sign)
- 다중 체인 작업
- 기존 정책 확인 및 거래 검증

## Environment Variables

Privy Wallet API 인증을 위해 다음 환경변수 필수:

- `PRIVY_APP_ID` — Privy 애플리케이션 ID (privy.com 대시보드)
- `PRIVY_APP_SECRET` — Privy 애플리케이션 시크릿 키 (안전하게 보관)
- `PRIVY_WALLET_ID` — 사용자의 Privy 지갑 ID (사용자마다 고유)

## Authentication (Basic Auth)

모든 요청은 Basic Auth 사용. Base64 인코딩:

```bash
echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64
```

헤더에 추가:

```
Authorization: Basic <base64-encoded-credentials>
```

## Supported Chains

| Chain | Network ID | RPC | 용도 |
|-------|-----------|-----|------|
| **Ethereum** | 1 | `https://eth.meowrpc.com` | 메인넷, 높은 수수료 |
| **Base** | 8453 | `https://base.meowrpc.com` | 권장 체인, 저수수료, 빠름 |
| **Polygon** | 137 | `https://polygon.meowrpc.com` | POS 체인, 매우 저수수료 |
| **Arbitrum** | 42161 | `https://arb1.arbitrum.io/rpc` | L2 Rollup, 빠르고 저수수료 |
| **Optimism** | 10 | `https://mainnet.optimism.io` | L2 Rollup, 빠르고 저수수료 |

## API Endpoints

**Base URL**: `https://api.privy.io/v1`

### 1. Get Wallet Info

지갑 주소, 잔액, 체인 정보 조회.

```bash
curl -X GET "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID" \
  -H "Authorization: Basic $(echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64)" \
  -H "Content-Type: application/json"
```

**Response**:
```json
{
  "id": "wallet_xxxx",
  "address": "0x1234...",
  "chains": [
    {
      "id": 1,
      "name": "ethereum",
      "address": "0x1234...",
      "balance": "1.5"
    }
  ]
}
```

### 2. Send EVM Transaction

트랜잭션 서명 및 전송 (eth_sendTransaction).

```bash
curl -X POST "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID/rpc" \
  -H "Authorization: Basic $(echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64)" \
  -H "Content-Type: application/json" \
  -d '{
    "chainId": 8453,
    "method": "eth_sendTransaction",
    "params": [
      {
        "from": "0x1234...",
        "to": "0x5678...",
        "value": "1000000000000000000",
        "gasLimit": "21000"
      }
    ]
  }'
```

**Parameter Guide**:
- `chainId` — 체인 ID (위 테이블 참고, **기본값 8453 = Base**)
- `method` — `eth_sendTransaction` (트랜잭션 전송) 또는 `personal_sign` (메시지 서명)
- `from` — 발신자 지갑 주소
- `to` — 수신자 주소 (선택, null이면 컨트랙트 배포)
- `value` — Wei 단위 전송 금액 (hex 또는 decimal 문자열)
- `data` — 컨트랙트 호출 시 calldata (선택)
- `gasLimit` — Gas limit (21000 = 기본 전송, 더 복잡하면 더 필요)

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "0xabcd...",
  "id": 1
}
```

반환값 `result`는 **트랜잭션 해시** — 이를 기록하고 사용자에게 보고.

### 3. Sign Message

메시지 서명 (personal_sign, EIP-191).

```bash
curl -X POST "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID/rpc" \
  -H "Authorization: Basic $(echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64)" \
  -H "Content-Type: application/json" \
  -d '{
    "chainId": 8453,
    "method": "personal_sign",
    "params": [
      "0x48656c6c6f20576f726c64",
      "0x1234..."
    ]
  }'
```

**Parameters**:
- `params[0]` — 서명할 메시지 (hex 인코딩, 또는 UTF-8 문자열)
- `params[1]` — 서명자 주소

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "0xabcd...",
  "id": 1
}
```

반환값 `result`는 **서명(signature)** — 검증 또는 저장.

## Workflow

### Step 1: 정책 확인 (Policy Check First)

거래 전에 항상 지갑의 정책 및 제한사항 확인:

```bash
curl -X GET "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID" \
  -H "Authorization: Basic $(echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64)"
```

응답에서:
- 지갑 주소 확인
- 활성화된 체인 목록 확인
- 잔액 충분한지 확인

### Step 2: 거래 구성

트랜잭션 세부사항 결정:
- **체인 선택**: 기본값은 **Base (8453)** 사용 (최저 수수료)
- **금액 계산**: Wei 단위 변환 (1 ETH = 1e18 Wei)
- **Gas 예측**: 일반 전송은 21000, 컨트랙트 호출은 더 필요

### Step 3: 사용자 확인

거래 전 사용자의 명시적 확인 요청:

```
거래를 진행하시겠습니까?
- 수신자: 0x5678...
- 금액: 1.0 ETH
- 체인: Base
- 예상 수수료: ~0.001 ETH
```

### Step 4: 거래 실행

확인 후 `eth_sendTransaction` 호출:

```bash
curl -X POST "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID/rpc" \
  -H "Authorization: Basic $(echo -n "$PRIVY_APP_ID:$PRIVY_APP_SECRET" | base64)" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

### Step 5: 결과 보고

응답 처리:
- **성공**: 트랜잭션 해시 저장 및 사용자에게 보고
- **실패**: 에러 메시지 출력 (부족한 잔액, 정책 위반, 네트워크 오류 등)

## Important Rules

1. **정책 우선**: 거래 전 `GET /wallets/$PRIVY_WALLET_ID` 으로 정책 확인
2. **Base 기본값**: 체인 지정 없으면 항상 Base (8453) 사용
3. **사용자 확인**: eth_sendTransaction 전 항상 명시적 동의 요청
4. **트랜잭션 해시**: 거래 성공 후 `result` 필드의 해시값 기록 및 보고
5. **에러 처리**: 에러는 명확하게 설명 — 부족한 잔액, 정책 위반, 네트워크 오류 등
6. **메시지 인코딩**: personal_sign 메시지는 hex 또는 UTF-8 지원

## Error Handling

### 일반 에러

```json
{
  "error": {
    "code": -32603,
    "message": "Insufficient balance",
    "data": {...}
  }
}
```

### 주요 에러 케이스

| 에러 | 원인 | 해결 |
|------|------|------|
| `Insufficient balance` | 지갑 잔액 부족 | 금액 감소 또는 충전 요청 |
| `Policy violation` | 정책 위반 | 정책 확인 후 조정 |
| `Invalid signature` | 서명 실패 | 주소/메시지 재확인 |
| `Network error` | RPC 연결 실패 | 재시도 또는 체인 변경 |
| `Gas estimation failed` | Gas 부족 | gasLimit 증가 |

## Red Flags

- **환경변수 누락**: `PRIVY_APP_ID`, `PRIVY_APP_SECRET`, `PRIVY_WALLET_ID` 필수
- **Base64 인코딩**: Basic Auth는 반드시 Base64 인코딩 — 평문 전송 금지
- **Wei 단위**: 금액은 항상 Wei (1 ETH = 1e18 Wei) — 실수 단위 주의
- **사용자 확인 건너뛰기**: 거래 전 항상 명시적 동의 필수
- **체인 기본값**: 지정 없으면 Base (8453) — Ethereum (1) 아님
- **트랜잭션 폴링**: 즉시 완료 보장 안 함 — 블록 포함 대기 필요
- **컨트랙트 calldata**: 잘못된 `data`는 거래 실패 — ABI 인코딩 확인
