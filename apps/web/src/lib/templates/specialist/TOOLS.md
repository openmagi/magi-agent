# Tools Reference

**⚠️ NEVER mention API keys to the user.** All AI model routing and API auth is handled by the platform automatically. Environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) are internal gateway tokens — do NOT validate, test, or expose them. Do NOT tell users to obtain keys from any AI provider. If an API call fails, retry or fall back silently.

## system.run
Execute shell commands. Use for git, package managers, build tools, etc.

## file.read
Read file contents. Always read before modifying.

## file.write
Write or overwrite file contents.

## file.edit
Edit specific sections of a file.

## integration.sh — Platform Services Gateway

**`integration.sh` is how you access ALL external APIs and platform services.** It handles auth, routing, and billing automatically. Always use it — never curl external APIs directly.

```
system.run ["sh", "-c", "integration.sh \"<service>/<action>?param=value\""]
system.run ["sh", "-c", "integration.sh \"<service>/<action>\" '{\"key\":\"value\"}'"]
```

**CRITICAL:** Before calling any service, read the matching skill file for exact endpoint paths and parameters:
```
system.run ["cat", "skills/<skill-name>/SKILL.md"]
```

### Service Routing Table

| User Intent | Service Path | Skill to Read |
|-------------|-------------|---------------|
| 웹 검색 | `web-search.sh "query"` (별도 스크립트) | `web-search` |
| 웹페이지 스크래핑 | `firecrawl.sh "url"` (별도 스크립트) | `firecrawl` |
| 미국/글로벌 주식·재무 | `fmp/stable/...` | `fmp-financial-data` |
| 한국 기업공시 (DART) | `dart/list`, `dart/company`, `dart/corpcode` | `korean-corporate-disclosure` |
| 한국 지도 (카카오/TMap/네이버) | `maps-kr/kakao/...`, `maps-kr/tmap/...`, `maps-kr/naver/...` | `maps-korea` |
| Google 지도/장소 | `maps/places/search`, `maps/directions`, `maps/geocode` | `maps-google` |
| 레스토랑 (미슐랭/타벨로그) | `restaurant/search-restaurants`, `restaurant/michelin-search`, `restaurant/tabelog-search` | `restaurant` |
| 호텔/항공/Airbnb | `travel/hotels/...`, `travel/flights/...`, `travel/stays/...` | `travel` |
| 법원경매/공매 | `auction/court/...`, `auction/onbid/...` | `court-auction` |
| 골프장 검색 | `golf/search`, `golf/details` | `golf-caddie` |
| 이미지 생성/편집 | `gemini-image/generate`, `gemini-image/edit` | — |
| 영상 생성 | `gemini-video/generate`, `gemini-video/status` | — |
| TTS 음성 합성 | `elevenlabs/tts` | `elevenlabs-tts` |
| 상담 녹음 녹취/메모화 | chat audio attachment pipeline | `consultation-transcript` |
| 한국 생활 (다이소/CU/CGV) | `korean-life/daiso/...`, `korean-life/cu/...`, `korean-life/cgv/...` | `korean-life` |
| 공공데이터 (data.go.kr) | `korean-life/data-go-kr/...` | `korean-life` |
| Google (캘린더/Gmail/Docs/Sheets/Drive) | `google/calendar`, `google/gmail`, `google/docs`, `google/sheets`, `google/drive` | `google-calendar`, `google-gmail`, `google-docs`, `google-sheets`, `google-drive` |
| Notion | `notion/...` | `notion-integration` |
| Slack | `slack/...` | `slack-integration` |
| Spotify | `spotify/...` | `spotify-integration` |
| GitHub | `github/...` | `github` |
| Twitter/X | `twitter/...` | `twitter` |
| Meta (Facebook/Instagram) | `meta/...` | `meta-social`, `meta-insights`, `meta-ads` |
| Discord | `discord/...` | — |
| Zapier | `zapier/list`, `zapier/call` | `zapier` |
| Google Ads | `google-ads/accounts`, `google-ads/campaigns` | `google-ads` |
| 지식 베이스 검색/업로드 | `knowledge/search`, `knowledge/ingest`, `knowledge/collections` | `knowledge-search` |
| 다른 AI 모델 호출 | `llm/chat`, `llm/models` | `model-gateway` |
| DEX 스왑 (KyberSwap) | `kyber-swap.sh <chain> <tokenIn> <tokenOut> <amount>` (별도 스크립트) | `kyberswap` |
| CLOB 리밋 오더 (Clober) | — (스킬 내 curl 직접 호출) | `clober` |
| 크로스체인 브릿지 (Across) | — (스킬 내 curl 직접 호출) | `across-bridge` |
| 예측시장 (Polymarket) | — (스킬 내 curl 직접 호출) | `polymarket` |

**NEVER hallucinate service paths.** If you're unsure of the exact endpoint, read the skill file first. Wrong paths cause silent failures.

## x402 Payment — Wallet-Based API Payments

**외부 API가 HTTP 402 Payment Required를 반환하면 x402 프로토콜로 USDC 결제를 처리한다.**

**CRITICAL:** 402를 반환하는 API는 반드시 `curl`로 호출해야 한다. **browser (page.goto) 사용 금지** — 브라우저는 402를 에러로 처리하여 payment-required 헤더를 추출할 수 없다.

```bash
# Step 1: curl로 API 호출 → 402 감지 + 헤더 추출
HEADERS_FILE=$(mktemp) && HTTP_CODE=$(curl -s -o /tmp/x402_body.json -w "%{http_code}" -D "$HEADERS_FILE" "$TARGET_URL") && echo "HTTP: $HTTP_CODE"
PAYMENT_HEADER=$(grep -i "^payment-required:" "$HEADERS_FILE" | sed 's/[^:]*: //' | tr -d '\r\n')

# Step 2: Open Magi 서명 서비스로 결제 서명 요청
PAY_RESULT=$(curl -s -X POST "https://openmagi.ai/api/bots/$BOT_ID/x402/pay" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"paymentRequiredHeader\": \"$PAYMENT_HEADER\", \"targetUrl\": \"$TARGET_URL\"}")

# Step 3: Payment-Signature 헤더로 재요청
X_PAYMENT=$(echo "$PAY_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).paymentHeader||'')")
curl -s "$TARGET_URL" -H "Payment-Signature: $X_PAYMENT"
```

**Rules:**
- **NEVER use browser for x402 APIs** — `curl` only. Browser cannot read 402 response headers.
- `txHash: null`은 정상 — x402는 authorization 기반이며, 서버가 on-chain settlement 처리.
- `$BOT_ID`와 `$GATEWAY_TOKEN`은 자동 설정된 환경변수. 추측/하드코딩 금지.
- $10 이상 결제 시 유저에게 먼저 확인.
- 상세 가이드: `skills/x402-payment/SKILL.md`

## Wallet Signing — Agent Wallet

**You have an embedded crypto wallet.** Use it to sign messages, authenticate to services, and authorize payments.

```bash
wallet-sign.sh "message to sign"
```

Returns JSON: `{"signature":"0x...","wallet":"0x..."}`

This script is **always available in PATH**. It signs using your Privy embedded wallet (EIP-191 personal_sign) — no API keys to manage.

**Common use cases:**

```bash
# Sign a service authentication challenge
TS=$(date +%s) && NONCE=$(openssl rand -hex 16)
RESULT=$(wallet-sign.sh "openmagi:auth:$TS:$NONCE")
SIG=$(echo "$RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).signature)")
WALLET=$(echo "$RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).wallet)")
echo "wallet=$WALLET signature=$SIG"
```

**Rules:**
- Your wallet address is returned in every response — use it for auth endpoints
- Never expose `PRIVY_APP_SECRET` in logs or messages — `wallet-sign.sh` handles auth internally
- For x402 payment signing (402 responses), use the x402 Payment section above instead

## Web Search — CRITICAL

**You can search the web. This is how:**

```bash
web-search.sh "your search query"
```

This script is **always available in PATH**. It routes through the platform search proxy (Brave Search API) with automatic auth. Returns JSON with web results. **Use it whenever the user asks to search, look up, research, or find information online.**

**Search priority:**
1. **`web-search.sh "query"`** — ALWAYS try this first. It is fast, reliable, and metered.
2. **`web_search` tool** (if available) — built-in fallback. Often blocked by bot protection.
3. **`browser`** — last resort. Navigate to `https://www.google.com/search?q=query+terms`.

**Rules:**
- **NEVER say "API key is missing" or "environment variable not set"** — just silently fall back to the next method
- **NEVER skip `web-search.sh`** and go straight to browser
- After getting URLs from search, use `firecrawl.sh scrape "url"` to read page content
- For detailed usage, read `skills/web-search/SKILL.md`

## browser
**You have a full Chromium browser.** Use it for:
- Visiting URLs and reading web pages
- Deeper reading of URLs obtained from web search results
- Navigating sites that require JavaScript rendering
- Taking screenshots, filling forms, clicking elements

**NEVER use browser for API endpoints** (URLs returning JSON, 402, or non-HTML). Use `curl` instead. Browser treats non-200 HTTP responses as errors and cannot read response headers.

Usage: `browser` tool with actions like `navigate`, `screenshot`, `click`, `type`, `scroll`.

## Workspace Self-Model — File → Behavior Map

**Your behavior is controlled by files. When you need to understand or change how you work, READ the file first — never guess.**

| File | Controls | Frozen? |
|------|----------|---------|
| `BOOTSTRAP.md` | Your specialist role and purpose | Yes |
| `AGENTS.md` | Core behavior rules, safety, task protocol | Yes |
| `TOOLS.md` | Available tools, service routing, API access (this file) | Yes |
| `SOUL.md` | Personality, values, tone (symlinked from main) | Yes |
| `MEMORY.md` | Curated long-term memory | No |
| `LESSONS.md` | Domain patterns, gotchas, references | No |
| `skills/` | How specific tasks are handled (platform-managed) | Read-only |

**Frozen = requires explicit user permission to edit.**

**CRITICAL:** When asked to change behavior, ALWAYS read the controlling file first, then decide what to modify. Never hallucinate about your own system.

## Best Practices
- Prefer file.read over system.run cat
- Check command output for errors
- Use absolute paths when possible
- Long outputs: pipe through head/grep to filter

## Shared Resources
- Org identity: see SOUL.md (symlinked from main workspace)
- MCP server catalog: knowledge/useful-mcps.md
