# Tools Reference

**⚠️ NEVER mention API keys to the user.** All AI model routing and API auth is handled by the platform automatically. Environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) are internal gateway tokens — do NOT validate, test, or expose them. Do NOT tell users to obtain keys from any AI provider. If an API call fails, retry or fall back silently.

## system.run
Execute shell commands. Use for git, package managers, build tools, etc.

### BANNED — System Package Installation
**NEVER run `apt-get`, `apt`, `yum`, `apk`, `pip install`, or any system package manager.** The filesystem is read-only (`readOnlyRootFilesystem`). These commands will always fail.

If you need to process files (PDF, DOCX, XLSX), use the `document-reader` skill → `document-worker` service. Do NOT try to install `poppler-utils`, `pdftotext`, `libreoffice`, or any other system package as a workaround.

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
| 한국 법령/판례 | `law/search`, `law/article`, `law/precedent`, `law/tool` | `korean-law-research` |
| 한국 세무/세법 | `tax/lookup` | `tax-regulation-research` |
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
| Instagram/X one-time browser reads | native `SocialBrowser` tool only | `social-browser` |
| Discord | `discord/...` | — |
| Zapier | `zapier/list`, `zapier/call` | `zapier` |
| Google Ads | `google-ads/accounts`, `google-ads/campaigns` | `google-ads` |
| 지식 베이스 검색 | `kb-search.sh "query"` (별도 스크립트) | `knowledge-search` |
| 지식 베이스 쓰기 | generated files → `FileDeliver(target="kb")`, markdown notes → `kb-write.sh --add/--update/--delete ...` | `knowledge-write` |
| 다른 AI 모델 호출 | `llm/chat`, `llm/models` | `model-gateway` |
| DEX 스왑 (KyberSwap) | `kyber-swap.sh <chain> <tokenIn> <tokenOut> <amount>` (별도 스크립트) | `kyberswap` |
| CLOB 리밋 오더 (Clober) | — (스킬 내 curl 직접 호출) | `clober` |
| 크로스체인 브릿지 (Across) | — (스킬 내 curl 직접 호출) | `across-bridge` |
| 예측시장 (Polymarket) | — (스킬 내 curl 직접 호출) | `polymarket` |

**NEVER hallucinate service paths.** If you're unsure of the exact endpoint, read the skill file first. Wrong paths cause silent failures.

**Social browser rule:** Use native `SocialBrowser` only after the user opens a one-time session in Dashboard > Integrations. Never ask for, store, replay, or infer social-network passwords.

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

## Agent Runner — Universal Subagent

**Spawn subagents with any model.** Use for research, analysis, translation, comparison, document generation, or any delegated task.

```bash
agent-run.sh "task description"
agent-run.sh --model google/gemini-3.1-pro-preview "task description"
agent-run.sh --context file.md --model openai/gpt-5.5 --max-turns 10 "task description"
```

`agent-run.sh` is always in PATH. It runs a full agent loop (multi-turn, ~200K context) routed through the platform. The subagent has access to file R/W, web search, platform services, and shell commands.

### Cross-Verification (교차검증)

When the user asks to verify, cross-check, or compare using multiple models:

```bash
# 1. Save the task prompt to a shared context file
cat > /tmp/xv-task.md << 'TASK'
[paste the document or task here]
TASK

# 2. Dispatch subagents with different models (capture outputs)
agent-run.sh --context /tmp/xv-task.md --model openai/gpt-5.5 --max-turns 5 "Analyze the document in context. Output structured findings." > /tmp/xv-gpt.txt
agent-run.sh --context /tmp/xv-task.md --model google/gemini-3.1-pro-preview --max-turns 5 "Analyze the document in context. Output structured findings." > /tmp/xv-gemini.txt
agent-run.sh --context /tmp/xv-task.md --model anthropic/claude-opus-4-6 --max-turns 5 "Analyze the document in context. Output structured findings." > /tmp/xv-opus.txt

# 3. Synthesize: read all outputs, compare agreements/disagreements, deliver consolidated result
```

**Guidelines:**
- Use **the same prompt** for all models so outputs are directly comparable
- Pass the source material via `--context` so each subagent sees identical input
- After collecting outputs, **you** (the main agent) synthesize — highlight where models agree, where they diverge, and your confidence assessment
- Use `--max-turns 5` per subagent to control costs (warn user if >3 models)

**Rules:**
- Write clear, self-contained prompts — the subagent has no conversation context
- Use `--context` to pass relevant files
- Use `--max-turns 5` for simple tasks to save credits
- Output goes to stdout — capture with `> /path/to/output.txt` if needed
- For multi-file coding, prefer `claude-agent.sh` (next section)

## Coding Agent — Complex Code Generation

**You have a professional coding agent.** For complex multi-file coding tasks, delegate to the coding agent instead of doing it yourself.

```bash
claude-agent.sh "Build a FastAPI TODO app with SQLite and Pydantic validation"
```

Runs the coding agent in headless mode, routed through api-proxy for credit billing. Has access to Bash, file read/write, grep, and glob tools. Project sandbox output goes under `/workspace/code/<project>/`.

**Project sandbox:** Before delegation, create or reuse `/workspace/code/<project>/`, initialize git there, and keep source files, dependencies, build outputs, and generated artifacts inside it. Docker-in-Docker, privileged containers, root operations, and host Docker socket mounts are unavailable; Docker files can be authored but not locally executed as verification.

**Usage:** Read `skills/complex-coding/SKILL.md` for the full PM workflow — analyze request, write detailed spec, invoke agent, review results, deliver to user.

**Rules:**
- Write a detailed spec before invoking — the agent has no conversation context
- Review results before delivering to user
- Warn user about credit usage for large tasks
- `claude-agent.sh` is always in PATH

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

## File Attachments — Sending Files to Users

**Choose the right skill based on the channel:**

| System context shows | Skill to use |
|----------------------|--------------|
| `[Channel: <name>]` present (web/mobile app) | **native `FileDeliver`** → upload first, then include returned attachment marker |
| No `[Channel: ...]` hint (Telegram-only bot) | `telegram-file-output` skill |

**Using the Telegram Bot API from a web/app channel silently delivers the file to the wrong destination — the user never sees it.** Always check for `[Channel: ...]` first.

Quick reference:

```json
FileDeliver({
  "artifactId": "<artifact id>",
  "target": "chat",
  "chat": { "channel": "general" }
})
```

Include the returned marker in your final reply text:
`[attachment:<id>:<filename>]`

The client renders it as an inline image or downloadable file card.

### Supported file types
- **Images:** jpg, png, gif, webp (rendered inline)
- **Documents:** pdf, txt, csv, md, html, json, docx, hwpx, xlsx, zip (rendered as file card)

### Rules
- Max file size: 50MB
- **NEVER use `message` tool's `send` action for file delivery in web/app channels** — it will always fail
- **NEVER call `api.telegram.org` when `[Channel: ...]` is present** — read `file-send` skill for the correct path

## browser
**You have native core-agent browser automation through the `Browser` tool.**
Use it for:
- Visiting URLs and reading web pages that need JavaScript rendering
- Deeper reading of URLs obtained from web search results
- Navigating interactive sites that require clicking, forms, or snapshots
- Taking workspace-safe screenshots

**NEVER use browser for API endpoints** (URLs returning JSON, 402, or non-HTML). Use `curl` instead. Browser treats non-200 HTTP responses as errors and cannot read response headers.

Usage: native `Browser` actions `create_session`, `open`, `snapshot`, `scrape`, `click`, `fill`, `scroll`, `screenshot`, and `close_session`.

Do not claim browser automation is unavailable unless the `Browser` tool returns a concrete runtime error. For login-gated, private, rate-limited, or high-volume platform data, identify the missing authorization, export, official API, or approved provider connector instead of pretending browser access can retrieve unavailable data.

## Workspace Self-Model — File → Behavior Map

**Your behavior is controlled by files. When you need to understand or change how you work, READ the file first — never guess.**

| File | Controls | Frozen? |
|------|----------|---------|
| `HEARTBEAT.md` | Autonomous heartbeat loop (what to do every ~55min, when to message) | Yes |
| `AGENTS.md` | Core behavior rules, safety, autonomy, file permissions, anti-patterns | Yes |
| `TOOLS.md` | Available tools, service routing, API access (this file) | Yes |
| `CLAUDE.md` | Memory architecture, cost optimization, context management | Yes |
| `SOUL.md` | Personality, values, tone | Yes |
| `IDENTITY.md` | Purpose, role description | Yes |
| `USER.md` | User preferences, profile, timezone, communication style | No |
| `MEMORY.md` | Learned patterns, persistent facts, decisions | No |
| `skills/` | How specific tasks are handled (platform-managed) | Read-only |
| `skills-learned/` | Bot-created reusable skills | No |
| `openclaw cron` (CLI) | Precisely timed scheduled tasks | CLI-managed |
| `plans/TASK-QUEUE.md` | Heartbeat-driven queued tasks | No |

**Frozen = requires explicit user permission to edit.** See `AGENTS.md` → File Permissions.

**CRITICAL:** When asked to change behavior, ALWAYS:
1. Identify the controlling file from this table
2. Read it (`system.run ["cat", "<file>"]`)
3. Check if the current config already matches the request
4. If frozen → ask user for permission. If updatable → modify and verify.
5. **Never say "알겠습니다" without actually reading + modifying the relevant file**

For full behavior change protocol → read `skills/meta-cognition/SKILL.md`.

## Cron Jobs — `openclaw cron` CLI

Schedule recurring tasks. The gateway manages execution.

```bash
# List jobs
openclaw cron list
# Add a job (runs in isolated session by default)
openclaw cron add --cron "*/30 * * * *" --message "check for updates" --name "my-task" --tz Asia/Seoul
# Remove / disable / enable
openclaw cron rm <jobId>
openclaw cron disable <jobId>
openclaw cron enable <jobId>
```

### Cron Delivery — Telegram vs App Channel

**Where the user creates the cron determines where the output should go.**

#### Auto-routing (default behavior)
**The CLI reads sessions.json and auto-detects the active session.** If you use `--announce` without `--target` or `--channel`, the CLI picks the right delivery based on which session was most recently updated:
- Most recent session is `agent:*:app:<channel>` → auto-routes to **`--channel <channel>`** (prevents Telegram leak when user is in web/mobile)
- Most recent session is Telegram → auto-routes to **`--target <chatId>`**

A warning is printed to stderr when auto-routing happens. **This means `--announce` alone is safe in most cases** — you don't need to remember chatIds or channel names.

#### From Telegram DM → `--announce` (optionally `--target`)
```bash
openclaw cron add --cron "0 18 * * *" --message "daily briefing" --name "my-briefing" --tz Asia/Seoul --announce
# or explicit:
openclaw cron add --cron "0 18 * * *" --message "daily briefing" --name "my-briefing" --tz Asia/Seoul --announce --target 6629171909
```
If you DO pass `--target`, it MUST be a numeric chat ID. `@usernames` are rejected — Telegram Bot API cannot resolve them. If auto-detect and fallback all fail, `cron add` exits with "no Telegram target resolved" — ask the user instead of guessing.

Look up chat ID manually: `grep -oE 'telegram:[0-9]+' ~/.openclaw/agents/main/sessions/sessions.json | head -1`

#### From App Channel (web/mobile) → use `--channel`
```bash
openclaw cron add --cron "0 19 * * *" --message "daily briefing" --name "my-briefing" --tz Asia/Seoul --channel daily-update
```
Channel name must be lowercase alphanumeric with dashes/underscores (the CLI rejects `@` prefixes and invalid characters). Use ONLY a channel the user has open — you see `[Channel: <name>]` in system context. Do NOT invent channel names from the bot's own name. `--channel` takes precedence over `--announce` when both are passed (but you typically just need `--channel` alone, since it already sets delivery mode).

#### How to decide
| Context | Flag | Delivery |
|---------|------|----------|
| User asked in **Telegram** DM | `--announce --target <chatId>` | Telegram message |
| User asked in **app channel** (web/mobile) | `--channel <channel-name>` | App channel post |
| Internal/operational cron | Neither | Silent (no delivery) |

**Rule:** Match the delivery to where the user made the request. Never use `--announce` for app channel requests — it sends to Telegram instead of the requested channel.

### `--announce` Flag — RESTRICTED

**`--announce` makes the cron output go DIRECTLY to the user's Telegram.** There is NO filter.

| Use Case | Use `--announce`? |
|----------|-------------------|
| Watchdog / health check | **NEVER** — internal operation |
| Memory maintenance | **NEVER** — internal operation |
| Error recovery | **NEVER** — internal operation |
| Scheduled news/content post to user (Telegram) | Yes + `--target` |
| Daily briefing requested in Telegram | Yes + `--target` |
| Daily briefing requested in app channel | **NO** — use `--channel` |

**Common mistake:** Adding `--announce` to a watchdog cron with "사용자에게 메시지 보내지 마" in the prompt. The prompt instruction is IGNORED — `--announce` forces delivery at the gateway level.

### Avoiding Duplicate Crons
Before adding a new cron, always run `openclaw cron list` first. If a similar job exists, update it with `openclaw cron edit` instead of creating a new one.

### Diagnosing Broken Crons — `openclaw cron doctor`
Lists crons with error state or suspicious delivery config (non-numeric `to`, malformed `channel`). Run this whenever a user reports "cron didn't deliver" or when heartbeat needs to self-audit scheduled work. Returns empty output = all healthy.

```bash
openclaw cron doctor          # human-readable
openclaw cron doctor --json   # structured (for programmatic checks)
```

## Best Practices
- Prefer file.read over system.run cat
- Check command output for errors
- Use absolute paths when possible
- Long outputs: pipe through head/grep to filter
