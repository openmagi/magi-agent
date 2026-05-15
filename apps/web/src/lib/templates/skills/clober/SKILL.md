---
name: clober
description: Use when placing limit orders on-chain, querying CLOB orderbooks, managing open orders, or doing on-chain trading with price limits. Triggers on "limit order", "CLOB", "오더북", "리밋 오더", "Clober", "지정가 주문", "지정가 매매", "orderbook".
metadata:
  author: openmagi
  version: "1.0"
---

# Clober V2 — On-Chain CLOB 지정가 주문

Clober는 온체인 Central Limit Order Book (CLOB) DEX다. CEX처럼 지정가 주문(limit order)을 온체인에서 직접 걸 수 있다.

## When to Use
- 유저가 특정 가격에 토큰을 사고/팔고 싶을 때 (지정가 주문)
- 온체인 오더북(호가창)을 확인하고 싶을 때
- 열린 지정가 주문을 조회/취소/클레임 하고 싶을 때
- Uniswap 같은 AMM 대신 CLOB에서 매매하고 싶을 때

## 지원 체인 & 컨트랙트

| 체인 | Chain ID | BookManager | Controller |
|------|----------|-------------|------------|
| Base | 8453 | `0x382CCccbD3b142D7DA063bEc15ce8Ce4C6Ce0bB3` | `0x6e3a1BF0Cd3Ed8D3BA0e01cE98e8De03F41E2c5C` |
| Arbitrum | 42161 | `0x382CCccbD3b142D7DA063bEc15ce8Ce4C6Ce0bB3` | `0x6e3a1BF0Cd3Ed8D3BA0e01cE98e8De03F41E2c5C` |

> **주의:** 컨트랙트 주소는 Clober V2 기준. 최신 주소는 https://docs.clober.io 에서 확인할 것.

## 핵심 개념

| 개념 | 설명 |
|------|------|
| **Limit Order (지정가)** | 지정한 가격에 도달할 때만 체결. Maker로서 오더북에 유동성을 제공. |
| **Market Order (시장가)** | 현재 오더북의 최우선 가격으로 즉시 체결. Taker로서 유동성을 소비. |
| **Maker** | 오더북에 주문을 걸어 유동성을 제공하는 쪽. 수수료가 낮거나 없음. |
| **Taker** | 기존 주문을 체결하여 유동성을 소비하는 쪽. |
| **Tick** | 오더북의 가격 단위. Clober는 tick 기반 가격 체계 사용 (Uniswap V3와 유사). |
| **Book** | 특정 토큰 쌍(base/quote)의 오더북. |
| **Claim** | 체결된 지정가 주문의 토큰을 수령하는 행위. |

## API 엔드포인트

Clober V2는 SDK 기반 + subgraph로 데이터를 제공한다. 봇 환경에서는 아래 패턴으로 호출한다.

### Base URLs

```
Clober API: https://api.clober.io
Clober Subgraph (Base): https://subgraph.satsuma-prod.com/clober/book-manager-base/api
Clober Subgraph (Arbitrum): https://subgraph.satsuma-prod.com/clober/book-manager-arbitrum/api
```

> **참고:** subgraph URL은 변경될 수 있다. 호출 실패 시 https://docs.clober.io 에서 최신 엔드포인트를 firecrawl로 확인하라.

---

## 주요 토큰 주소

### Base (Chain ID: 8453)

| 토큰 | 주소 |
|------|------|
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| USDbC | `0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6Ca` |
| cbETH | `0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22` |
| DAI | `0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb` |

### Arbitrum (Chain ID: 42161)

| 토큰 | 주소 |
|------|------|
| WETH | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` |
| USDC | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| USDT | `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9` |
| ARB | `0x912CE59144191C1204E64559FE8253a0e49E6548` |
| WBTC | `0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f` |

---

## Step 0: 환경 확인 (최초 1회)

> **INSTRUCTION:** 지갑 주소와 환경변수를 확인한다.
> **RATIONALE:** 트랜잭션 전송에 필요한 환경변수와 지갑 주소를 먼저 확보.

```bash
echo "BOT_ID=$BOT_ID" && echo "GATEWAY_TOKEN length=${#GATEWAY_TOKEN}"
```

```bash
WALLET_RESULT=$(wallet-sign.sh "get-address") && echo "$WALLET_RESULT"
WALLET_ADDRESS=$(echo "$WALLET_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).wallet||'')")
echo "Wallet: $WALLET_ADDRESS"
```

> **EXPECTED:** `WALLET_ADDRESS`가 `0x...` 형태.
> **IF EMPTY:** 관리자에게 보고. Privy 월렛이 설정되지 않았을 수 있다.

---

## 1. 오더북 조회 (Read — 무료)

### 1-1. 토큰 심볼로 주소 해석

유저가 심볼(예: "ETH", "USDC")로 요청하면 위 토큰 주소 테이블에서 매핑한다. 테이블에 없는 토큰은 Clober API로 조회:

```bash
curl -s "https://api.clober.io/v2/tokens?chain_id=8453" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
const tokens=JSON.parse(d);
if(Array.isArray(tokens)){tokens.slice(0,20).forEach(t=>console.log(t.symbol+' → '+t.address))}
else{console.log('Response:',d.slice(0,500))}
"
```

> **참고:** API 응답 구조는 변경될 수 있다. 에러 시 firecrawl로 docs.clober.io/api 를 스크래핑하여 최신 엔드포인트를 확인하라.

### 1-2. 오더북 (호가창) 조회

특정 토큰 쌍의 현재 매수/매도 호가를 조회한다.

```bash
# Base WETH/USDC 오더북 예시
CHAIN_ID=8453
BASE_TOKEN="0x4200000000000000000000000000000000000006"   # WETH
QUOTE_TOKEN="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC

curl -s "https://api.clober.io/v2/orderbook?chain_id=$CHAIN_ID&base=$BASE_TOKEN&quote=$QUOTE_TOKEN" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const ob=JSON.parse(d);
  console.log('=== 매도 (Asks) ===');
  const asks=(ob.asks||[]).slice(0,10);
  asks.forEach(a=>console.log('Price:',a.price,'Amount:',a.amount));
  console.log('=== 매수 (Bids) ===');
  const bids=(ob.bids||[]).slice(0,10);
  bids.forEach(b=>console.log('Price:',b.price,'Amount:',b.amount));
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

> **EXPECTED:** asks(매도)와 bids(매수) 목록. 가격과 수량이 표시됨.
> **IF ERROR:** API 경로가 변경되었을 수 있다. firecrawl로 Clober docs를 확인.

### 1-3. 사용 가능한 마켓(토큰 쌍) 목록

```bash
curl -s "https://api.clober.io/v2/markets?chain_id=8453" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const markets=JSON.parse(d);
  if(Array.isArray(markets)){markets.slice(0,20).forEach(m=>console.log(m.base_symbol+'/'+m.quote_symbol,'—',m.base_address))}
  else{console.log('Response:',d.slice(0,1000))}
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

---

## 2. 내 열린 주문 조회 (Read — 무료)

### 2-1. Subgraph로 열린 주문 조회

```bash
CHAIN_ID=8453
# Subgraph URL — Base
SUBGRAPH_URL="https://subgraph.satsuma-prod.com/clober/book-manager-base/api"

QUERY='{"query":"{ openOrders(where: { owner: \"'$WALLET_ADDRESS'\" }) { id bookId tick rawAmount claimedAmount } }"}'

curl -s -X POST "$SUBGRAPH_URL" \
  -H "Content-Type: application/json" \
  -d "$QUERY" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const result=JSON.parse(d);
  const orders=result.data?.openOrders||[];
  if(orders.length===0){console.log('열린 주문 없음');return;}
  orders.forEach(o=>console.log('ID:',o.id,'Book:',o.bookId,'Tick:',o.tick,'Amount:',o.rawAmount,'Claimed:',o.claimedAmount));
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

> **EXPECTED:** 열린 주문 목록 또는 "열린 주문 없음".
> **참고:** subgraph 스키마는 변경될 수 있다. 에러 시 Clober docs에서 최신 스키마를 확인하라.

### 2-2. Clober API로 열린 주문 조회 (대안)

```bash
curl -s "https://api.clober.io/v2/orders/open?chain_id=$CHAIN_ID&owner=$WALLET_ADDRESS" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const orders=JSON.parse(d);
  if(Array.isArray(orders)&&orders.length===0){console.log('열린 주문 없음');return;}
  console.log(JSON.stringify(orders,null,2).slice(0,2000));
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

---

## 3. 지정가 주문 걸기 (Write — 가스비 발생)

### 전체 워크플로우

```
1. 토큰 주소 확인 (심볼 → 주소)
2. 오더북 확인 (현재 호가 파악)
3. 유저에게 주문 내용 확인 받기 ← 필수
4. 토큰 approve (Controller에게 지출 허용)
5. limit order 트랜잭션 전송
6. 결과 확인
```

### Step 3-1: 토큰 잔고 확인

```bash
CHAIN_ID=8453
TOKEN_ADDRESS="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC

# ERC-20 balanceOf(address) — selector: 0x70a08231
PADDED_ADDR=$(echo "$WALLET_ADDRESS" | sed 's/0x//' | awk '{printf "%064s", $0}' | tr ' ' '0')
CALL_DATA="0x70a08231${PADDED_ADDR}"

BALANCE_HEX=$(curl -s -X POST "https://mainnet.base.org" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"'$TOKEN_ADDRESS'","data":"'$CALL_DATA'"},"latest"],"id":1}' | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
console.log(JSON.parse(d).result||'0x0');
")

echo "Balance (hex): $BALANCE_HEX"

# USDC는 6 decimals
node -e "const b=BigInt('$BALANCE_HEX');console.log('Balance:',(Number(b)/1e6).toFixed(6),'USDC')"
```

> **참고:** RPC URL은 체인별로 다르다.
> - Base: `https://mainnet.base.org`
> - Arbitrum: `https://arb1.arbitrum.io/rpc`

### Step 3-2: 토큰 Approve

지정가 주문을 걸려면 Controller 컨트랙트에 토큰 사용을 허가해야 한다.

```bash
CHAIN_ID=8453
TOKEN_ADDRESS="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC
CONTROLLER="0x6e3a1BF0Cd3Ed8D3BA0e01cE98e8De03F41E2c5C"

# approve(address spender, uint256 amount) — selector: 0x095ea7b3
# MAX_UINT256 approve (무제한 — 한 번만 하면 됨)
SPENDER_PADDED=$(echo "$CONTROLLER" | sed 's/0x//' | awk '{printf "%064s", $0}' | tr ' ' '0')
AMOUNT_PADDED="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
APPROVE_DATA="0x095ea7b3${SPENDER_PADDED}${AMOUNT_PADDED}"

APPROVE_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "'$TOKEN_ADDRESS'",
    "data": "'$APPROVE_DATA'",
    "value": "0x0",
    "chainId": '$CHAIN_ID'
  }')
echo "Approve result: $APPROVE_RESULT"
```

> **EXPECTED:** `{"txHash":"0x..."}` — 트랜잭션 해시 반환.
> **IF ERROR:** 가스비 부족일 수 있다. ETH 잔고를 확인하라.

### Step 3-3: Clober API로 주문 Calldata 생성

Clober의 컨트랙트 호출은 복잡하므로 (tick 계산, book ID 등), API에서 calldata를 생성받는다.

```bash
CHAIN_ID=8453
BASE_TOKEN="0x4200000000000000000000000000000000000006"   # WETH (사려는 토큰)
QUOTE_TOKEN="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC (지불 토큰)
PRICE="3000.00"          # USDC 가격 (예: ETH를 $3000에 매수)
AMOUNT="0.1"             # 수량 (예: 0.1 ETH)
SIDE="bid"               # bid(매수) 또는 ask(매도)

# Clober API에서 트랜잭션 데이터 생성 요청
ORDER_RESULT=$(curl -s -X POST "https://api.clober.io/v2/limit" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": '$CHAIN_ID',
    "user_address": "'$WALLET_ADDRESS'",
    "base": "'$BASE_TOKEN'",
    "quote": "'$QUOTE_TOKEN'",
    "price": "'$PRICE'",
    "amount": "'$AMOUNT'",
    "side": "'$SIDE'"
  }')

echo "$ORDER_RESULT" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const r=JSON.parse(d);
  if(r.error){console.log('Error:',r.error);return;}
  console.log('To:',r.to);
  console.log('Data length:',r.data?.length||0);
  console.log('Value:',r.value||'0');
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

> **참고:** API 응답에 `to`, `data`, `value` 필드가 포함된다. 이것을 그대로 트랜잭션으로 전송한다.
> **API 경로가 다를 수 있다:** 에러 시 `https://api.clober.io/v2/order/limit`, `https://api.clober.io/v2/make` 등 대안 경로를 시도하거나 docs를 확인하라.

### Step 3-4: 주문 트랜잭션 전송

> **INSTRUCTION:** 반드시 유저에게 주문 내용을 보여주고 확인을 받은 후 실행하라.

유저에게 확인할 내용:
```
주문 요약:
- 체인: Base (8453)
- 유형: 매수 (bid) 지정가 주문
- 토큰: WETH/USDC
- 가격: $3,000.00
- 수량: 0.1 ETH
- 총 비용: ~$300 USDC + 가스비

진행할까요?
```

유저 확인 후:

```bash
# ORDER_RESULT에서 to, data, value 추출
TX_TO=$(echo "$ORDER_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).to||'')")
TX_DATA=$(echo "$ORDER_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data||'')")
TX_VALUE=$(echo "$ORDER_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).value||'0x0')")

SEND_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "'$TX_TO'",
    "data": "'$TX_DATA'",
    "value": "'$TX_VALUE'",
    "chainId": '$CHAIN_ID'
  }')
echo "Order result: $SEND_RESULT"
```

> **EXPECTED:** `{"txHash":"0x..."}` — 트랜잭션 해시.
> 블록 익스플로러에서 확인: `https://basescan.org/tx/<txHash>`

### Step 3-5: 트랜잭션 확인

```bash
TX_HASH="<위에서 받은 txHash>"
curl -s -X POST "https://mainnet.base.org" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":["'$TX_HASH'"],"id":1}' | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
const r=JSON.parse(d).result;
if(!r){console.log('Pending...');return;}
console.log('Status:',r.status==='0x1'?'SUCCESS':'FAILED');
console.log('Block:',parseInt(r.blockNumber,16));
console.log('Gas used:',parseInt(r.gasUsed,16));
"
```

---

## 4. 주문 클레임 (체결된 주문 수령)

지정가 주문이 체결되면 토큰을 클레임해야 지갑에 들어온다.

### Step 4-1: 클레임 가능한 주문 확인

```bash
# 열린 주문 조회 (Section 2 참고) 후 claimedAmount < rawAmount인 주문이 클레임 가능
curl -s "https://api.clober.io/v2/orders/claimable?chain_id=$CHAIN_ID&owner=$WALLET_ADDRESS" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const orders=JSON.parse(d);
  if(Array.isArray(orders)&&orders.length===0){console.log('클레임 가능한 주문 없음');return;}
  console.log(JSON.stringify(orders,null,2).slice(0,2000));
} catch(e){ console.log('Raw:',d.slice(0,1000)); }
"
```

### Step 4-2: 클레임 트랜잭션 생성 & 전송

```bash
# ORDER_IDS — 클레임할 주문 ID 배열 (Step 4-1에서 확인)
ORDER_IDS='["order-id-1","order-id-2"]'

CLAIM_RESULT=$(curl -s -X POST "https://api.clober.io/v2/claim" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": '$CHAIN_ID',
    "user_address": "'$WALLET_ADDRESS'",
    "order_ids": '$ORDER_IDS'
  }')

TX_TO=$(echo "$CLAIM_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).to||'')")
TX_DATA=$(echo "$CLAIM_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data||'')")

curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "'$TX_TO'",
    "data": "'$TX_DATA'",
    "value": "0x0",
    "chainId": '$CHAIN_ID'
  }'
```

---

## 5. 주문 취소 (미체결 주문 취소)

### Step 5-1: 취소할 주문 확인

Section 2의 열린 주문 조회로 취소할 주문 ID를 확인한다. 유저에게 목록을 보여주고 확인을 받는다.

### Step 5-2: 취소 트랜잭션 생성 & 전송

```bash
# ORDER_IDS — 취소할 주문 ID 배열
ORDER_IDS='["order-id-1"]'

CANCEL_RESULT=$(curl -s -X POST "https://api.clober.io/v2/cancel" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": '$CHAIN_ID',
    "user_address": "'$WALLET_ADDRESS'",
    "order_ids": '$ORDER_IDS'
  }')

TX_TO=$(echo "$CANCEL_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).to||'')")
TX_DATA=$(echo "$CANCEL_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data||'')")

curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "'$TX_TO'",
    "data": "'$TX_DATA'",
    "value": "0x0",
    "chainId": '$CHAIN_ID'
  }'
```

> **유저 확인 필수:** 취소 전 반드시 주문 내용을 보여주고 동의를 받아라.

---

## Workflow Examples

### 예시 1: "ETH를 $3,000에 매수 주문 걸어줘" (Base)

1. 지갑 주소 확인 (Step 0)
2. USDC 잔고 확인 (Step 3-1) — 충분한지 확인
3. WETH/USDC 오더북 확인 (Section 1-2) — 현재 호가 파악
4. 유저에게 주문 요약 제시:
   - "현재 ETH 매수 최우선가: $2,980, 매도 최우선가: $3,005"
   - "ETH를 $3,000에 0.1 ETH 매수 주문 → 비용 ~$300 USDC + 가스비"
5. 유저 확인 후 USDC approve (Step 3-2, 최초 1회만)
6. limit order calldata 생성 (Step 3-3)
7. 트랜잭션 전송 (Step 3-4)
8. 트랜잭션 확인 (Step 3-5)
9. "주문이 오더북에 등록되었습니다. 체결 시 클레임이 필요합니다."

### 예시 2: "내 열린 주문 보여줘"

1. 지갑 주소 확인 (Step 0)
2. 열린 주문 조회 (Section 2)
3. 결과 표로 요약:
   - 주문 ID, 토큰 쌍, 매수/매도, 가격, 수량, 체결율
4. 클레임 가능한 주문이 있으면 안내

### 예시 3: "체결된 주문 클레임해줘"

1. 클레임 가능 주문 확인 (Step 4-1)
2. 목록 표시 + 유저 확인
3. 클레임 트랜잭션 전송 (Step 4-2)
4. 트랜잭션 확인

### 예시 4: "주문 취소해줘"

1. 열린 주문 조회 (Section 2)
2. 취소할 주문 유저에게 확인
3. 취소 트랜잭션 전송 (Section 5)
4. 미체결 토큰이 지갑으로 반환됨을 안내

---

## API 경로 Fallback

Clober API 경로가 변경된 경우 아래 순서로 시도:

1. 위에 명시된 경로 호출
2. 실패 시 firecrawl로 `https://docs.clober.io` 스크래핑하여 최신 API 문서 확인
3. Clober GitHub (`https://github.com/clober-dex`) 의 SDK/MCP 서버 소스 참고

```bash
firecrawl.sh scrape "https://docs.clober.io"
```

---

## 규칙

1. **주문 전 반드시 유저 확인** — 금액, 가격, 수량을 보여주고 동의를 받아라.
2. **잔고 확인 필수** — 주문 전 토큰 잔고와 가스비(ETH) 잔고를 반드시 확인.
3. **오더북 먼저 확인** — 주문 가격을 정하기 전에 현재 호가를 보여줘라.
4. **jq 대신 `node -e`** — JSON 파싱은 반드시 `node -e` 패턴 사용.
5. **스크립트 파일 금지** — 모든 명령을 인라인으로 실행.
6. **$100 이상 주문 시 재확인** — 큰 금액은 한 번 더 확인.
7. **가스비 설명** — 온체인 트랜잭션에는 가스비가 발생함을 안내.
8. **체인 확인** — Base인지 Arbitrum인지 유저에게 반드시 확인.
9. **Approve는 1회** — 같은 토큰의 approve는 최초 1회만 필요. 이미 approve했으면 스킵.
10. **클레임 안내** — 지정가 주문 체결 후에는 클레임이 필요함을 반드시 안내.
11. **컨트랙트 주소 검증** — 최초 사용 시 docs.clober.io에서 최신 주소를 확인하는 것을 권장.
