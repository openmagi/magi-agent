---
name: loop
description: Use when the user wants to repeat a task on an interval until a goal is met, OR stop/cancel a running loop. Recognizes /loop commands and natural language like "매 5분마다 확인해줘", "check every 10 minutes", "keep monitoring until done", "모니터링 종료해", "루프 멈춰", "stop monitoring", "cancel the loop", "그만 확인해", "이제 됐어".
metadata:
  author: openmagi
  version: "2.0"
---

# Loop — Recurring Task Execution

Run a task repeatedly at a fixed interval until a goal is met, using OpenClaw's native cron system via the `openclaw cron` CLI.

## When to Use

- User says `/loop <interval> <goal>`
- User says "매 5분마다 ...", "check every 10 minutes ...", "keep monitoring until ..."
- User wants something done repeatedly until a condition is satisfied
- User wants periodic monitoring, polling, or progress tracking

## Creating a Loop

### Step 1: Parse the Request

Extract two things:
- **Interval**: `1m`, `5m`, `10m`, `30m`, `1h` etc. Convert to **minutes** (integer).
- **Goal**: What to accomplish or monitor.

Natural language mapping:
- "매 5분" / "every 5 minutes" / "5분 간격" / "5분마다" → `5` (minutes)
- "매시간" / "every hour" / "1시간마다" → `60` (minutes)

**Limits (enforce before creating):**
- Minimum interval: **1 minute**
- Maximum concurrent loops: **3** (check `plans/loops/` for RUNNING loops)
- If either limit violated, inform user and do not create.

### Step 2: Determine Max Iterations

Default: **20**. Adjust based on context:
- Short interval (1-5m) + monitoring task → 20
- Long interval (30m-1h) + open-ended task → 48
- User specifies explicitly → use their value

Inform user before starting:
> "5분 간격으로 최대 20회 (약 1시간 40분) 반복합니다. 시작합니다."

If user's tone is urgent ("바로 돌려", "지금 시작해"), skip confirmation and start immediately.

### Step 3: Create State File

Create `plans/loops/<jobId>.md` (use a short unique id, e.g., `loop-a1b2`):

```markdown
# Loop: <goal summary>
- JobId: <jobId>
- CronId: (filled after cron creation)
- Interval: <human readable>
- Max Iterations: <N>
- Created: <ISO timestamp>
- Status: RUNNING

## Goal
<Full goal description from user>

## Context
<Any relevant context: file paths, URLs, commands, previous conversation details that the isolated session will need>

## Iteration Log
(empty — iterations will be appended here)
```

**IMPORTANT:** The Context section must contain everything an isolated session needs to execute the task. The isolated session starts fresh with no conversation history — this file is its only context.

### Step 4: Create Cron Job

**Always check existing crons first** to avoid duplicates:

```
system.run ["openclaw", "cron", "list", "--json"]
```

#### Delivery routing

**The CLI auto-detects the active session.** `--announce` alone routes to whichever session was most recently active:
- User in web/mobile app channel (`agent:*:app:<name>` session) → auto-routes to `--channel <name>`, prevents Telegram leak
- User in Telegram → auto-routes to Telegram chat ID from `deliveryContext`

A warning is printed to stderr when auto-routing fires. **In most cases `--announce` alone is correct.** Explicit flags override auto-detect:

- `--target <chatId>` — forces Telegram delivery to a specific numeric chat ID. `@usernames` are rejected. Look up: `grep -oE 'telegram:[0-9]+' ~/.openclaw/agents/main/sessions/sessions.json | head -1`
- `--channel <name>` — forces app-channel delivery. Channel must be lowercase alphanumeric with dashes/underscores. Only use a channel the user currently has open (`[Channel: <name>]` visible in system context).

#### Default: just use `--announce`

**In nearly all cases, pass only `--announce`.** The CLI detects where the user currently is and routes accordingly — chat channel when they're in web/mobile, Telegram when they're in Telegram. Do NOT hardcode `--target <chatId>` from IDENTITY.md; it will override auto-detect when the active session is Telegram but be ignored when the active session is an app channel, which is confusing.

#### Templates

Standard loop — auto-routed delivery:

```
system.run ["openclaw", "cron", "add", "--every", "<minutes>", "--name", "Loop: <goal summary>", "--message", "[Loop <jobId>] Read plans/loops/<jobId>.md, then execute the next iteration. Follow the loop skill instructions in skills/loop/SKILL.md.", "--session", "isolated", "--announce"]
```

For tasks requiring deep analysis, add `--model sonnet`:

```
system.run ["openclaw", "cron", "add", "--every", "<minutes>", "--name", "Loop: <goal summary>", "--message", "[Loop <jobId>] ...", "--session", "isolated", "--announce", "--model", "sonnet"]
```

#### Explicit overrides (rare)

Only when the user is asking for delivery to somewhere OTHER than their current session:

- `--channel <name>` — force post to a specific app channel. Channel must be lowercase alphanumeric with dashes/underscores. Omits `--announce`.
- `--target <chatId>` — force Telegram delivery to a specific numeric chat ID (paired with `--announce`). `@usernames` are rejected. Look up a chatId: `grep -oE 'telegram:[0-9]+' ~/.openclaw/agents/main/sessions/sessions.json | head -1`

**If `cron add` exits non-zero** with "no Telegram target resolved": the CLI could not detect any active session. Do NOT retry blindly. Ask the user which chat they want results in, or run `openclaw cron doctor` to inspect existing broken configs.

**Parse the returned cron job ID** from the output and update the state file's `CronId` field.

### Step 5: Confirm to User

> "Loop 시작: 5분 간격으로 '배포 완료 확인' 실행합니다 (최대 20회). `/loop stop`으로 중지 가능."

---

## Executing an Iteration (Isolated Session)

When you see `[Loop <jobId>]` in your prompt, you are running inside a loop iteration.

### Iteration Protocol

1. **Read state file**: `plans/loops/<jobId>.md`
2. **Check iteration count**: Count entries in Iteration Log. If `>= Max Iterations`:
   - Append final log entry: `### #N (<time>) — LIMIT_REACHED`
   - Update Status to `LIMIT_REACHED`
   - Remove the cron: `system.run ["openclaw", "cron", "rm", "<cronId>"]`
   - Announce: "Loop 종료: 최대 반복 횟수에 도달했습니다. [마지막 상태 요약]"
   - **STOP.**
3. **Execute**: Perform one action toward the goal. Use available tools (system.run, web_search, web_fetch, integration.sh, etc.)
4. **Evaluate result**:
   - **Goal met** → Append `### #N (<time>) — COMPLETE`, update Status to `COMPLETE`, remove cron via `system.run ["openclaw", "cron", "rm", "<cronId>"]`, announce result.
   - **Meaningful progress or change** → Append log entry with details, announce the change to user.
   - **No change** → Append brief log entry. Do NOT announce (stay silent).
   - **Error** → Append error details to log. If recoverable, continue. If permanent, update Status to `ERROR`, remove cron, announce error.
5. **Write state file**: Always update `plans/loops/<jobId>.md` with the new iteration log entry before finishing.

### Announcement Guidelines

- **Always announce**: Goal completion, errors, limit reached, significant state changes
- **Never announce**: "Still checking...", "No change yet", routine progress with no new information
- **When in doubt**: Don't announce. Users prefer silence over noise.

### Model Selection

Default model is `clawy-smart-router/auto`. For simple tasks (status checks, API calls, comparisons) this is fine.

If the goal requires deep analysis, reasoning, or complex judgment, specify `--model sonnet` when creating the cron job in Step 4.

---

## Managing Loops

### Recognizing Stop/List Intent

Users will rarely say "크론잡 삭제해" — they'll use natural language. **Any of these mean "stop the loop":**

- 🇰🇷 "모니터링 종료해", "루프 멈춰", "그만 확인해", "반복 중지", "이제 됐어", "그거 꺼줘", "더 이상 안 해도 돼", "확인 그만해", "루프 끝내"
- 🇺🇸 "stop monitoring", "cancel the loop", "stop checking", "kill it", "that's enough", "stop the task", "end the loop", "no more checks"
- 🇯🇵 "モニタリング止めて", "ループ止めて", "もう確認しなくていい", "終了して"
- 🇨🇳 "停止监控", "关掉循环", "不用再检查了"
- 🇪🇸 "detén el monitoreo", "para el loop", "deja de revisar"

**Rule:** If the user's message implies "stop doing that recurring thing," treat it as a loop stop request. Don't wait for exact commands. When in doubt, check `plans/loops/` for RUNNING loops and ask if they want to stop it.

Similarly, **these mean "list loops":**
- "뭐 돌고 있어?", "루프 상태", "반복 작업 뭐 있어?", "what's running?", "loop status", "active loops?"

### `/loop list` or any list-intent phrase

1. Read all files in `plans/loops/`
2. Filter for `Status: RUNNING`
3. Also verify against live crons: `system.run ["openclaw", "cron", "list", "--json"]`
4. Present:
   ```
   Active loops:
   1. [loop-a1b2] 배포 완료 확인 — 5m interval, iteration 7/20
   2. [loop-c3d4] 환율 모니터링 — 1h interval, iteration 3/48
   ```

### `/loop stop` or any stop-intent phrase

If only one active loop: stop it immediately (no confirmation needed).
If multiple: ask which one, or stop the most recent.

1. Remove the cron: `system.run ["openclaw", "cron", "rm", "<cronId>"]`
2. Update state file: `Status: STOPPED`
3. Append: `### #N (<time>) — STOPPED (user requested)`
4. Confirm: "Loop 중지됨: '배포 완료 확인'"

### `/loop stop <jobId>` or `/loop stop all`

- Specific: remove that cron and update state file
- All: iterate through all RUNNING loops and stop each

---

## Examples

### Simple Monitoring
```
User: /loop 5m 배포 완료될 때까지 확인해줘
Bot: Loop 시작: 5분 간격으로 '배포 완료 확인' 실행합니다 (최대 20회). /loop stop으로 중지 가능.

→ system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Loop: 배포 완료 확인", "--message", "[Loop loop-a1b2] Read plans/loops/loop-a1b2.md, then execute the next iteration. Follow the loop skill instructions in skills/loop/SKILL.md.", "--session", "isolated", "--announce"]

[5분 후, isolated session]
→ system.run kubectl get pods → 3/10 ready → silent, log only

[10분 후]
→ 7/10 ready → silent, log only

[15분 후]
→ 10/10 ready → announce: "배포 완료! 모든 10개 pod가 Ready 상태입니다."
→ system.run ["openclaw", "cron", "rm", "<cronId>"]
```

### Price Monitoring
```
User: 매시간 비트코인 가격 확인해서 $100k 넘으면 알려줘
Bot: Loop 시작: 1시간 간격으로 'BTC $100k 돌파 모니터링' (최대 48회, 약 2일). /loop stop으로 중지.

→ system.run ["openclaw", "cron", "add", "--every", "60", "--name", "Loop: BTC $100k 모니터링", "--message", "[Loop loop-c3d4] ...", "--session", "isolated", "--announce"]

[iterations 1-5: $98k-$99k → silent]
[iteration 6: $100.2k → announce: "BTC $100k 돌파! 현재 $100,200" → cron rm]
```

### Periodic Report
```
User: /loop 6h 주요 뉴스 요약해서 알려줘
Bot: Loop 시작: 6시간 간격으로 '뉴스 요약 리포트' (최대 20회, 약 5일). /loop stop으로 중지.

→ system.run ["openclaw", "cron", "add", "--every", "360", "--name", "Loop: 뉴스 요약 리포트", "--message", "[Loop loop-e5f6] ...", "--session", "isolated", "--announce", "--model", "sonnet"]

[매 iteration: web_search로 뉴스 수집 → 주요 뉴스 있으면 announce, 특이사항 없으면 silent]
```
