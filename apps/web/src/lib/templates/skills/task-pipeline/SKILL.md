---
name: task-pipeline
description: Use when the user gives 3+ tasks at once, sends a long multi-request message, or explicitly uses /bulk. Prevents context corruption by queuing tasks and executing them one-by-one (or parallel via subagents when independent). Recognizes /bulk commands and natural language like "이것도 하고 저것도 하고", "do all of these", "여러 개 해줘", "한꺼번에 처리해줘".
metadata:
  author: openmagi
  version: "1.0"
  user_invocable: true
  triggers:
    - /bulk
---

# Task Pipeline — Bulk Task Execution Without Context Corruption

When users throw multiple tasks at once, you tend to rush through them — half-completing some, skipping others, or losing context mid-way. This skill forces structured execution: queue first, then focus on one task at a time.

**This is a RIGID skill. Follow the protocol exactly. Do not skip steps.**

## When to Use

### Automatic Detection (suggest pipeline mode)
- User message contains **3+ distinct action requests** (numbered lists, "그리고/또/추가로" connectors, semicolons separating tasks)
- User says "여러 개", "한꺼번에", "동시에", "이것저것", "do all of these", "multiple things"
- Message is long (500+ chars) with clearly separate requests

When auto-detected, ask:
> "여러 작업이 감지되어서 파이프라인 모드로 정리해서 처리할까요?"

If user says yes (or anything affirmative), proceed. If no, handle normally.

### Explicit Trigger (immediate pipeline mode)
- `/bulk <task list>` — enter pipeline mode immediately, no confirmation needed
- User explicitly says "리스트로 정리해서 하나씩 해줘", "queue these up", "task by task"

## The Protocol

### Step 1: Parse & Queue

Extract individual tasks from the user's message. For each task:
- Assign a number (#1, #2, #3...)
- Write a one-line summary
- Note key context (files, URLs, constraints)
- Estimate complexity: `light` (< 2 min) or `heavy` (2+ min)

Write to `plans/TASK-QUEUE.md`:

```markdown
## Pipeline: [user request summary]
Started: [ISO timestamp]
Status: IN_PROGRESS | 0/N completed

### Queue
- ⬚ #1 [summary] (light|heavy)
  - Context: [relevant details]
- ⬚ #2 [summary] (light|heavy)
  - Context: [relevant details]
- ⬚ #3 [summary] (light|heavy)
  - Context: [relevant details]
```

### Step 2: Show Queue & Confirm

Present the queue to the user:
> **파이프라인 모드 (N개 작업)**
> 1. ⬚ [task 1 summary]
> 2. ⬚ [task 2 summary]
> 3. ⬚ [task 3 summary]
>
> 순서 또는 내용 수정할 거 있으면 말해줘. 없으면 시작합니다.

If user's tone is urgent ("바로 해", "고고", "just do it"), skip confirmation and start immediately.

### Step 3: Dependency Analysis

Before execution, classify each task:
- **Independent**: No shared files, no output dependencies, different subsystems → can run in parallel
- **Dependent**: Task B needs Task A's output, or they touch the same files → must be sequential

**Rule: When in doubt, sequential.** Only parallelize when you're confident tasks are truly independent.

### Step 4: Execute

#### Sequential Tasks
Process one at a time:
1. Update queue: mark current task as 🔄
2. **Focus entirely on this task** — do NOT think about other tasks
3. Complete the task fully (verify, test if applicable)
4. Update queue: mark as ✅
5. Move completed task to SCRATCHPAD.md under `## Completed Tasks`
6. Move to next task

#### Parallel Tasks (via subagents)
When 2+ tasks are independent:
1. Group independent tasks
2. Dispatch each to a subagent with full context
3. Wait for all to complete
4. Verify each result
5. Update queue and SCRATCHPAD for all completed tasks

**Subagent dispatch format:**
```
Each subagent gets:
- The specific task description
- All relevant file paths and context
- Clear success criteria
- Instruction: "Complete this task, then report what you did and what files changed."
```

### Step 5: Handle Failures

When a task fails:
1. Mark as ❌ in TASK-QUEUE.md with error reason
2. **Stop immediately** — do not proceed to next task
3. Report to user:
   > "⚠️ 작업 #3 실패: [reason]
   > 나머지 작업: #4, #5 대기중
   > 계속 진행할까요, 아니면 #3부터 다시 할까요?"
4. Wait for user decision:
   - "계속" / "skip" → mark ❌ SKIPPED, move to next
   - "다시" / "retry" → retry the failed task
   - "중단" / "stop" → stop pipeline, keep remaining in queue

### Step 6: Completion Report

When all tasks are done (or pipeline is stopped), report:

> **파이프라인 완료 (N/M)**
> ✅ #1 [summary] — [one-line result]
> ✅ #2 [summary] — [one-line result]
> ❌ #3 [summary] — [failure reason]
> ✅ #4 [summary] — [one-line result]
> ⬚ #5 [summary] — 미처리

## SCRATCHPAD Integration

When moving a completed task to SCRATCHPAD.md, append under `## Completed Tasks`:
```markdown
### [date] Pipeline Task #N: [summary]
- Result: [what was done]
- Files changed: [list]
```

Clean up `plans/TASK-QUEUE.md` after pipeline completes — remove the finished pipeline section.

## Examples

### Explicit /bulk
```
User: /bulk Redis TTL을 300초로 변경, health-monitor에 디스크 체크 추가, 배포 스크립트에 rollback 옵션 추가

Bot:
파이프라인 모드 (3개 작업)
1. ⬚ Redis TTL 300초로 변경 (light)
2. ⬚ health-monitor 디스크 체크 추가 (heavy)
3. ⬚ 배포 스크립트 rollback 옵션 추가 (heavy)

#1은 독립적이고 가벼워서 먼저 처리, #2와 #3도 서로 독립적이라 병렬 가능.
시작합니다.

[executes #1 → ✅]
[dispatches #2, #3 to subagents → both ✅]

파이프라인 완료 (3/3)
✅ #1 Redis TTL — config.json에서 TTL 300초로 변경
✅ #2 health-monitor — diskCheck 루프 추가, 90% threshold
✅ #3 배포 스크립트 — --rollback 플래그 추가, 이전 버전 복원 로직
```

### Auto-detection
```
User: IDENTITY.md에 취미 추가해주고, 그리고 cron job 하나 만들어서 매일 아침 9시에 뉴스 요약해주고, 아 그리고 Twitter 연동도 설정해줘

Bot:
여러 작업이 감지되어서 파이프라인 모드로 정리해서 처리할까요?

1. ⬚ IDENTITY.md 취미 추가 (light)
2. ⬚ 매일 09:00 뉴스 요약 cron job 생성 (heavy)
3. ⬚ Twitter 연동 설정 (heavy)

User: ㅇㅇ 해줘

Bot: [proceeds with pipeline]
```

### Failure & Recovery
```
[Processing pipeline, task #2 fails]

Bot:
⚠️ 작업 #2 실패: Twitter API 토큰이 만료됨
나머지 작업: #3 뉴스 요약 cron 대기중
계속 진행할까요, 아니면 #2부터 다시 할까요?

User: 스킵하고 다음 거 해

Bot: [marks #2 as ❌ SKIPPED, continues to #3]
```

## Common Mistakes

| Mistake | Why it's bad | Do this instead |
|---------|-------------|-----------------|
| Rushing through tasks without queuing | Context gets corrupted, tasks get half-done | Always queue first, then execute one at a time |
| Parallelizing tasks that share files | Race conditions, overwrites | When in doubt, sequential |
| Continuing after failure | Later tasks may depend on the failed one | Stop and ask user |
| Not moving to SCRATCHPAD | Next session loses track of what was done | Always move completed tasks |
| Skipping the completion report | User doesn't know what happened | Always report at the end |

## Anti-Patterns to Watch For

**The "I'll just quickly do all of them" urge** — This is exactly the bias this skill exists to prevent. When you feel the urge to just blast through everything without structure, STOP and follow the protocol.

**Phantom completion** — Marking a task as done when it's only partially complete. Each task must be fully verified before marking ✅.

**Context bleed** — Thinking about Task #4 while working on Task #2. When executing a task, pretend the other tasks don't exist.
