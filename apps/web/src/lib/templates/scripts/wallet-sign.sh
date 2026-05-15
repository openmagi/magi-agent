#!/bin/sh
# Sign a message with the bot's Privy embedded wallet (EIP-191 personal_sign).
# Returns JSON: {"signature":"0x...","wallet":"0x..."}
#
# Usage:
#   wallet-sign.sh "message to sign"
#   wallet-sign.sh                    # reads from stdin
#
# Env required: PRIVY_APP_ID, PRIVY_APP_SECRET, PRIVY_WALLET_ID

set -e

MSG="$1"
if [ -z "$MSG" ]; then
  MSG=$(cat)
fi

if [ -z "$PRIVY_APP_ID" ] || [ -z "$PRIVY_APP_SECRET" ] || [ -z "$PRIVY_WALLET_ID" ]; then
  echo '{"error":"Missing PRIVY_APP_ID, PRIVY_APP_SECRET, or PRIVY_WALLET_ID"}'
  exit 1
fi

if [ -z "$MSG" ]; then
  echo '{"error":"No message provided"}'
  exit 1
fi

# Convert message to hex (EIP-191 personal_sign expects hex-encoded message)
HEX_MSG="0x$(printf '%s' "$MSG" | od -A n -t x1 | tr -d ' \n')"

AUTH=$(printf '%s:%s' "$PRIVY_APP_ID" "$PRIVY_APP_SECRET" | base64 | tr -d '\n')

# Get wallet address
WALLET_RESP=$(curl -s "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID" \
  -H "Authorization: Basic $AUTH" \
  -H "privy-app-id: $PRIVY_APP_ID" \
  -H "Content-Type: application/json")

WALLET_ADDR=$(echo "$WALLET_RESP" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  try { console.log(JSON.parse(d).address || ''); } catch { console.log(''); }
")

if [ -z "$WALLET_ADDR" ]; then
  echo "{\"error\":\"Failed to get wallet address\",\"detail\":$(echo "$WALLET_RESP" | head -c 200)}"
  exit 1
fi

# Sign the message
SIGN_RESP=$(curl -s -X POST "https://api.privy.io/v1/wallets/$PRIVY_WALLET_ID/rpc" \
  -H "Authorization: Basic $AUTH" \
  -H "privy-app-id: $PRIVY_APP_ID" \
  -H "Content-Type: application/json" \
  -d "{\"method\":\"personal_sign\",\"params\":{\"message\":\"$HEX_MSG\"}}")

SIGNATURE=$(echo "$SIGN_RESP" | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf8');
  try { const p=JSON.parse(d); console.log((p.data && p.data.result) || ''); } catch { console.log(''); }
")

if [ -z "$SIGNATURE" ]; then
  echo "{\"error\":\"Signing failed\",\"detail\":$(echo "$SIGN_RESP" | head -c 200)}"
  exit 1
fi

echo "{\"signature\":\"$SIGNATURE\",\"wallet\":\"$WALLET_ADDR\"}"
