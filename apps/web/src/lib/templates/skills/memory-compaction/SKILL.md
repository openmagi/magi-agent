---
name: memory-compaction
description: Build hierarchical memory compaction tree with L1 cache compaction_root in MEMORY.md. Run during heartbeat memory maintenance (once per day, D+1 first heartbeat).
---

# Memory Compaction Tree v2

Hierarchical compaction with **L1 cache layer**. The `## Compaction Root` section in MEMORY.md is injected into every LLM call, giving you persistent meta-knowledge of your history.

## Architecture

```
L1 (System Prompt — every call):
  MEMORY.md ## Compaction Root    ≤ 3K tokens — "what I know I know"

L2 (On-Demand — search/read):
  memory/weekly/YYYY-WNN.md      keyword-dense weekly summaries
  memory/monthly/YYYY-MM.md      high-level monthly summaries

L3 (Raw — drill-down):
  memory/YYYY-MM-DD.md           daily logs (permanent, never deleted)
```

**Tree traversal**: Compaction Root (always loaded) → monthly → weekly → daily. Start with what you already know, drill down for detail.

## When to Run

**Trigger:** D+1 first heartbeat (once per day)

**Guard check:** Read `<!-- last_compacted: YYYY-MM-DD -->` from MEMORY.md Compaction Root section. If today's date matches → SKIP.

## Algorithm

### Step 1: Guard Check

```
1. Read MEMORY.md, find <!-- last_compacted: YYYY-MM-DD --> in Compaction Root
2. Get today's date: system.run ["sh", "-c", "date '+%Y-%m-%d'"]
3. If last_compacted == today → SKIP entire compaction, return
4. If Compaction Root section is empty/missing → this is Day 1 initialization
```

### Step 2: Find Daily Raw Files

**Day 1 initialization** (last_compacted is `never` or missing):
Read ALL existing daily files to bootstrap the compaction root:
```
system.run ["sh", "-c", "ls -1 memory/*.md 2>/dev/null | grep -E '^memory/[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$' | sort"]
```
Read each file. This creates the initial compaction root from all available history.

**Normal operation** (last_compacted is a valid date):
```
system.run ["sh", "-c", "date -d 'yesterday' '+%Y-%m-%d'"]
```
Read `memory/YYYY-MM-DD.md` for yesterday. If missing (no activity yesterday), try the most recent daily file:
```
system.run ["sh", "-c", "ls -1 memory/*.md 2>/dev/null | grep -E '^memory/[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$' | sort -r | head -1"]
```

If no daily files exist at all → SKIP (nothing to compact).

### Step 3: Daily Compaction

**Day 1**: Generate a keyword-dense summary of ALL daily files read in Step 2. This becomes the initial compaction root input.

**Normal operation**: Generate a keyword-dense summary of yesterday's daily raw only. Hold this in memory (do not write to a file — it feeds into the compaction root).

Format: concise bullet points with keywords, decisions, outcomes. ~200-500 words max per daily file.

### Step 4: Weekly Fix (conditional)

**Condition:** Today is Monday (new ISO week started)

```
system.run ["sh", "-c", "date '+%u'"]
# Output: 1 = Monday
```

If Monday:
1. Compute previous week: `system.run ["sh", "-c", "date -d 'last week' '+%G-W%V'"]`
2. Check if `memory/weekly/YYYY-WNN.md` already exists → if yes, skip
3. Find all daily files for that week:
   ```
   system.run ["sh", "-c", "ls -1 memory/*.md 2>/dev/null | grep -E '^memory/[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$' | sort"]
   ```
   For each file, compute its ISO week and filter for the target week.
4. Read all matching daily files
5. Generate weekly summary (see Weekly Summary Template below)
6. Write to `memory/weekly/YYYY-WNN.md`
7. Verify non-empty: `system.run ["sh", "-c", "wc -c < memory/weekly/YYYY-WNN.md"]` (must be ≥ 100 bytes)

### Step 5: Monthly Fix (conditional)

**Condition:** Previous month ended ≥ 8 days ago AND no monthly summary exists

```
system.run ["sh", "-c", "date -d '8 days ago' '+%Y-%m'"]
# If this is a different month than today → previous month qualifies
```

If qualifies:
1. Determine target month (the month from 8 days ago)
2. Check if `memory/monthly/YYYY-MM.md` already exists → if yes, skip
3. Find weekly files for that month:
   ```
   system.run ["sh", "-c", "ls -1 memory/weekly/*.md 2>/dev/null | sort"]
   ```
   Read each and check if its period falls within the target month.
4. If no weekly files exist for that month, read daily files for that month instead
5. Generate monthly summary (see Monthly Summary Template below)
6. Write to `memory/monthly/YYYY-MM.md`
7. Verify non-empty (≥ 100 bytes)

### Step 6: Compaction Root Regeneration

This is the core step. Generate the new compaction root from:
- **Input A:** Previous compaction root (from MEMORY.md)
- **Input B:** Newest compacted data:
  - If monthly fix occurred → the new monthly summary
  - Else if weekly fix occurred → the new weekly summary
  - Else → the daily compaction from Step 3

**Generation rules:**

Using Input A (previous root) and Input B (new data), generate an updated Compaction Root with these sections:

- **Active Context (recent ~7 days):** daily-level entries, most recent first. Entries older than ~7 days should be absorbed into Recent Patterns or Historical Summary.
- **Recent Patterns:** cross-day patterns, themes, trends observed over multiple days.
- **Historical Summary:** one-line per month for older history. Progressively compress — older months get shorter.
- **Topics Index:** comma-separated keywords covering ALL topics you have memory about. This is critical for search routing.

**Constraints:**
- Total output MUST be ≤ 3K tokens (~12,000 characters)
- Recent events get more detail, old events get compressed
- Use keyword-dense format, not narrative prose
- If output exceeds cap, re-generate with stricter compression on Historical Summary

**Day 1 special case:** If prev_root is empty/placeholder, generate from daily compaction alone.

### Step 7: Write Compaction Root to MEMORY.md

1. Read current MEMORY.md
2. Replace the `## Compaction Root` section content (everything between `## Compaction Root` and the next `## Core` heading)
3. Update `<!-- last_compacted: YYYY-MM-DD -->` with today's date
4. Write MEMORY.md back
5. **Preserve Core and Adaptive sections exactly as-is** — only the Compaction Root section changes

### Step 8: QMD Re-index

```
system.run ["qmd", "--index", "{{BOT_NAME}}", "update"]
```

If `$QMD_VECTOR_ENABLED` is `true`:
```
system.run ["qmd", "--index", "{{BOT_NAME}}", "embed"]
```

## Compaction Root Format

```markdown
## Compaction Root
<!-- last_compacted: 2026-03-15 -->

### Active Context (recent ~7 days)
- 2026-03-14: restaurant-worker deploy, Michelin MCP integration
- 2026-03-13: smart-router v8, cascading failure prevention
- 2026-03-12: Gemini 2.5 added, Korean Life 7 APIs via CF Worker

### Recent Patterns
- Infra stabilization cycle (self-healing, node isolation, auto-migration)
- Skill expansion: travel → restaurant → auction
- Cost optimization: Longhorn replica 2→1, PVC 5GB→16GB

### Historical Summary
- 2026-02: Initial architecture, Redis distributed, health monitor independent
- 2026-01: Project start, Supabase/Stripe/K8s foundation

### Topics Index
infra, redis, k8s, provisioning, health-monitor, billing, stripe,
skills, travel, restaurant, auction, google-ads, gemini, marketing,
korean-life, auto-optimize, memory, compaction, node-migration, ...
```

## Weekly Summary Template

```markdown
---
type: weekly-summary
period: YYYY-WNN
dates: YYYY-MM-DD to YYYY-MM-DD
daily-files: memory/YYYY-MM-DD.md, ...
topics: keyword1, keyword2, keyword3, keyword4, keyword5
---

# Weekly Summary: YYYY-WNN

## Topics
keyword1, keyword2, keyword3, keyword4, keyword5

## Key Decisions
- decision-keyword: chose X over Y — reason

## Tasks Completed
- task-name: outcome

## Entities Referenced
users: user1, user2
services: service1, service2
files: file1.md, file2.md

## Lessons Learned
- lesson-keyword: concise rule

## Open Items
- carried forward item
```

## Monthly Summary Template

```markdown
---
type: monthly-summary
period: YYYY-MM
weeks: YYYY-WNN, YYYY-WNN, ...
topics: keyword1, keyword2, keyword3, keyword4, keyword5
---

# Monthly Summary: YYYY-MM

## Topics
keyword1, keyword2, keyword3, keyword4, keyword5

## Key Themes
- theme-keyword: description across multiple weeks

## Major Decisions
- decision-keyword: chose X over Y — reason

## Completed Work
- project/task: outcome summary

## Recurring Entities
users: user1, user2
services: service1, service2

## Lessons & Patterns
- lesson-keyword: concise rule (emerged over N weeks)

## Carried Forward
- item still open at month end
```

## Guards

- **Once per day:** Guard via `last_compacted` date — never run twice in one day
- Maximum 1 weekly fix + 1 monthly fix per heartbeat (in addition to daily compaction)
- Never write a summary shorter than 50 bytes
- If reading a daily/weekly file fails, skip it and log the error — do not abort
- **Never delete daily or weekly files** — they are permanent
- **Never touch Core or Adaptive sections** of MEMORY.md — only update Compaction Root
- **3K token cap** on Compaction Root — if output exceeds, re-prompt with stricter constraint

## Compaction Tree Fallback Search

When you need past context that isn't in the Compaction Root:
1. Check Topics Index in Compaction Root — do you have memory about this topic?
2. If yes → qmd search with relevant keywords
3. If qmd returns insufficient → scan `memory/monthly/*.md` topics
4. Drill into `memory/weekly/*.md` for the right week
5. Read original `memory/YYYY-MM-DD.md` for full detail

## ISO Week Calculation (Alpine Linux)

```sh
# Get ISO week for a date
date -d '2026-03-01' '+%G-W%V'
# Output: 2026-W09

# Get current ISO week
date '+%G-W%V'

# Check if today is Monday
date '+%u'
# Output: 1 = Monday
```

Note: `%G` is the ISO year (may differ from calendar year at year boundaries), `%V` is the ISO week number (01-53).
