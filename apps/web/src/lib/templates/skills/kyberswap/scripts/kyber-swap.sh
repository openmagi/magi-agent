#!/bin/sh
# KyberSwap DEX Aggregator — token swap via Privy wallet
# Usage: kyber-swap.sh <chain> <tokenIn> <tokenOut> <amount> [slippage_bps]
#   chain:        ethereum, base, arbitrum, polygon, optimism, bsc, avalanche, linea, sonic, berachain
#   tokenIn:      token symbol (ETH, USDC, etc.) or 0x address
#   tokenOut:     token symbol or 0x address
#   amount:       human-readable amount (e.g., 0.5, 100)
#   slippage_bps: slippage tolerance in basis points (default: 50 = 0.5%)
#
# Env required: BOT_ID, GATEWAY_TOKEN, PRIVY_APP_ID, PRIVY_APP_SECRET, PRIVY_WALLET_ID
# Env optional: DRY_RUN=1 (quote only, no transaction)
#
# Returns JSON: {"status":"success","txHash":"0x...","amountIn":"...","amountOut":"...","amountInUsd":"...","amountOutUsd":"...","gasUsd":"...","explorer":"..."}
# Or on error:  {"status":"error","message":"..."}

set -e

# ─── Args ────────────────────────────────────────────────────────────────
CHAIN="$1"
TOKEN_IN_SYM="$2"
TOKEN_OUT_SYM="$3"
AMOUNT="$4"
SLIPPAGE="${5:-50}"

if [ -z "$CHAIN" ] || [ -z "$TOKEN_IN_SYM" ] || [ -z "$TOKEN_OUT_SYM" ] || [ -z "$AMOUNT" ]; then
  echo '{"status":"error","message":"Usage: kyber-swap.sh <chain> <tokenIn> <tokenOut> <amount> [slippage_bps]"}'
  exit 1
fi

# ─── Env check ───────────────────────────────────────────────────────────
if [ -z "$BOT_ID" ] || [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"status":"error","message":"Missing BOT_ID or GATEWAY_TOKEN"}'
  exit 1
fi
if [ -z "$PRIVY_APP_ID" ] || [ -z "$PRIVY_APP_SECRET" ] || [ -z "$PRIVY_WALLET_ID" ]; then
  echo '{"status":"error","message":"Missing PRIVY_APP_ID, PRIVY_APP_SECRET, or PRIVY_WALLET_ID"}'
  exit 1
fi

NATIVE="0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
KYBER_BASE="https://aggregator-api.kyberswap.com"
TOKEN_API="https://token-api.kyberswap.com/api/v1/public/tokens"

# ─── Chain ID mapping ────────────────────────────────────────────────────
CHAIN_ID=$(node -e "
const m={ethereum:1,base:8453,arbitrum:42161,polygon:137,optimism:10,bsc:56,avalanche:43114,linea:59144,sonic:146,berachain:80094,mantle:5000};
const id=m['$CHAIN'];
if(!id){console.error('Unknown chain: $CHAIN');process.exit(1);}
console.log(id);
")

# ─── Explorer mapping ────────────────────────────────────────────────────
EXPLORER=$(node -e "
const m={ethereum:'https://etherscan.io/tx/',base:'https://basescan.org/tx/',arbitrum:'https://arbiscan.io/tx/',polygon:'https://polygonscan.com/tx/',optimism:'https://optimistic.etherscan.io/tx/',bsc:'https://bscscan.com/tx/',avalanche:'https://snowtrace.io/tx/',linea:'https://lineascan.build/tx/',sonic:'https://sonicscan.org/tx/',berachain:'https://berascan.com/tx/',mantle:'https://mantlescan.xyz/tx/'};
console.log(m['$CHAIN']||'');
")

# ─── Token address resolver ──────────────────────────────────────────────
resolve_token() {
  local SYM="$1"
  # If already an address, return as-is
  if echo "$SYM" | grep -q "^0x"; then
    echo "$SYM"
    return
  fi

  # Built-in registry lookup
  RESOLVED=$(node -e "
const registry = {
  ethereum: {
    ETH:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WETH:'0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    USDC:'0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    USDT:'0xdAC17F958D2ee523a2206206994597C13D831ec7',
    DAI:'0x6B175474E89094C44Da98b954EedeAC495271d0F',
    WBTC:'0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599'
  },
  base: {
    ETH:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WETH:'0x4200000000000000000000000000000000000006',
    USDC:'0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    USDbC:'0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA',
    DAI:'0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb'
  },
  arbitrum: {
    ETH:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WETH:'0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
    USDC:'0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
    'USDC.e':'0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
    USDT:'0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
    WBTC:'0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',
    ARB:'0x912CE59144191C1204E64559FE8253a0e49E6548'
  },
  polygon: {
    POL:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    MATIC:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WMATIC:'0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270',
    USDC:'0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    'USDC.e':'0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
    USDT:'0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
    WBTC:'0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6',
    WETH:'0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619'
  },
  optimism: {
    ETH:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WETH:'0x4200000000000000000000000000000000000006',
    USDC:'0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85',
    'USDC.e':'0x7F5c764cBc14f9669B88837ca1490cCa17c31607',
    USDT:'0x94b008aA00579c1307B0EF2c499aD98a8ce58e58',
    WBTC:'0x68f180fcCe6836688e9084f035309E29Bf0A2095',
    OP:'0x4200000000000000000000000000000000000042'
  },
  bsc: {
    BNB:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WBNB:'0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
    USDC:'0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',
    USDT:'0x55d398326f99059fF775485246999027B3197955',
    BUSD:'0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56',
    WETH:'0x2170Ed0880ac9A755fd29B2688956BD959F933F8'
  },
  avalanche: {
    AVAX:'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
    WAVAX:'0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7',
    USDC:'0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E',
    USDT:'0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7',
    WETH:'0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB'
  }
};
const sym='$SYM'.toUpperCase();
const addr=registry['$CHAIN']?.[sym];
if(addr) console.log(addr);
else console.log('');
")

  if [ -n "$RESOLVED" ]; then
    echo "$RESOLVED"
    return
  fi

  # Fallback: KyberSwap Token API
  RESOLVED=$(curl -s "${TOKEN_API}?chainIds=${CHAIN_ID}&name=${SYM}&isWhitelisted=true" | node -e "
    const d=require('fs').readFileSync('/dev/stdin','utf8');
    try {
      const j=JSON.parse(d);
      const t=j.data?.tokens?.[0];
      if(t) console.log(t.address);
      else console.log('');
    } catch { console.log(''); }
  ")

  if [ -z "$RESOLVED" ]; then
    echo ""
    return
  fi
  echo "$RESOLVED"
}

# ─── Token decimals resolver ─────────────────────────────────────────────
resolve_decimals() {
  local SYM="$1"
  local ADDR="$2"

  # Check built-in first
  DECIMALS=$(node -e "
const d={
  ethereum:{ETH:18,WETH:18,USDC:6,USDT:6,DAI:18,WBTC:8},
  base:{ETH:18,WETH:18,USDC:6,USDbC:6,DAI:18},
  arbitrum:{ETH:18,WETH:18,USDC:6,'USDC.e':6,USDT:6,WBTC:8,ARB:18},
  polygon:{POL:18,MATIC:18,WMATIC:18,USDC:6,'USDC.e':6,USDT:6,WBTC:8,WETH:18},
  optimism:{ETH:18,WETH:18,USDC:6,'USDC.e':6,USDT:6,WBTC:8,OP:18},
  bsc:{BNB:18,WBNB:18,USDC:18,USDT:18,BUSD:18,WETH:18},
  avalanche:{AVAX:18,WAVAX:18,USDC:6,USDT:6,WETH:18}
};
const sym='$SYM'.toUpperCase();
const dec=d['$CHAIN']?.[sym];
if(dec!==undefined) console.log(dec);
else console.log('');
")

  if [ -n "$DECIMALS" ]; then
    echo "$DECIMALS"
    return
  fi

  # Fallback: Token API
  DECIMALS=$(curl -s "${TOKEN_API}?chainIds=${CHAIN_ID}&address=${ADDR}&isWhitelisted=true" | node -e "
    const d=require('fs').readFileSync('/dev/stdin','utf8');
    try {
      const j=JSON.parse(d);
      const t=j.data?.tokens?.[0];
      if(t) console.log(t.decimals);
      else console.log('18');
    } catch { console.log('18'); }
  ")
  echo "${DECIMALS:-18}"
}

# ─── Resolve tokens ──────────────────────────────────────────────────────
TOKEN_IN=$(resolve_token "$TOKEN_IN_SYM")
if [ -z "$TOKEN_IN" ]; then
  echo "{\"status\":\"error\",\"message\":\"Cannot resolve token: $TOKEN_IN_SYM on $CHAIN\"}"
  exit 1
fi

TOKEN_OUT=$(resolve_token "$TOKEN_OUT_SYM")
if [ -z "$TOKEN_OUT" ]; then
  echo "{\"status\":\"error\",\"message\":\"Cannot resolve token: $TOKEN_OUT_SYM on $CHAIN\"}"
  exit 1
fi

DECIMALS_IN=$(resolve_decimals "$TOKEN_IN_SYM" "$TOKEN_IN")

# ─── Convert amount to wei ────────────────────────────────────────────────
AMOUNT_WEI=$(node -e "
const amount='$AMOUNT';
const decimals=$DECIMALS_IN;
const parts=amount.split('.');
const whole=parts[0]||'0';
const frac=(parts[1]||'').slice(0,decimals).padEnd(decimals,'0');
const wei=BigInt(whole)*BigInt(10)**BigInt(decimals)+BigInt(frac);
console.log(wei.toString());
")

if [ "$AMOUNT_WEI" = "0" ]; then
  echo '{"status":"error","message":"Amount must be greater than 0"}'
  exit 1
fi

# ─── Get route ────────────────────────────────────────────────────────────
ROUTE_RESP=$(curl -s "${KYBER_BASE}/${CHAIN}/api/v1/routes?tokenIn=${TOKEN_IN}&tokenOut=${TOKEN_OUT}&amountIn=${AMOUNT_WEI}&source=clawy-bot" \
  -H "X-Client-Id: clawy-bot")

# Parse route
ROUTE_PARSED=$(echo "$ROUTE_RESP" | node -e "
const d=require('fs').readFileSync('/dev/stdin','utf8');
try {
  const j=JSON.parse(d);
  if(j.code!==0 || !j.data?.routeSummary) {
    console.log(JSON.stringify({error:j.message||'No route found'}));
  } else {
    const r=j.data.routeSummary;
    console.log(JSON.stringify({
      ok:true,
      amountIn:r.amountIn,
      amountOut:r.amountOut,
      amountInUsd:r.amountInUsd,
      amountOutUsd:r.amountOutUsd,
      gas:r.gas,
      gasUsd:r.gasUsd,
      routerAddress:j.data.routerAddress
    }));
  }
} catch(e) { console.log(JSON.stringify({error:'Failed to parse route response'})); }
")

ROUTE_ERROR=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);if(j.error)console.log(j.error);else console.log('')")
if [ -n "$ROUTE_ERROR" ]; then
  echo "{\"status\":\"error\",\"message\":\"$ROUTE_ERROR\"}"
  exit 1
fi

AMOUNT_IN_USD=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).amountInUsd||'0')")
AMOUNT_OUT_USD=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).amountOutUsd||'0')")
AMOUNT_OUT=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).amountOut||'0')")
GAS_USD=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).gasUsd||'0')")
ROUTER_ADDR=$(echo "$ROUTE_PARSED" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).routerAddress||'')")

# ─── DRY_RUN: quote only ─────────────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  DECIMALS_OUT=$(resolve_decimals "$TOKEN_OUT_SYM" "$TOKEN_OUT")
  AMOUNT_OUT_HUMAN=$(node -e "
    const wei='$AMOUNT_OUT';const dec=$DECIMALS_OUT;
    const s=wei.padStart(dec+1,'0');
    const whole=s.slice(0,-dec)||'0';
    const frac=s.slice(-dec).replace(/0+$/,'')||'0';
    console.log(whole+'.'+frac);
  ")
  echo "{\"status\":\"quote\",\"amountIn\":\"$AMOUNT\",\"tokenIn\":\"$TOKEN_IN_SYM\",\"amountOut\":\"$AMOUNT_OUT_HUMAN\",\"tokenOut\":\"$TOKEN_OUT_SYM\",\"amountInUsd\":\"$AMOUNT_IN_USD\",\"amountOutUsd\":\"$AMOUNT_OUT_USD\",\"gasUsd\":\"$GAS_USD\",\"chain\":\"$CHAIN\"}"
  exit 0
fi

# ─── Get wallet address ──────────────────────────────────────────────────
WALLET_RESP=$(wallet-sign.sh "get-address")
WALLET_ADDR=$(echo "$WALLET_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');try{console.log(JSON.parse(d).wallet||'')}catch{console.log('')}")

if [ -z "$WALLET_ADDR" ]; then
  echo '{"status":"error","message":"Failed to get wallet address"}'
  exit 1
fi

# ─── Honeypot check (non-registry tokens) ────────────────────────────────
if [ "$TOKEN_OUT" != "$NATIVE" ]; then
  HP_CHECK=$(curl -s "https://token-api.kyberswap.com/api/v1/public/tokens/honeypot-fot-info?chainId=${CHAIN_ID}&address=${TOKEN_OUT}" | node -e "
    const d=require('fs').readFileSync('/dev/stdin','utf8');
    try {
      const j=JSON.parse(d);const info=j.data;
      if(!info){console.log('');return;}
      if(info.isHoneypot) console.log('HONEYPOT');
      else if(info.isFot) console.log('FOT:buy='+info.buyFeeBps+'bps,sell='+info.sellFeeBps+'bps');
      else console.log('');
    } catch { console.log(''); }
  ")

  if [ "$HP_CHECK" = "HONEYPOT" ]; then
    echo '{"status":"error","message":"HONEYPOT DETECTED — token cannot be sold after purchase. Swap aborted."}'
    exit 1
  fi
  # FOT tokens: proceed but info is available in the output
fi

# ─── Build calldata ───────────────────────────────────────────────────────
ROUTE_SUMMARY=$(echo "$ROUTE_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.stringify(JSON.parse(d).data.routeSummary))")

BUILD_RESP=$(curl -s -X POST "${KYBER_BASE}/${CHAIN}/api/v1/route/build" \
  -H "X-Client-Id: clawy-bot" \
  -H "Content-Type: application/json" \
  -d "{\"routeSummary\":$ROUTE_SUMMARY,\"sender\":\"$WALLET_ADDR\",\"recipient\":\"$WALLET_ADDR\",\"slippageTolerance\":$SLIPPAGE,\"source\":\"clawy-bot\"}")

BUILD_OK=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.data?.data?'1':'')")
if [ "$BUILD_OK" != "1" ]; then
  BUILD_ERR=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');const j=JSON.parse(d);console.log(j.message||JSON.stringify(j))")
  echo "{\"status\":\"error\",\"message\":\"Build failed: $BUILD_ERR\"}"
  exit 1
fi

TX_DATA=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data.data)")
TX_VALUE=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data.transactionValue||'0')")
TX_ROUTER=$(echo "$BUILD_RESP" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).data.routerAddress)")

# ─── ERC-20 Approve (skip for native token) ──────────────────────────────
if [ "$TOKEN_IN" != "$NATIVE" ]; then
  # Check allowance: allowance(address,address) = 0xdd62ed3e
  OWNER_PADDED=$(printf '%064s' "${WALLET_ADDR#0x}" | tr ' ' '0')
  SPENDER_PADDED=$(printf '%064s' "${TX_ROUTER#0x}" | tr ' ' '0')
  ALLOWANCE_DATA="0xdd62ed3e${OWNER_PADDED}${SPENDER_PADDED}"

  ALLOWANCE_RESP=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/eth-call" \
    -H "Authorization: Bearer $GATEWAY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"to\":\"$TOKEN_IN\",\"data\":\"$ALLOWANCE_DATA\",\"chainId\":$CHAIN_ID}")

  NEED_APPROVE=$(echo "$ALLOWANCE_RESP" | node -e "
    const d=require('fs').readFileSync('/dev/stdin','utf8');
    try {
      const j=JSON.parse(d);
      const allowance=j.result ? BigInt(j.result) : BigInt(0);
      const needed=BigInt('$AMOUNT_WEI');
      console.log(allowance>=needed?'0':'1');
    } catch { console.log('1'); }
  ")

  if [ "$NEED_APPROVE" = "1" ]; then
    # approve(address,uint256) = 0x095ea7b3 + spender + max_uint256
    APPROVE_DATA="0x095ea7b3${SPENDER_PADDED}ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

    APPROVE_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
      -H "Authorization: Bearer $GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"to\":\"$TOKEN_IN\",\"data\":\"$APPROVE_DATA\",\"value\":\"0\",\"chainId\":$CHAIN_ID}")

    APPROVE_HASH=$(echo "$APPROVE_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');try{console.log(JSON.parse(d).hash||'')}catch{console.log('')}")
    if [ -z "$APPROVE_HASH" ]; then
      APPROVE_ERR=$(echo "$APPROVE_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.stringify(JSON.parse(d)))" 2>/dev/null || echo "$APPROVE_RESULT")
      echo "{\"status\":\"error\",\"message\":\"Approve failed: $APPROVE_ERR\"}"
      exit 1
    fi
    # Brief wait for approve to confirm
    sleep 3
  fi
fi

# ─── Send swap transaction ────────────────────────────────────────────────
SWAP_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/wallet/send-transaction" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"to\":\"$TX_ROUTER\",\"data\":\"$TX_DATA\",\"value\":\"$TX_VALUE\",\"chainId\":$CHAIN_ID}")

TX_HASH=$(echo "$SWAP_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');try{console.log(JSON.parse(d).hash||'')}catch{console.log('')}")

if [ -z "$TX_HASH" ]; then
  SWAP_ERR=$(echo "$SWAP_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.stringify(JSON.parse(d)))" 2>/dev/null || echo "$SWAP_RESULT")
  echo "{\"status\":\"error\",\"message\":\"Swap transaction failed: $SWAP_ERR\"}"
  exit 1
fi

# ─── Format output ────────────────────────────────────────────────────────
DECIMALS_OUT=$(resolve_decimals "$TOKEN_OUT_SYM" "$TOKEN_OUT")
AMOUNT_OUT_HUMAN=$(node -e "
  const wei='$AMOUNT_OUT';const dec=$DECIMALS_OUT;
  const s=wei.padStart(dec+1,'0');
  const whole=s.slice(0,-dec)||'0';
  const frac=s.slice(-dec).replace(/0+$/,'')||'0';
  console.log(whole+'.'+frac);
")

echo "{\"status\":\"success\",\"txHash\":\"$TX_HASH\",\"amountIn\":\"$AMOUNT\",\"tokenIn\":\"$TOKEN_IN_SYM\",\"amountOut\":\"$AMOUNT_OUT_HUMAN\",\"tokenOut\":\"$TOKEN_OUT_SYM\",\"amountInUsd\":\"$AMOUNT_IN_USD\",\"amountOutUsd\":\"$AMOUNT_OUT_USD\",\"gasUsd\":\"$GAS_USD\",\"chain\":\"$CHAIN\",\"explorer\":\"${EXPLORER}${TX_HASH}\"}"
