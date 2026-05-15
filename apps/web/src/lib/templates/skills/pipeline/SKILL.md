---
name: pipeline
description: "Generic multi-step workflow orchestrator with cron-based auto-progression. Parses natural language into step plans, executes parallel steps via subagents, converges results automatically. Triggers: '/pipeline', '파이프라인', '단계별로', 'step by step workflow', '각각 나눠서', '결과를 합쳐서', '자동으로 진행', multi-step requests with 3+ sequential/parallel phases. Also handles: '/pipeline status', '/pipeline stop', '/pipeline resume', '/pipeline list', '파이프라인 멈춰', '어디까지 됐어'."
user_invocable: true
metadata:
  author: openmagi
  version: "1.1"
---

# Pipeline — Multi-Step Workflow Orchestrator

Orchestrates complex multi-step workflows via cron-based auto-progression. Supports parallel execution (subagents), convergence steps, quality-based iteration loops, and summary chain for efficient context passing.

## CRITICAL RULES — READ FIRST

1. **You MUST create a cron job.** There is NO "push-based", "callback", or "auto-notification" mechanism in OpenClaw. The ONLY way to auto-progress between steps is a cron that polls `state.json` every 5 minutes. Without a cron, the pipeline DIES after the current session ends.
2. **Cron is the orchestrator.** It reads state.json, detects completed steps, and executes the next step. This is non-negotiable.
3. **Subagents are fire-and-forget.** When you delegate parallel steps to subagents, they write their output files and exit. Nobody "notifies" you when they finish. The cron must check for output files on the next tick.
4. **Never claim "자동 알림" or "완료되면 알려드립니다" without a cron.** If you haven't created a cron, you cannot make this promise.
5. **Every user-facing cron needs a delivery flag — either `--announce` or `--channel <name>`.** The CLI auto-routes `--announce` based on the most recently active session (app channel → channel, Telegram → chatId). Explicit `--channel <name>` overrides auto-detect and is preferred when the user is in an app channel. Without any delivery flag, the cron runs silently (internal/operational only).
6. **If cron creation fails, retry with corrected syntax.** Do NOT fall back to "manual execution" or skip cron. Fix the command and try again.
7. **Subagent "pairing required" warnings are NORMAL.** When running `openclaw agent`, you may see `gateway connect failed: pairing required` in stderr. This is expected — the subagent automatically falls back to embedded mode and works correctly. Do NOT treat this as a failure. Check the actual JSON result for `payloads[0].text` to confirm success.
8. **Never give up and propose "manual mode".** If something fails, diagnose and fix. The pipeline MUST run autonomously via cron. "Manual mode" defeats the entire purpose of this skill.

## When to Use

**Starting a pipeline — user describes:**
- Multi-step workflow with 3+ phases: "~한 다음 ~하고 ~해줘"
- Parallel work: "각각 나눠서", "Blue team / Red team", "동시에 분석"
- Convergence: "결과를 합쳐서", "종합해서 결론", "비교 분석"
- Explicit: `/pipeline`, "파이프라인으로 해줘", "단계별로 자동으로"

**Managing a pipeline — user says:**
- "어디까지 됐어?", "pipeline status?", "/pipeline status"
- "파이프라인 멈춰", "중단", "/pipeline stop"
- "다시 시작", "/pipeline resume"
- "step 3 빼줘", "방향 수정"

**Do NOT use for:**
- Single-step tasks (just execute directly)
- Simple research (use `deep-research-loop` skill)
- Multiple independent tasks with no dependency (use `task-pipeline` skill)

---

## Starting a Pipeline

### Step 1: Parse & Plan

Parse the user's natural language request into a structured step plan. Infer:
- How many steps
- Which steps can run in parallel (same `parallelGroup`)
- Which steps depend on prior results (`dependsOn`)
- Which steps synthesize/converge results (`type: converge`)
- Whether any step needs quality-based iteration (`maxIterations > 1`)

### Step 2: Present Plan for Confirmation

Present the plan as a markdown table:

> Pipeline Plan:
>
> | # | Step | Type | Group | Depends | Iter |
> |---|------|------|-------|---------|------|
> | 1 | Blue team research | execute | A | — | 1 |
> | 2 | Red team research | execute | A | — | 1 |
> | 3 | Cross-review | execute | B | 1,2 | 1 |
> | 4 | Final synthesis | converge | — | 3 | 1 |
>
> 총 4 steps, 예상 소요 ~2-4시간. 시작할까요?

If user's tone is urgent ("바로 시작", "ㄱㄱ"), skip confirmation.

### Step 3: Create State

**Generate pipeline ID:** `pipeline-YYYYMMDD-HHmmss`

**Create directory and files:**

Create `plans/pipeline/{pipeline-id}/plan.md` with the confirmed plan table.

Create `plans/pipeline/{pipeline-id}/state.json`:

```json
{
  "id": "pipeline-20260403-153022",
  "status": "in_progress",
  "cronId": null,
  "createdAt": "2026-04-03T15:30:22+09:00",
  "query": "user's original request in full",
  "steps": [
    {
      "id": "step-1",
      "name": "Blue team research",
      "type": "execute",
      "parallelGroup": "A",
      "dependsOn": [],
      "maxIterations": 1,
      "passScore": null,
      "status": "pending",
      "iteration": 0,
      "score": null,
      "summary": null,
      "outputFile": "step-1-blue-team-research.md",
      "prompt": "Research arguments supporting the thesis. Provide evidence, data, and reasoning."
    },
    {
      "id": "step-2",
      "name": "Red team research",
      "type": "execute",
      "parallelGroup": "A",
      "dependsOn": [],
      "maxIterations": 1,
      "passScore": null,
      "status": "pending",
      "iteration": 0,
      "score": null,
      "summary": null,
      "outputFile": "step-2-red-team-research.md",
      "prompt": "Research arguments against the thesis. Find counterevidence, risks, and alternative explanations."
    }
  ],
  "currentGroup": "A",
  "completedGroups": [],
  "totalTimeout": 259200
}
```

Step type reference:
- `execute` — General execution (research, writing, analysis, rebuttal, etc.)
- `converge` — Synthesize results from preceding steps into a unified conclusion

### Step 4: Create Cron Job (MANDATORY — DO THIS BEFORE EXECUTING ANY STEP)

**You MUST create a cron BEFORE executing the first group.** The cron is what will pick up after the current session ends and progress to the next group. Without it, the pipeline stops forever.

Check existing crons first:
```
system.run ["openclaw", "cron", "list", "--json"]
```

Create the cron:
```
system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Pipeline: <query summary>", "--message", "[Pipeline <pipeline-id>] Read plans/pipeline/<pipeline-id>/state.json and execute the next pending step. Follow pipeline skill protocol.", "--session", "isolated", "--announce"]
```

**Delivery target — validate-delivery preflight (MANDATORY, 2026-04-18 postmortem 대응):**

`openclaw cron add` 호출 **전에** 반드시 `validate-delivery.sh`로 preflight 검증:

```
# 앱 채널 예상
validate-delivery.sh --mode announce --channel "daily-log"
# → {"valid":true, "resolved":{"type":"app_channel", ...}}  → cron 생성 진행

# Telegram 예상
validate-delivery.sh --mode announce --target "6629171909"
# → {"valid":true, "resolved":{"type":"telegram", ...}}  → cron 생성 진행

# 잘못된 설정
validate-delivery.sh --channel "test-2"
# → {"valid":false, "error":"channel ... not found", "recommendation":"..."}  → stop, 유저에 확인
```

Exit code 1이면 cron 생성 금지. 4회 연속 delivery 실패 재현 방지.

---

**Delivery target — decision tree:**

유저가 어디 있느냐에 따라 다르게 설정. 잘못 설정하면 크론 delivery가 매번 실패함:

1. **유저가 앱 채널 안**: 시스템 컨텍스트에 `[Channel: <name>]` 보임
   - `--channel <channel-name>` 사용 (내부적으로 앱 채널 backend로 라우팅)
   - 예: `--channel "daily-log"`

2. **유저가 Telegram**: `[Channel: ...]` 보이지 않고 Telegram 메시지로 대화 중
   - **`--target <chatId>` 사용** (Telegram은 channel name을 모름)
   - chatId 얻는 법: `system.run ["sh", "-c", "echo $TELEGRAM_CHAT_ID"]` 또는 `/workspace/IDENTITY.md`의 telegram.chat_id 확인
   - 예: `--target "6629171909"`

3. **둘 다 해당 모호할 때**: 유저에 확인 요청. 절대 기본값 추측 금지.

```
# 앱 채널
system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Pipeline: <query>", "--message", "[Pipeline <id>] ...", "--session", "isolated", "--announce", "--channel", "<channel-name>"]

# Telegram
system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Pipeline: <query>", "--message", "[Pipeline <id>] ...", "--session", "isolated", "--announce", "--target", "<chatId>"]
```

**Parse the returned cron ID** and update `state.json` cronId field. Verify the cron was created with `cron list --json`.

**크론 생성 직후 첫 tick 검증:** cron 생성 후 1분 내에 `openclaw cron runs <cronId> --last 1 --json`으로 첫 tick 결과 확인. `status=error` 이고 `error`에 "delivery" 포함되면 즉시 cron 삭제 + 유저에 설정 오류 보고. 재발 방지.

### Step 5: Execute First Group Immediately

Now that the cron exists, execute the first group in the current session:

- **If first group has 1 step:** Execute directly in current session (유저가 기다리고 있으므로 동기 실행 OK).
- **If first group has 2+ steps:** 각 step을 fire-and-forget 서브에이전트로 디스패치. **반드시 아래 `spawn-with-verify` 프로토콜을 따라라 (단순 nohup만으론 phantom 실행 발생).**

### spawn-with-verify — agent-spawn.sh 래퍼 사용 (MANDATORY, v1.1 갱신)

**직접 `nohup openclaw agent` 호출 금지.** 대신 플랫폼이 제공하는 `agent-spawn.sh` 래퍼를 호출하라. 래퍼가 spawn-handshake / retry-with-backoff / concurrency-limit / CP2 self-report를 한 번에 처리한다.

```bash
# 올바른 사용 (직접 nohup 금지)
system.run ["sh", "-c",
  "agent-spawn.sh \
    --session-id pipeline-{step-id} \
    --message '{prompt}' \
    --timeout 7200 \
    --log /workspace/plans/pipeline/{pipeline-id}/{step-id}-agent.log \
    --pipeline-id {pipeline-id}"]
```

출력(JSON) 예:
```
{"status":"verified","session_id":"pipeline-step-3a","pid":12345,"attempt":1,"log":"..."}
{"status":"rejected","reason":"concurrency_limit","limit":4,"attempt":1}
{"status":"failed","reason":"phantom","attempts":3,"session_id":"..."}
```

처리 지침:
- `"status":"verified"` → state.json에 `steps[n].status="in_progress"`, `spawned_at=<now>`, `spawn_attempt=<attempt>` 저장
- `"status":"rejected"` → 이번 tick에서 spawn하지 않음. `status="pending"` 유지. 다음 tick 때 재시도 (래퍼가 concurrency 초과를 자동 차단하므로 우리 쪽에서 추가 처리 불필요)
- `"status":"failed"` → `steps[n].status="failed"`, `error=<reason>`, 유저에 "⚠️ Step X spawn 실패" announce

**래퍼가 이미 하는 것 (우리가 다시 할 필요 없음):**
- 5초 spawn handshake (subagents list 검증)
- 1s/2s/4s exponential backoff 재시도 (max 3회)
- `AEF_SPAWN_CONCURRENT_LIMIT=4` 내부 체크
- `pipeline-report.sh` 이벤트 emit (step_spawned/step_phantom_detected/step_failed)

### batch 동시성 (래퍼가 강제 — 여기선 순차 dispatch만)

동일 tick에서 여러 step을 spawn할 때는 **그냥 순차적으로 `agent-spawn.sh` 호출**하면 된다. 래퍼 자체가 concurrency-limit(4)를 redis/subagents 기반으로 확인하고 rejected를 반환하므로, 우리가 앞단에서 자르지 않아도 된다.

```
for step in executable_steps:
    result = agent-spawn.sh --session-id ... --pipeline-id ...
    parse result.status → state.json 업데이트
```

13개 request 시도해도 5번째 이상은 `rejected`가 자동 반환되어 `pending`으로 유지됨.

---

**이상 agent-spawn.sh 래퍼 하나로 spawn-with-verify + retry-with-backoff + batch-limit 3종이 전부 처리된다. 절대 직접 nohup 호출로 회귀 금지.**

Set verified step `status: "in_progress"`. 크론이 다음 tick에서 완료 감지 OR stalled 감지.

### 모든 상태 전이는 pipeline-report.sh로 플랫폼에 자기보고 (MANDATORY)

플랫폼이 phantom/stalled를 실시간 관측 가능하도록 매 전이 보고:

```
pipeline-report.sh <pipeline_id> pipeline_started
pipeline-report.sh <pipeline_id> step_spawned step-3a "attempt=1"
pipeline-report.sh <pipeline_id> step_verified step-3a
pipeline-report.sh <pipeline_id> step_phantom_detected step-3a "subagent not found after 5s, attempt=1"
pipeline-report.sh <pipeline_id> step_failed step-3a "phantom x3 retries exhausted"
pipeline-report.sh <pipeline_id> step_completed step-3a
pipeline-report.sh <pipeline_id> pipeline_completed
pipeline-report.sh <pipeline_id> delivery_error "" "target/channel mismatch"
```

보고는 fire-and-forget (5s timeout). 실패해도 파이프라인은 계속 진행. 이 보고로 Open Magi가:
- `/pipeline status` 명령 시 실시간 응답
- 시간당 N회 이상 severe 이벤트 감지 시 운영자 알림
- 유저에게 "실패 패턴"을 요약 제공

For steps completed in this session:
1. Save output to `plans/pipeline/{pipeline-id}/{step.outputFile}`
2. Extract/generate `## Summary` (~500 chars) at end of output
3. Copy summary to `steps[n].summary` in state.json
4. Set `steps[n].status: "completed"`

Update state: move to next group, set `currentGroup` accordingly.

Confirm to user:
> "Pipeline 시작. Group A (2 steps) 실행 중. 5분 간격으로 자동 진행됩니다. '/pipeline status'로 확인 가능."

---

## Cron Iteration Protocol (Isolated Session)

**CRITICAL: 크론 세션은 30초 이내에 끝나야 한다.** OpenClaw announce delivery는 90초 gateway timeout이 있다. 크론이 직접 무거운 작업(서브에이전트 동기 대기, 긴 분석)을 하면 timeout → delivery 실패 → 유저에게 알림 안 감. 크론은 "상태 체크 + 보고 + fire-and-forget 디스패치"만 한다.

When you see `[Pipeline <pipeline-id>]` in your prompt:

### 1. Read State (즉시)

Read `plans/pipeline/{pipeline-id}/state.json`.

### 2. Check Termination (즉시)

**In this order:**

a) `status` is `completed` or `cancelled` → delete cron, respond NO_REPLY, STOP.

b) Pipeline total timeout exceeded (default 72h) → auto-complete:
   - Generate FINAL.md from all completed step summaries
   - Set `status: "completed"`
   - Delete cron
   - Announce: "Pipeline 완료 (시간 제한). 결과: plans/pipeline/{id}/FINAL.md"
   - STOP.

c) All steps completed → generate FINAL.md, set `status: "completed"`, delete cron, announce, STOP.

### 3. Check In-Progress Steps (즉시 — 파일 존재만 확인)

For each step with `status: "in_progress"`:
- Check if outputFile exists AND contains `## Summary`
- If completed: read summary, set `step.status: "completed"`, `step.summary: "..."`
- If still running: note it

### 4. Find & Dispatch Next Steps (fire-and-forget)

Determine which steps are ready to execute:
1. Find all steps with `status: "pending"`
2. Filter to those whose `dependsOn` steps are ALL `completed`

If no steps are executable:
- If in-progress steps exist: report "Step X still running" → STOP (wait for next tick)
- Otherwise: report "Waiting for dependencies" → STOP

**For each executable step, dispatch via spawn-with-verify (batch limit = 4):**

앞서 "spawn-with-verify + retry-with-backoff + batch-limit" 프로토콜을 그대로 적용한다 (이 섹션 앞부분 참조).

한 tick의 실행 순서:
1. `executable_steps` 중 앞 4개만 이번 tick에서 spawn
2. 각 step에 spawn-with-verify 수행
3. 실패 step은 retry-with-backoff (최대 3회), 그래도 실패 시 `status="failed"`
4. 성공 step만 `status="in_progress"`로 전환 + state.json 쓰기
5. 나머지 (5번째~)는 `status="pending"` 유지 → 다음 tick에 처리

**절대 순서가 바뀌면 안 된다:** verify 실패했는데 state.json에 먼저 "in_progress" 쓰면 phantom 재발생.

```
잘못된 순서 (이전 버그):
  state.json: step.status = "in_progress"  ← 먼저 씀
  nohup ... &                              ← 실패해도 몰라
  
올바른 순서:
  nohup ... &
  sleep 5
  verify via subagents list
  verified? → state.json: step.status = "in_progress"
  not verified? → retry OR status = "failed"
```

### Intervention Signals (MANDATORY, v1.1 갱신)

**매 cron tick 시작 시 반드시** 유저가 프론트엔드에서 보낸 intervention 신호를 먼저 읽는다. 이 순서로 처리:

```bash
# pipeline-interventions.sh 래퍼 사용 (curl/JSON 구성 금지)
INTERVENTIONS=$(system.run ["sh", "-c", "pipeline-interventions.sh {pipeline-id}"])
```

JSON response의 `interventions[]`에는 가장 최근 요청부터 쌓여 있다. **가장 최근 요청 1개만 처리**하고 (멱등성), 처리했으면 완료 이벤트 보고:

| action | 처리 |
|--------|------|
| `cancel` | state.json `status="cancelled"`, cron 삭제, FINAL.md 생성 (부분 결과), 유저 announce "파이프라인 중단됨" |
| `pause` | state.json `status="paused"`, cron 삭제 (재시작 시 cron 재생성), 유저 announce "파이프라인 일시정지" |
| `resume` | state.json `status="in_progress"`, cron 재생성, 다음 tick부터 진행 |
| `retry_step` | 해당 step의 `status="pending"`, `spawn_attempt=0`, outputFile 삭제. 다음 executable step 디스패치 단계에서 자동 pickup |

처리 후 반드시 `pipeline-report.sh` 로 상응하는 이벤트 보고:
- cancel → `pipeline_paused` ("intervention: cancel")
- pause → `pipeline_paused` ("intervention: pause")
- resume → `pipeline_started` ("intervention: resume") (새 run으로 보고)
- retry_step → `step_spawned` (다음 spawn 시 자동)

**중요:** 같은 intervention 신호를 두 번 실행하지 않도록 — intervention 처리 직후 `state.json`에 `last_intervention_ts` 기록하고, 다음 tick에선 `requested_at > last_intervention_ts`인 신호만 처리.

### Stalled Detection — heartbeat-check.sh 래퍼 사용 (MANDATORY, v1.1 갱신)

매 cron tick 첫 작업. 직접 `subagents list | grep` 하지 말고 `heartbeat-check.sh` 래퍼를 호출해라.

각 `in_progress` step에 대해:

```bash
system.run ["sh", "-c",
  "heartbeat-check.sh \
    --session-id pipeline-{step-id} \
    --log /workspace/plans/pipeline/{pipeline-id}/{step-id}-agent.log \
    --state /workspace/plans/pipeline/{pipeline-id}/state.json \
    --pipeline-id {pipeline-id}"]
```

출력 처리:

| status | 의미 | 행동 |
|--------|------|------|
| `alive` | 정상 진행 중 | pass — 다음 step 체크 |
| `stalled` | 15분 이상 무활동 (log/state mtime 기준) | spawn_attempt < 3 → retry-with-backoff (agent-spawn.sh 재호출), ≥3 → `status="failed"` + announce |
| `dead` | 30분 이상 무활동 + subagent 비활성 | 즉시 `status="failed"`, error="phantom detected", 유저 announce "⚠️ Step X phantom 감지" |

outputFile 완성 체크는 별도로:
- outputFile 존재 + `## Summary` 포함 → `step.status="completed"`, heartbeat-check 결과 무시

**래퍼가 이미 하는 것 (우리가 다시 할 필요 없음):**
- `openclaw subagents list` 호출 + sessionId 매칭
- log/state mtime 측정 + stalled/dead 임계값 비교
- `pipeline-report.sh step_stalled` 이벤트 emit

N 임계값은 환경변수로 조정 가능:
- `AEF_HEARTBEAT_STALLED=900` (15분, stalled)
- `AEF_HEARTBEAT_DEAD=1800` (30분, dead)

이 로직이 2026-04-18 postmortem의 26분 phantom 상태를 차단한다.

**Subagent prompt template:**
```
Goal: {step.name}

Context: {state.query — original user request, abbreviated}

Prior step results:
{For each step in dependsOn: "### {name}\n{summary}"}

Instructions: {step.prompt}

IMPORTANT:
- Save your complete output to plans/pipeline/{pipeline-id}/{step.outputFile}
- Include a ## Summary section (~500 chars) at the very end summarizing your key findings/conclusions
- If this step involves research, use the web-search skill
- Execute autonomously — do not ask for permission at each phase
```

**Converge steps:** Also fire-and-forget. The converge subagent reads prior step outputs and synthesizes.

```
Goal: {step.name}

You are synthesizing results from multiple prior steps.

Prior step results:
{For each step in dependsOn: "### {name}\n{summary}"}

Read full outputs from plans/pipeline/{pipeline-id}/ if needed.

Instructions: {step.prompt}

Synthesize a unified conclusion. Identify the strongest, most evidence-backed position.

Save to plans/pipeline/{pipeline-id}/{step.outputFile}.
Include ## Summary (~500 chars) at the end.
```

**Iterating steps (maxIterations > 1):** First iteration is fire-and-forget. On next cron tick, if output exists but score < passScore, dispatch retry as fire-and-forget.

### 5. Announce Progress (크론 응답 = 유저에게 보이는 메시지)

크론의 텍스트 응답이 곧 유저에게 announce되는 메시지다. 간결하게:

- Nothing changed: respond `NO_REPLY` (suppress delivery)
- Step completed: "✅ Step 3 'Cross-review' 완료. (3/4 steps done)"
- Step dispatched: "🚀 Step 4 'Solve T1' 시작. 다음 체크: ~5분 후"
- Group complete: "📦 Group B 완료. Group C 시작."
- Pipeline complete: executive summary + FINAL.md path + duration
- Error: immediate announce + pipeline paused
- Still waiting: respond `NO_REPLY` (don't spam "아직 진행 중")

**전체 크론 실행 시간 목표: 10-20초. 절대 60초를 넘기지 마라.**

---

## FINAL.md Generation

When all steps are completed (or on timeout/cancellation with partial results):

```markdown
# Pipeline: {query}

## Executive Summary
{Overall conclusion — strongest findings, most plausible perspective, key takeaways}

## Step Results

### Step 1: {name}
{summary}

### Step 2: {name}
{summary}

...

## Process Log
- Started: {createdAt}
- Completed: {now}
- Steps: {completed}/{total} completed
- Duration: {elapsed}
```

For converge steps, the executive summary should primarily draw from the converge step's output rather than being a generic aggregation.

---

## Error Handling

| Situation | Action |
|-----------|--------|
| Subagent timeout (7200s default) | `step.status → "failed"`, announce, pipeline paused |
| No `## Summary` in output | Orchestrator reads output, auto-generates summary, continue |
| 3 consecutive failures same step | `status → "paused"`, announce "3회 실패, 확인 필요" |
| `state.json` missing on cron tick | Delete orphaned cron, STOP |
| Subagent returns empty/error | Retry once, then mark failed |
| **Spawn verify failed (phantom)** | retry-with-backoff max 3회, 실패 시 `status="failed"`, announce "phantom 감지" |
| **Phantom timeout (spawned but no activity N min)** | `status="failed"`, announce, 유저 개입 요청 |
| **Gateway timeout (10s on sessions_spawn)** | batch-limit(4) 이미 적용. 초과 시 1-2-4s exponential backoff |
| **Cron delivery error (wrong target/channel)** | 즉시 cron 삭제 + 유저에 "delivery 설정 오류 — telegram은 target, 앱 채널은 channel. 확인 요청." |
| **Consecutive cron errors (>= 3)** | cron 자동 삭제, pipeline `status="paused"`, 유저에 정지 사유 보고 |

**Timeout defaults:**

| Step type | Timeout |
|-----------|---------|
| Simple execute | 7200s (2h) |
| `maxIterations > 1` loop | 14400s (4h) |
| Converge | 3600s (1h) |

Pipeline total timeout: 72 hours (259200s).

---

## Managing Pipelines (User Commands)

### Status Check

User: "어디까지 됐어?", "/pipeline status"

1. Find active pipeline: scan `plans/pipeline/*/state.json` for `status: "in_progress"` or `"paused"`
2. Present:
   > Pipeline 진행 중: '{query}'
   >
   > | # | Step | Status |
   > |---|------|--------|
   > | 1 | Blue team research | ✅ |
   > | 2 | Red team research | ✅ |
   > | 3 | Cross-review | 🔄 진행 중 |
   > | 4 | Final synthesis | ⬜ 대기 |
   >
   > 경과: 45분, 2/4 steps 완료

### Stop

User: "파이프라인 멈춰", "/pipeline stop", "중단"

1. Set `status: "cancelled"` in state.json
2. Delete cron: `system.run ["openclaw", "cron", "rm", "<cronId>"]`
3. Generate FINAL.md with partial results (completed steps only)
4. Confirm: "Pipeline 중단. 현재까지 결과: plans/pipeline/{id}/FINAL.md"

### Resume

User: "다시 시작", "/pipeline resume"

1. Set `status: "in_progress"` in state.json
2. Recreate cron
3. Confirm: "Pipeline 재개. 다음 step부터 진행합니다."

### Modify

User: "step 5 빼줘", "Red team 논거에 ~도 추가해"

1. If step is pending: modify `step.prompt` or set `step.status: "skipped"`
2. If step is completed: cannot modify (warn user)
3. If step is in-progress: pause → modify → resume
4. Update state.json

### List

User: "/pipeline list"

Scan `plans/pipeline/*/state.json`, show recent 10:
> | Pipeline | Status | Steps | Duration |
> |----------|--------|-------|----------|
> | Blue/Red debate on X | ✅ completed | 7/7 | 3h 15m |
> | Market analysis Y | 🔄 in_progress | 3/5 | 1h 20m |

---

## File Structure

```
plans/pipeline/
  {pipeline-id}/
    state.json              # orchestration state
    plan.md                 # confirmed step plan
    step-1-{slug}.md        # step 1 output
    step-2-{slug}.md        # step 2 output
    ...
    FINAL.md                # final synthesized result
```

---

## Cron Lifecycle Safety

1. **Creation:** only when pipeline has 2+ groups (single-group pipelines execute synchronously)
2. **Auto-delete on completion:** all steps done, timeout, or cancellation
3. **Orphan protection:** if cron fires and status is completed/cancelled, cron deletes itself
4. **Duplicate prevention:** always `cron list --json` before creating
5. **One active pipeline:** if a pipeline is already in_progress, warn user before starting another
