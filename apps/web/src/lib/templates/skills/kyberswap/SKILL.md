---
name: kyberswap
description: Use when the user asks to swap tokens, exchange crypto, trade on DEX, use KyberSwap, or mentions "토큰 교환", "스왑", "DEX 거래". Supports Ethereum, Base, Arbitrum, Polygon, Optimism, and other EVM chains.
metadata:
  author: openmagi
  version: "1.0"
---

# KyberSwap DEX Aggregator

KyberSwap DEX aggregator로 온체인 토큰 스왑을 실행한다. 여러 DEX에서 최적 경로를 찾아 best rate로 교환.

## When to Use

- 토큰 스왑 / 교환 요청 ("ETH를 USDC로 바꿔줘")
- DEX 거래 견적 조회 ("1 ETH → USDC 얼마?")
- KyberSwap 직접 언급
- 토큰 교환 비용 비교
- 멀티체인 스왑 (Ethereum, Base, Arbitrum, Polygon, Optimism 등)

## Prerequisites

- 봇 지갑에 스왑할 토큰 잔액 필요
- ERC-20 토큰 스왑 시 KyberSwap Router에 대한 allowance 필요 (스크립트가 자동 처리)
- Native token (ETH, MATIC 등) → ERC-20 스왑은 approve 불필요

## 지원 체인

| Chain | ID | Native Token | KyberSwap Chain Slug |
|-------|----|-------------|---------------------|
| Ethereum | 1 | ETH | `ethereum` |
| Base | 8453 | ETH | `base` |
| Arbitrum | 42161 | ETH | `arbitrum` |
| Polygon | 137 | POL | `polygon` |
| Optimism | 10 | ETH | `optimism` |
| BSC | 56 | BNB | `bsc` |
| Avalanche | 43114 | AVAX | `avalanche` |
| Linea | 59144 | ETH | `linea` |
| Sonic | 146 | S | `sonic` |
| Berachain | 80094 | BERA | `berachain` |

## 토큰 레지스트리

> **INSTRUCTION:** 아래 주소들을 참고하라. 여기에 없는 토큰은 KyberSwap Token API로 조회한다.
> **Native token address (모든 체인 동일):** `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE`

### Ethereum (chainId: 1)
| Token | Address | Decimals |
|-------|---------|----------|
| ETH | `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` | 18 |
| WETH | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` | 18 |
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | 6 |
| USDT | `0xdAC17F958D2ee523a2206206994597C13D831ec7` | 6 |
| DAI | `0x6B175474E89094C44Da98b954EedeAC495271d0F` | 18 |
| WBTC | `0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599` | 8 |

### Base (chainId: 8453)
| Token | Address | Decimals |
|-------|---------|----------|
| ETH | `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` | 18 |
| WETH | `0x4200000000000000000000000000000000000006` | 18 |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6 |
| USDbC | `0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA` | 6 |
| DAI | `0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb` | 18 |

### Arbitrum (chainId: 42161)
| Token | Address | Decimals |
|-------|---------|----------|
| ETH | `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` | 18 |
| WETH | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` | 18 |
| USDC | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | 6 |
| USDC.e | `0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8` | 6 |
| USDT | `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9` | 6 |
| WBTC | `0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f` | 8 |
| ARB | `0x912CE59144191C1204E64559FE8253a0e49E6548` | 18 |

### Polygon (chainId: 137)
| Token | Address | Decimals |
|-------|---------|----------|
| POL | `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` | 18 |
| WMATIC | `0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270` | 18 |
| USDC | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | 6 |
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | 6 |
| USDT | `0xc2132D05D31c914a87C6611C10748AEb04B58e8F` | 6 |
| WBTC | `0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6` | 8 |
| WETH | `0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619` | 18 |

### Optimism (chainId: 10)
| Token | Address | Decimals |
|-------|---------|----------|
| ETH | `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` | 18 |
| WETH | `0x4200000000000000000000000000000000000006` | 18 |
| USDC | `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85` | 6 |
| USDC.e | `0x7F5c764cBc14f9669B88837ca1490cCa17c31607` | 6 |
| USDT | `0x94b008aA00579c1307B0EF2c499aD98a8ce58e58` | 6 |
| WBTC | `0x68f180fcCe6836688e9084f035309E29Bf0A2095` | 8 |
| OP | `0x4200000000000000000000000000000000000042` | 18 |

## Quick Start — kyber-swap.sh

`kyber-swap.sh` 스크립트로 간편하게 스왑:

```bash
# 견적만 조회 (DRY_RUN 모드)
DRY_RUN=1 kyber-swap.sh base ETH USDC 0.01

# 실제 스왑 실행
kyber-swap.sh base ETH USDC 0.01

# slippage 지정 (기본 50bps = 0.5%)
kyber-swap.sh ethereum USDC WETH 100 100
```

**Usage:** `kyber-swap.sh <chain> <tokenIn> <tokenOut> <amount> [slippage_bps]`

- `chain`: 체인 slug (ethereum, base, arbitrum, polygon, optimism, bsc 등)
- `tokenIn`: 입력 토큰 심볼 (ETH, USDC 등) 또는 주소
- `tokenOut`: 출력 토큰 심볼 또는 주소
- `amount`: 입력 토큰 양 (human-readable, e.g. `0.5`, `100`)
- `slippage_bps`: slippage tolerance in basis points (기본: 50 = 0.5%)

## Workflow 1: 견적 조회 (Read-Only)

유저가 "얼마나 받을 수 있어?" 같은 견적 요청을 할 때.

### Step 1: 토큰 주소 확인

> **INSTRUCTION:** 위 토큰 레지스트리에서 주소를 찾는다. 없으면 Token API로 조회.
> **RATIONALE:** KyberSwap API는 토큰 주소가 필요하다. 심볼만으로는 호출 불가.

```bash
# 레지스트리에 없는 토큰 조회
curl -s "https://token-api.kyberswap.com/api/v1/public/tokens?chainIds=8453&name=AERO&isWhitelisted=true" | \
  node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  const t=j.data?.tokens?.[0];
  if(t) console.log(JSON.stringify({address:t.address,decimals:t.decimals,symbol:t.symbol}));
  else console.log('{\"error\":\"Token not found\"}');"
```

> **EXPECTED:** `{"address":"0x...","decimals":18,"symbol":"AERO"}`

### Step 2: 금액을 Wei로 변환

> **INSTRUCTION:** 입력 금액을 토큰의 decimals에 맞춰 wei 단위로 변환하라.
> **RATIONALE:** KyberSwap API는 wei (최소 단위) 문자열을 받는다.

```bash
AMOUNT_WEI=$(node -e "
  const amount = '0.5';
  const decimals = 18;
  const [whole, frac=''] = amount.split('.');
  const padded = (frac + '0'.repeat(decimals)).slice(0, decimals);
  console.log(BigInt(whole) * BigInt(10)**BigInt(decimals) + BigInt(padded));
")
echo "Amount in wei: $AMOUNT_WEI"
```

### Step 3: 경로 조회

> **INSTRUCTION:** KyberSwap routes API로 최적 스왑 경로를 조회하라.
> **RATIONALE:** 여러 DEX의 유동성을 합산하여 최적 경로를 제안한다.

```bash
ROUTE_RESP=$(curl -s "https://aggregator-api.kyberswap.com/base/api/v1/routes?tokenIn=0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE&tokenOut=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913&amountIn=$AMOUNT_WEI&source=clawy-bot" \
  -H "X-Client-Id: clawy-bot") && \
echo "$ROUTE_RESP" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  const r=j.data?.routeSummary;
  if(!r){console.log('Error:',j.message||'No route found');process.exit(1);}
  console.log('Input:',r.amountIn,'wei (\$'+r.amountInUsd+')');
  console.log('Output:',r.amountOut,'wei (\$'+r.amountOutUsd+')');
  console.log('Gas:',r.gas,'(\$'+r.gasUsd+')');
  console.log('Router:',j.data.routerAddress);
"
```

> **EXPECTED:** Input/Output 금액, gas 비용, router 주소 출력.
> **IF "No route found":** 토큰 쌍에 유동성이 없거나 지원하지 않는 체인. 유저에게 안내.

### Step 4: 유저에게 결과 제시

> **INSTRUCTION:** 견적 결과를 이해하기 쉬운 형태로 유저에게 보여줘라.

포맷:
```
Swap 견적:
- 입력: 0.5 ETH ($X,XXX)
- 출력: X,XXX USDC ($X,XXX)
- 가스비: ~$X.XX
- 체인: Base
```

## Workflow 2: 스왑 실행

유저가 실제 스왑을 요청할 때. **반드시 견적을 먼저 보여주고 유저 확인을 받아라.**

### Step 1-3: 견적 조회 (Workflow 1과 동일)

위 Step 1-3을 실행하여 `ROUTE_RESP`를 확보한다.

### Step 4: 유저 확인

> **INSTRUCTION:** 견적 결과를 보여주고 유저의 동의를 받아라. **$50 이상 스왑은 반드시 재확인.**
> **RATIONALE:** 온체인 트랜잭션은 취소 불가. 실수 방지를 위해 유저 확인 필수.

### Step 5: 토큰 안전성 확인 (허니팟 체크)

> **INSTRUCTION:** 출력 토큰이 레지스트리에 없는 경우, 반드시 허니팟/FOT 체크를 실행하라.
> **RATIONALE:** 악성 토큰은 구매 후 판매 불가 (honeypot) 또는 전송 시 수수료 차감 (fee-on-transfer)될 수 있다.

```bash
curl -s "https://token-api.kyberswap.com/api/v1/public/tokens/honeypot-fot-info?chainId=8453&address=0xTOKEN_ADDRESS" | \
  node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  const info=j.data;
  if(!info){console.log('No safety data available');process.exit(0);}
  if(info.isHoneypot) console.log('WARNING: HONEYPOT DETECTED - DO NOT SWAP');
  if(info.isFot) console.log('WARNING: Fee-on-transfer token (buy:'+info.buyFeeBps+'bps, sell:'+info.sellFeeBps+'bps)');
  if(!info.isHoneypot && !info.isFot) console.log('Token safety: OK');
"
```

> **IF HONEYPOT:** 스왑을 **즉시 중단**하고 유저에게 경고. 절대 진행하지 마라.
> **IF FOT:** 수수료 정보를 유저에게 안내하고 계속할지 확인.

### Step 6: Calldata 빌드

> **INSTRUCTION:** routes 응답의 `routeSummary`를 build API에 전달하여 실행 calldata를 생성하라.

```bash
WALLET_ADDR=$(wallet-sign.sh "get-address" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).wallet||'')")

ROUTE_SUMMARY=$(echo "$ROUTE_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.stringify(JSON.parse(d).data.routeSummary))")

BUILD_RESP=$(curl -s -X POST "https://aggregator-api.kyberswap.com/base/api/v1/route/build" \
  -H "X-Client-Id: clawy-bot" \
  -H "Content-Type: application/json" \
  -d "{\"routeSummary\":$ROUTE_SUMMARY,\"sender\":\"$WALLET_ADDR\",\"recipient\":\"$WALLET_ADDR\",\"slippageTolerance\":50,\"source\":\"clawy-bot\"}") && \
echo "$BUILD_RESP" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  if(j.data){console.log('Calldata ready. Router:',j.data.routerAddress,'Value:',j.data.transactionValue||'0');}
  else console.log('Build error:',j.message||JSON.stringify(j));
"
```

> **EXPECTED:** Calldata ready 메시지.

### Step 7: ERC-20 Allowance 확인 및 Approve

> **INSTRUCTION:** tokenIn이 ERC-20인 경우 (native token이 아닌 경우), router에 대한 allowance를 확인하고 부족하면 approve 트랜잭션을 보내라.
> **RATIONALE:** ERC-20 토큰은 스마트컨트랙트가 대신 전송하려면 사전 승인이 필요하다.
> **SKIP IF:** tokenIn이 `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` (native token)이면 이 단계를 건너뛴다.

```bash
TOKEN_IN="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC on Ethereum
ROUTER_ADDR=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data.routerAddress)")
CHAIN_ID=1

# allowance 확인 (allowance(owner,spender) selector: 0xdd62ed3e)
ALLOWANCE_DATA="0xdd62ed3e$(printf '%064s' "${WALLET_ADDR:2}" | tr ' ' '0')$(printf '%064s' "${ROUTER_ADDR:2}" | tr ' ' '0')"
ALLOWANCE_RESP=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/eth-call" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"to\":\"$TOKEN_IN\",\"data\":\"$ALLOWANCE_DATA\",\"chainId\":$CHAIN_ID}")

ALLOWANCE=$(echo "$ALLOWANCE_RESP" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  console.log(j.result ? BigInt(j.result).toString() : '0');
")

echo "Current allowance: $ALLOWANCE, Required: $AMOUNT_WEI"

# allowance가 부족하면 approve (max uint256)
if node -e "process.exit(BigInt('$ALLOWANCE')>=BigInt('$AMOUNT_WEI')?0:1)"; then
  echo "Allowance sufficient, skipping approve"
else
  echo "Approving router..."
  APPROVE_DATA="0x095ea7b3$(printf '%064s' "${ROUTER_ADDR:2}" | tr ' ' '0')ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  APPROVE_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
    -H "Authorization: Bearer $GATEWAY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"to\":\"$TOKEN_IN\",\"data\":\"$APPROVE_DATA\",\"value\":\"0\",\"chainId\":$CHAIN_ID}")
  echo "Approve tx: $APPROVE_RESULT"
fi
```

> **EXPECTED:** Allowance sufficient 또는 Approve tx hash 출력.

### Step 8: 스왑 트랜잭션 전송

> **INSTRUCTION:** build 응답의 calldata로 트랜잭션을 전송하라.
> **RATIONALE:** Privy wallet API를 통해 서명 + 전송이 한번에 처리된다.

```bash
TX_DATA=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.data.data)")
TX_VALUE=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.data.transactionValue||'0')")

SWAP_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"to\":\"$ROUTER_ADDR\",\"data\":\"$TX_DATA\",\"value\":\"$TX_VALUE\",\"chainId\":$CHAIN_ID}")

echo "$SWAP_RESULT" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);
  if(j.hash) console.log('Swap TX submitted:', j.hash);
  else console.log('Swap failed:', JSON.stringify(j));
"
```

> **EXPECTED:** `Swap TX submitted: 0x...`
> **IF FAILED:** 에러 메시지 확인. 잔액 부족, gas 부족 등일 수 있음.

### Step 9: 유저에게 결과 보고

> **INSTRUCTION:** 트랜잭션 해시와 블록 탐색기 링크를 제공하라.

체인별 블록 탐색기:
| Chain | Explorer |
|-------|----------|
| Ethereum | `https://etherscan.io/tx/` |
| Base | `https://basescan.org/tx/` |
| Arbitrum | `https://arbiscan.io/tx/` |
| Polygon | `https://polygonscan.com/tx/` |
| Optimism | `https://optimistic.etherscan.io/tx/` |
| BSC | `https://bscscan.com/tx/` |
| Avalanche | `https://snowtrace.io/tx/` |

## 에러 처리

| 에러 | 원인 | 대응 |
|------|------|------|
| `No route found` | 유동성 없음 또는 미지원 토큰 쌍 | 다른 경로 또는 다른 체인 제안 |
| `Insufficient balance` | 잔액 부족 | 유저에게 잔액 확인 안내 |
| `Insufficient gas` | 가스비용 ETH 부족 | native token 충전 필요 |
| `HONEYPOT detected` | 악성 토큰 | 스왑 즉시 중단 |
| `Allowance failed` | approve tx 실패 | 재시도 또는 수동 approve |
| `Slippage exceeded` | 가격 변동 | slippage 올리거나 재시도 |
| `Rate limit (429)` | API 요청 초과 | 잠시 대기 후 재시도 |

## 규칙

1. **견적 없이 스왑하지 마라** — 반드시 경로 조회 → 견적 제시 → 유저 확인 → 실행 순서.
2. **$50 이상 스왑은 재확인** — 금액, 토큰, 체인을 다시 한번 유저에게 보여주고 명시적 동의.
3. **허니팟 토큰 절대 스왑 금지** — 레지스트리에 없는 토큰은 반드시 안전성 체크.
4. **jq 대신 `node -e`** — JSON 파싱은 항상 node 사용.
5. **환경변수를 추측하지 마라** — `$BOT_ID`, `$GATEWAY_TOKEN` 등은 자동 설정된 값 사용.
6. **체인을 혼동하지 마라** — 토큰 주소는 체인별로 다르다. 반드시 올바른 체인의 주소 사용.
7. **Native token은 approve 불필요** — `0xEeee...` 주소면 Step 7 건너뛴다.
8. **슬리피지 기본 0.5%** — 유저가 별도 지정하지 않으면 50bps 사용. 스테이블코인 쌍은 10bps 권장.
9. **DRY_RUN=1로 견적만 조회 가능** — 스크립트에 DRY_RUN 환경변수 설정 시 트랜잭션 전송하지 않음.
10. **투자 자문 금지** — 스왑 실행만 담당. "이 토큰 사야 해?" 류의 질문에 조언하지 마라.
