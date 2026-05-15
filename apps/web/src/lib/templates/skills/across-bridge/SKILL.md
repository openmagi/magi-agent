---
name: across-bridge
description: Use when user wants to bridge tokens cross-chain, move assets between chains, or use Across protocol. Triggers on "bridge", "cross-chain", "크로스체인", "브릿지", "체인 이동", "체인 전환", "Across", "브릿지 수수료", "bridge fee".
metadata:
  author: openmagi
  version: "1.0"
---

# Across Cross-Chain Bridge

Across Protocol을 사용하여 체인 간 토큰 브릿지를 수행한다.

## When to Use

- 유저가 토큰을 다른 체인으로 이동하고 싶을 때
- "ETH를 Arbitrum으로 보내줘", "USDC를 Base로 브릿지해줘" 등
- 크로스체인 수수료를 확인하고 싶을 때
- 브릿지 상태를 추적하고 싶을 때

## Supported Chains

| Chain | ChainId | SpokePool Address |
|-------|---------|-------------------|
| Ethereum | 1 | `0x5c7BCd6E7De5423a257D81B442095A1a6ced35C5` |
| Arbitrum | 42161 | `0xe35e9842fceaCA96570B734083f4a58e8F7C5f2A` |
| Optimism | 10 | `0x6f26Bf09B1C792e3228e5467807a900A503c0281` |
| Polygon | 137 | `0x9295ee1d8C5b022Be115A2AD3c30C72E34e7F096` |
| Base | 8453 | `0x09aea4b2242abC8bb4BB78D537A67a245A7bEC64` |
| Linea | 59144 | `0x7E63A5f1a8F0B4d0934B2f2327DAED3F6bb2ee75` |
| ZkSync | 324 | `0xE0B015E54d54fc84a6cB9B666099c46adE3335136` |

## Common Bridgeable Tokens (Ethereum Mainnet)

| Token | Address |
|-------|---------|
| WETH | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` |
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` |
| USDT | `0xdAC17F958D2ee523a2206206994597C13D831ec7` |
| WBTC | `0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599` |
| DAI | `0x6B175474E89094C44Da98b954EedeAC495271d0F` |

**참고:** 각 체인의 토큰 주소는 다르다. `available-routes` API로 정확한 주소를 조회하라.

## Fee 설명

Across 브릿지 수수료는 3가지로 구성된다:

- **LP Fee**: 유동성 제공자에게 지불. 브릿지 풀 이용 대가.
- **Relayer Capital Fee**: 릴레이어가 목적지 체인에서 즉시 토큰을 제공하는 대가.
- **Relayer Gas Fee**: 릴레이어가 목적지 체인에서 트랜잭션을 실행하는 가스비.
- **Total Relay Fee** = LP Fee + Relayer Capital Fee + Relayer Gas Fee

일반적으로 총 수수료는 0.1%~1% 수준이며, 체인과 토큰에 따라 다르다.

## Step 0: 환경변수 + 지갑 주소 확인

> **INSTRUCTION:** 환경변수와 지갑 주소를 확인하라.
> **RATIONALE:** 브릿지에는 `$BOT_ID`, `$GATEWAY_TOKEN`, 지갑 주소가 필요하다.

```bash
echo "BOT_ID=$BOT_ID" && echo "GATEWAY_TOKEN length=${#GATEWAY_TOKEN}"
```

```bash
WALLET_INFO=$(wallet-sign.sh "get-address") && echo "$WALLET_INFO"
```

> **EXPECTED:** `BOT_ID`는 UUID, `GATEWAY_TOKEN` length=36, wallet은 `0x...` 주소.
> **IF EMPTY:** 관리자에게 보고. 직접 추측하지 마라.

지갑 주소를 변수에 저장:

```bash
MY_WALLET=$(echo "$WALLET_INFO" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).wallet)")
echo "Wallet: $MY_WALLET"
```

## Step 1: 사용 가능한 라우트 조회

> **INSTRUCTION:** origin/destination 체인 간 브릿지 가능한 토큰을 조회하라.
> **RATIONALE:** 모든 토큰이 모든 체인 쌍에서 브릿지 가능한 건 아니다. 반드시 먼저 확인.

```bash
ORIGIN_CHAIN=1          # 예: Ethereum
DEST_CHAIN=42161        # 예: Arbitrum

ROUTES=$(curl -s "https://app.across.to/api/available-routes?originChainId=$ORIGIN_CHAIN&destinationChainId=$DEST_CHAIN")
echo "$ROUTES" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  const routes=JSON.parse(d);
  if(!Array.isArray(routes)){console.log('Error:',d);process.exit(1);}
  console.log('Available routes:');
  routes.forEach(r=>console.log('  '+r.originTokenSymbol+': '+r.originToken+' -> '+r.destinationToken+(r.isNative?' (native)':'')));
"
```

> **EXPECTED:** 라우트 목록 (WETH, USDC, USDT 등).
> **IF ERROR:** 해당 체인 쌍은 지원되지 않는다. 다른 경로를 제안하라.

## Step 2: 수수료 견적 조회

> **INSTRUCTION:** 브릿지할 토큰과 금액으로 수수료를 조회하라.
> **RATIONALE:** 수수료는 시장 상황에 따라 변한다. 실시간 조회 필수.

```bash
INPUT_TOKEN="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"   # origin 토큰 주소
OUTPUT_TOKEN="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"  # destination 토큰 주소
AMOUNT="1000000000000000000"   # wei 단위 (1 ETH = 10^18, 1 USDC = 10^6)

FEES=$(curl -s "https://app.across.to/api/suggested-fees?inputToken=$INPUT_TOKEN&outputToken=$OUTPUT_TOKEN&originChainId=$ORIGIN_CHAIN&destinationChainId=$DEST_CHAIN&amount=$AMOUNT&recipient=$MY_WALLET")

echo "$FEES" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  const f=JSON.parse(d);
  if(f.error){console.log('Error:',f.error);process.exit(1);}

  const amt=BigInt('$AMOUNT');
  const totalPct=Number(f.totalRelayFee.pct)/1e16;
  const totalAbs=BigInt(f.totalRelayFee.total);
  const lpPct=Number(f.lpFee.pct)/1e16;
  const capitalPct=Number(f.relayerCapitalFee.pct)/1e16;
  const gasPct=Number(f.relayerGasFee.pct)/1e16;
  const outputAmount=amt-totalAbs;

  console.log('=== Bridge Fee Breakdown ===');
  console.log('Input Amount:       '+amt.toString());
  console.log('LP Fee:             '+lpPct.toFixed(4)+'%');
  console.log('Relayer Capital Fee:'+capitalPct.toFixed(4)+'%');
  console.log('Relayer Gas Fee:    '+gasPct.toFixed(4)+'%');
  console.log('Total Fee:          '+totalPct.toFixed(4)+'% ('+totalAbs.toString()+' wei)');
  console.log('Output Amount:      '+outputAmount.toString());
  console.log('');
  console.log('SpokePool:          '+f.spokePoolAddress);
  console.log('Timestamp:          '+f.timestamp);
  console.log('Fill Deadline:      '+(f.timestamp+21600));
  console.log('Exclusive Relayer:  '+(f.exclusiveRelayer||'none'));
  console.log('Exclusivity Deadline: '+(f.exclusivityDeadline||0));
"
```

> **EXPECTED:** 수수료 내역 (%, 절대값)과 예상 수신 금액.
> **CRITICAL:** 총 수수료가 5% 이상이면 유저에게 경고하라. "수수료가 높습니다 (X%). 계속할까요?"
> **유저 확인 필수:** 수수료와 수신 예상 금액을 반드시 유저에게 보여주고 확인받아라.

## Step 3: 한도 확인

> **INSTRUCTION:** 브릿지 한도를 확인하여 금액이 범위 내인지 검증하라.

```bash
LIMITS=$(curl -s "https://app.across.to/api/limits?inputToken=$INPUT_TOKEN&outputToken=$OUTPUT_TOKEN&originChainId=$ORIGIN_CHAIN&destinationChainId=$DEST_CHAIN")

echo "$LIMITS" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  const l=JSON.parse(d);
  console.log('Min Deposit: '+l.minDeposit);
  console.log('Max Deposit: '+l.maxDeposit);
  console.log('Max Deposit Instant: '+l.maxDepositInstant);
  console.log('Max Deposit Short Delay: '+l.maxDepositShortDelay);
"
```

> **EXPECTED:** 최소/최대 브릿지 금액.
> **IF AMOUNT > maxDeposit:** 금액을 줄이라고 안내.
> **IF AMOUNT < minDeposit:** 최소 금액 이상으로 조정.

## Step 4: ERC-20 토큰 Approve (네이티브 ETH는 건너뛰기)

> **INSTRUCTION:** ERC-20 토큰을 브릿지할 경우, SpokePool 컨트랙트에 토큰 사용 승인을 보내라.
> **RATIONALE:** `depositV3`가 SpokePool이 토큰을 가져갈 수 있도록 allowance가 필요하다. 네이티브 ETH 브릿지는 이 단계를 건너뛴다.

SpokePool 주소를 설정한다 (origin 체인에 맞게):

```bash
# origin chainId에 따라 SpokePool 선택
SPOKE_POOL="0x5c7BCd6E7De5423a257D81B442095A1a6ced35C5"  # Ethereum (chainId 1)
# 42161: 0xe35e9842fceaCA96570B734083f4a58e8F7C5f2A
# 10:    0x6f26Bf09B1C792e3228e5467807a900A503c0281
# 137:   0x9295ee1d8C5b022Be115A2AD3c30C72E34e7F096
# 8453:  0x09aea4b2242abC8bb4BB78D537A67a245A7bEC64
# 59144: 0x7E63A5f1a8F0B4d0934B2f2327DAED3F6bb2ee75
# 324:   0xE0B015E54d54fc84a6cB9B666099c46adE3335136
```

ERC-20 approve calldata 생성:

```bash
APPROVE_DATA=$(node -e "
  // approve(address spender, uint256 amount)
  // selector: 0x095ea7b3
  const spender='$SPOKE_POOL'.slice(2).toLowerCase().padStart(64,'0');
  const amount=BigInt('$AMOUNT').toString(16).padStart(64,'0');
  console.log('0x095ea7b3'+spender+amount);
")
echo "Approve calldata: $APPROVE_DATA"
```

트랜잭션 전송:

```bash
APPROVE_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"to\":\"$INPUT_TOKEN\",\"data\":\"$APPROVE_DATA\",\"value\":\"0x0\",\"chainId\":$ORIGIN_CHAIN}")
echo "$APPROVE_RESULT"
```

> **EXPECTED:** txHash가 포함된 응답.
> **IF ERROR:** 잔고 부족 또는 가스비 부족. 유저에게 안내.

## Step 5: depositV3 실행

> **INSTRUCTION:** Across SpokePool에 `depositV3`를 호출하여 브릿지를 실행하라.
> **RATIONALE:** Step 2에서 받은 수수료 정보(timestamp, exclusiveRelayer, exclusivityDeadline)를 사용한다.

depositV3 calldata 생성:

```bash
DEPOSIT_DATA=$(node -e "
  // depositV3(address depositor, address recipient, address inputToken, address outputToken,
  //           uint256 inputAmount, uint256 outputAmount, uint256 destinationChainId,
  //           address exclusiveRelayer, uint32 quoteTimestamp, uint32 fillDeadline,
  //           uint32 exclusivityDeadline, bytes message)
  // selector: 0xe7a7ed02

  function addr(a){ return a.replace('0x','').toLowerCase().padStart(64,'0'); }
  function uint256(v){ return BigInt(v).toString(16).padStart(64,'0'); }
  function uint32(v){ return Number(v).toString(16).padStart(64,'0'); }

  const depositor=addr('$MY_WALLET');
  const recipient=addr('$MY_WALLET');
  const inputToken=addr('$INPUT_TOKEN');
  const outputToken=addr('$OUTPUT_TOKEN');

  const inputAmount=uint256('$AMOUNT');

  // outputAmount = inputAmount - totalRelayFee (from Step 2 FEES response)
  const fees=JSON.parse(\`$(echo "$FEES" | tr -d '\n' | sed "s/'/\\\\'/g")\`);
  const totalFee=BigInt(fees.totalRelayFee.total);
  const outAmt=BigInt('$AMOUNT')-totalFee;
  const outputAmount=uint256(outAmt.toString());

  const destinationChainId=uint256('$DEST_CHAIN');
  const exclusiveRelayer=addr(fees.exclusiveRelayer||'0x0000000000000000000000000000000000000000');
  const quoteTimestamp=uint32(fees.timestamp);
  const fillDeadline=uint32(fees.timestamp+21600);  // +6 hours
  const exclusivityDeadline=uint32(fees.exclusivityDeadline||0);

  // message: empty bytes (offset + length 0)
  const messageOffset=uint256('384');  // 12 * 32 bytes offset
  const messageLength=uint256('0');

  console.log('0xe7a7ed02'+depositor+recipient+inputToken+outputToken+inputAmount+outputAmount+destinationChainId+exclusiveRelayer+quoteTimestamp+fillDeadline+exclusivityDeadline+messageOffset+messageLength);
")
echo "depositV3 calldata: ${DEPOSIT_DATA:0:20}..."
```

**네이티브 ETH를 브릿지하는 경우** `value`에 금액을 설정하고, `inputToken`은 WETH 주소를 사용:

```bash
# ERC-20 브릿지: value=0x0
# 네이티브 ETH 브릿지: value=금액 (hex)
IS_NATIVE_ETH=false   # 네이티브 ETH면 true로 변경

if [ "$IS_NATIVE_ETH" = "true" ]; then
  TX_VALUE="0x$(node -e "console.log(BigInt('$AMOUNT').toString(16))")"
else
  TX_VALUE="0x0"
fi

DEPOSIT_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"to\":\"$SPOKE_POOL\",\"data\":\"$DEPOSIT_DATA\",\"value\":\"$TX_VALUE\",\"chainId\":$ORIGIN_CHAIN}")

echo "$DEPOSIT_RESULT" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  const r=JSON.parse(d);
  if(r.txHash){
    console.log('Bridge TX submitted!');
    console.log('TX Hash: '+r.txHash);
    console.log('Track: https://app.across.to/transactions');
  } else {
    console.log('Error:', JSON.stringify(r));
  }
"
```

> **EXPECTED:** txHash가 포함된 응답.
> **IF ERROR:** 가스비 부족, 잔고 부족, 또는 approve 미완료. 오류 메시지 확인 후 안내.

## Step 6: 브릿지 상태 추적

> **INSTRUCTION:** 브릿지 트랜잭션의 완료 상태를 조회하라.
> **RATIONALE:** Across 브릿지는 보통 1-5분 내 완료된다. 상태를 추적하여 유저에게 알려준다.

```bash
TX_HASH="0x..."   # Step 5에서 받은 txHash

STATUS=$(curl -s "https://app.across.to/api/deposit/status?originChainId=$ORIGIN_CHAIN&depositTxHash=$TX_HASH")
echo "$STATUS" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  const s=JSON.parse(d);
  console.log('Status: '+(s.status||'unknown'));
  if(s.fillTx) console.log('Fill TX: '+s.fillTx);
  if(s.destinationChainId) console.log('Destination Chain: '+s.destinationChainId);
"
```

> **EXPECTED:** `pending` → `filled` 순서로 상태가 바뀐다.
> **status=filled:** 브릿지 완료. 유저에게 알린다.
> **status=pending:** 1-2분 후 다시 조회.
> **status=expired:** fillDeadline이 지났다. 유저에게 Across support 안내.

## 예상 브릿지 소요 시간

| Route | 예상 시간 |
|-------|-----------|
| L1 → L2 (Ethereum → Arbitrum/Optimism/Base) | 1-3분 |
| L2 → L1 (Arbitrum → Ethereum) | 2-5분 |
| L2 → L2 (Arbitrum → Base) | 1-3분 |
| → Polygon | 2-5분 |
| → ZkSync/Linea | 3-10분 |

Across는 릴레이어가 즉시 목적지에서 토큰을 제공하므로, 일반 브릿지보다 훨씬 빠르다.

## 전체 Workflow 요약

```
1. 유저 요청: "1 ETH를 Arbitrum으로 브릿지해줘"
2. available-routes 조회 → 라우트 확인
3. suggested-fees 조회 → 수수료 계산
4. 유저에게 수수료 보여주고 확인 받기
   "수수료: 0.12% (0.0012 ETH), 수신 예상: 0.9988 ETH. 진행할까요?"
5. (ERC-20) approve → SpokePool에 토큰 승인
6. depositV3 → 브릿지 실행
7. deposit/status → 완료 확인
8. "브릿지 완료! TX: 0x... Arbitrum에서 0.9988 ETH를 수신했습니다."
```

## Rules

1. **수수료 확인 필수** — 브릿지 실행 전에 반드시 수수료를 조회하고 유저에게 보여줘라.
2. **수수료 5% 이상 경고** — 총 수수료가 5%를 넘으면 "수수료가 비정상적으로 높습니다" 경고.
3. **유저 확인 없이 브릿지 금지** — 금액과 수수료를 보여주고 명시적 동의를 받아라.
4. **잔고 확인** — origin 체인에 충분한 잔고 + 가스비가 있는지 확인하라.
5. **available-routes 먼저** — 지원되지 않는 토큰/체인 쌍으로 브릿지 시도하지 마라.
6. **한도 확인** — limits API로 최소/최대 금액 범위 내인지 검증하라.
7. **jq 대신 `node -e`** — JSON 파싱은 항상 node를 사용.
8. **환경변수 추측 금지** — `$BOT_ID`, `$GATEWAY_TOKEN`은 자동 설정된 값 그대로 사용.
9. **네이티브 ETH vs ERC-20 구분** — 네이티브 ETH는 approve 불필요, value에 금액 설정. ERC-20은 approve 필수, value=0.
10. **브릿지 후 상태 추적** — depositV3 후 반드시 상태를 조회하여 유저에게 결과를 알려라.
11. **fillDeadline은 +6시간** — quoteTimestamp + 21600초. 이 시간 내에 릴레이어가 처리한다.
12. **quote 유효시간 주의** — suggested-fees 응답의 timestamp는 약 10분간 유효. 오래된 quote로 deposit하면 실패할 수 있다.
