---
name: deep-research-loop
description: "Orchestrator for multi-iteration deep research with review loops. Creates cron jobs, manages state, dispatches deep-research and deep-research-review skills. Triggers: '딥리서치', 'deep research', '깊게 조사', '철저히 알아봐', '종합 분석', 'comprehensive analysis'. Also handles: '리서치 어디까지', '리서치 중간 결과', '리서치 그만', '리서치 중단', 'research status', 'stop research'."
user_invocable: true
metadata:
  author: openmagi
  version: "3.0"
---

# Deep Research Loop — Cron-Based Research Orchestrator

Manages long-running, multi-iteration research with quality review loops. Dispatches `deep-research` for execution and `deep-research-review` for active verification.

## When to Use

**Starting research — user says:**
- "딥리서치해줘: X", "deep research: X"
- "깊게 조사해줘", "철저히 알아봐", "종합 분석해줘"
- "완벽하게 조사해", "하루 종일 걸려도 돼"

**Managing research — user says:**
- "리서치 어디까지 됐어?", "research status?"
- "중간 결과 보여줘", "show interim results"
- "리서치 그만해", "중단", "stop research", "이제 됐어"
- "더 깊게 파줘", "go deeper"

**Do NOT use for:**
- Simple factual lookups (use `web-search` skill directly)
- Questions answerable in 1-2 searches
- Quick mode research (handled directly by `deep-research` skill, no cron needed)

## Modes

| Mode | Duration | Max Iter | Pass Score | Stall Limit | Cron |
|------|----------|----------|------------|-------------|------|
| Quick | 5-10 min | 1 | - | - | No |
| Standard | 15-30 min | 3 | 7+ | 3 | Yes (5 min) |
| Deep | 30-60 min | 5 | 8+ | 3 | Yes (5 min) |
| xDeep | 60-120 min | 8 | 8+ | 3 | Yes (5 min) |
| xxDeep | max 24h | Unlimited | 9+ | 5 | Yes (5 min) |

**Mode signals:**
- Quick: "간단히", "빠르게", "overview", "quick"
- Standard: no qualifier, or "조사해줘", "알아봐줘"
- Deep: "깊게", "철저히", "deep", "comprehensive"
- xDeep: "완벽하게", "모든 걸 알아봐", "exhaustive", "xdeep"
- xxDeep: "하루 종일 걸려도 돼", "최대한 깊게", "xxdeep", "시간 상관없어"

## Starting Research

### Step 1: Determine Mode

Infer from user language. Default to Standard if ambiguous. Confirm mode and estimated time before starting:

> "Deep 모드로 리서치 시작합니다. 약 30-60분 소요, 최대 5회 반복. '리서치 그만해'로 중단 가능."

If user's tone is urgent ("바로 시작해", "지금 돌려"), skip confirmation.

### Step 2: Quick Mode — No Cron

For Quick mode, skip all cron/state machinery. Just run `deep-research` skill directly in the current session and deliver results. Done.

### Step 3: Standard+ Mode — Create State & Cron

**Generate research ID:** `research-YYYYMMDD-HHmmss`

**Create directory and state file:**

Create `plans/research/{research-id}/state.json`:

```json
{
  "id": "research-20260329-143022",
  "query": "user's original question in full",
  "mode": "deep",
  "phase": "SCOPE",
  "iteration": 0,
  "scores": [],
  "bestIteration": 0,
  "bestScore": 0,
  "startedAt": "2026-03-29T14:30:22Z",
  "lastUpdatedAt": "2026-03-29T14:30:22Z",
  "maxMinutes": 60,
  "maxIterations": 5,
  "passScore": 8,
  "stallCount": 0,
  "stallLimit": 3,
  "status": "in_progress",
  "cronId": null
}
```

Mode-to-config mapping:

| Field | Standard | Deep | xDeep | xxDeep |
|-------|----------|------|-------|--------|
| maxMinutes | 30 | 60 | 120 | 1440 |
| maxIterations | 3 | 5 | 8 | 9999 |
| passScore | 7 | 8 | 8 | 9 |
| stallLimit | 3 | 3 | 3 | 5 |

### Step 4: Create Cron Job

Check existing crons first:
```
system.run ["openclaw", "cron", "list", "--json"]
```

Create the cron:
```
system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Deep Research: <query summary>", "--message", "[DeepResearch <research-id>] Read plans/research/<research-id>/state.json and execute the next phase. Follow deep-research-loop skill for orchestration, deep-research skill for execution, deep-research-review skill for review. All searches must use the web-search skill.", "--session", "isolated", "--announce", "--model", "sonnet"]
```

**Channel delivery:** If the user is in an app channel (you see `[Channel: <name>]` in system context), add `--channel <name>` so final results are posted to that channel:

```
system.run ["openclaw", "cron", "add", "--every", "5", "--name", "Deep Research: <query summary>", "--message", "[DeepResearch <research-id>] ...", "--session", "isolated", "--announce", "--channel", "<channel-name>", "--model", "sonnet"]
```

**Parse the returned cron ID** and update `state.json` cronId field.

### Step 5: Run First Phase Immediately

Don't wait for cron — execute the SCOPE phase right now in this session:
1. Run `deep-research` skill with phase=SCOPE
2. Save output to `plans/research/{research-id}/scope.md`
3. Update state: `phase: "RESEARCH"`, `lastUpdatedAt: now`

Confirm to user:
> "리서치 범위 설정 완료. 5분 간격으로 자동 실행됩니다. '리서치 어디까지?'로 상태 확인 가능."

---

## Cron Iteration Protocol (Isolated Session)

When you see `[DeepResearch <research-id>]` in your prompt:

### 1. Read State

Read `plans/research/{research-id}/state.json`.

### 2. Check Termination

**In this order:**

a) `status` is `completed` or `cancelled` → delete cron, STOP.

b) Time elapsed > `maxMinutes` → take best draft as FINAL:
   - Copy `draft-{bestIteration}.md` → `FINAL.md`
   - Set `status: "completed"`, `phase: "DELIVER"`
   - Delete cron: `system.run ["openclaw", "cron", "rm", "<cronId>"]`
   - Announce: "리서치 완료 (시간 제한). 최종 리포트는 plans/research/{id}/FINAL.md"
   - STOP.

c) `iteration` >= `maxIterations` → same as (b), announce with "최대 반복 횟수 도달".

d) `stallCount` >= `stallLimit` → same as (b), announce with "추가 개선 없음, 최선 결과 전달".

### 3. Execute Phase

#### phase: SCOPE
- Run `deep-research` skill instructions for SCOPE phase
- Read `scope.md` for the query context, decompose into sub-questions
- Save to `plans/research/{id}/scope.md`
- Update state: `phase: "RESEARCH"`

#### phase: RESEARCH
- Read `scope.md` for sub-questions
- If `iteration > 0`, read latest `review-{n}.md` for feedback — focus on action items
- Run `deep-research` skill instructions for SEARCH → COLLECT → SYNTHESIZE → DELIVER
- All searches via `web-search` skill (web-search.sh + firecrawl.sh)
- Save to `plans/research/{id}/draft-{iteration+1}.md`
- Update `sources.json` with new sources
- Update state: `iteration++`, `phase: "REVIEW"`

#### phase: REVIEW
- Read latest `draft-{iteration}.md`
- Run `deep-research-review` skill instructions
- All verification searches via `web-search` skill
- Save to `plans/research/{id}/review-{iteration}.md`
- Extract score from review

**Score evaluation:**
- Score >= `passScore` → copy draft to `FINAL.md`, `status: "completed"`, delete cron, announce success
- Score < passScore AND score <= bestScore → `stallCount++`
- Score < passScore AND score > bestScore → `stallCount = 0`, update `bestScore`, `bestIteration`
- Set `phase: "RESEARCH"` for next iteration

#### phase: DELIVER
- Announce FINAL.md to user
- Delete cron
- STOP

### 4. Update State

Always write updated `state.json` before finishing.

### 5. xxDeep Interim Reports

For xxDeep mode: if 6+ hours have passed since last announcement AND status is still `in_progress`, announce interim status:
> "리서치 진행 중: {iteration}회 반복, 현재 최고 점수 {bestScore}/10. 계속 진행합니다."

---

## Managing Research (User Commands)

### Status Check

User: "리서치 어디까지?", "research status?"

1. Find active research: scan `plans/research/*/state.json` for `status: "in_progress"`
2. Present:
   > "Deep Research 진행 중: 'X에 대한 분석'
   > - 모드: Deep (30-60분)
   > - 진행: 3/5 iteration, 현재 점수 7/10
   > - 경과: 25분
   > - 상태: 리뷰 피드백 반영 중"

### Interim Results

User: "중간 결과 보여줘", "show what you have so far"

Read and present the latest `draft-{n}.md`.

### Cancel

User: "리서치 그만해", "중단", "stop", "이제 됐어", "더 안 해도 돼"

1. Set `status: "cancelled"` in state.json
2. Delete cron: `system.run ["openclaw", "cron", "rm", "<cronId>"]`
3. If any draft exists, present the best one
4. Confirm: "리서치 중단. 현재까지 최선 결과를 전달합니다."

### Upgrade Mode

User: "더 깊게 파줘", "go deeper", "시간 더 줘"

1. Upgrade mode one level (e.g., standard → deep)
2. Update `maxMinutes`, `maxIterations`, `passScore`, `stallLimit` in state.json
3. Reset `stallCount` to 0
4. Confirm: "Deep 모드로 업그레이드. 최대 60분, 통과 점수 8/10으로 계속합니다."

---

## File Structure

```
plans/research/
  {research-id}/
    state.json          # orchestration state
    scope.md            # question decomposition
    draft-1.md          # iteration 1 report
    review-1.md         # iteration 1 review (score + feedback)
    draft-2.md          # iteration 2 report (incorporates review-1 feedback)
    review-2.md         # iteration 2 review
    ...
    sources.json        # all collected sources with credibility
    FINAL.md            # final report after passing review
```

## Cron Lifecycle Safety

1. **Creation:** only when starting Standard+ research
2. **Auto-delete on completion:** score passes, time cap, stall limit, iteration cap
3. **Auto-delete on cancel:** user requests stop
4. **Orphan protection:** if cron fires and state is completed/cancelled, cron deletes itself
5. **No cron for Quick:** runs synchronously in single session
