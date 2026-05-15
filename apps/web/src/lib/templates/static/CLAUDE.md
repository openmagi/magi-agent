# CLAUDE.md — Operational Guidelines

## Memory Architecture

```
Layer 1 (System Prompt — every API call):
  MEMORY.md        Compaction Root (≤3K tok, L1 cache) + Core (frozen) + Adaptive (~50 lines)
  SCRATCHPAD.md    ~150 lines  active working state
  WORKING.md       ~100 lines  current tasks

Layer 2 (On-Demand — read when needed):
  memory/YYYY-MM-DD.md         daily logs (permanent raw records)
  knowledge/*.md               detailed knowledge (RAG searchable)
  plans/*.md                   task plans

Layer 3 (Search):
  qmd BM25 search              keyword search across all .md files
  Compaction Tree fallback      monthly → weekly → daily drill-down
```

### Compaction Tree (v2 — L1 Cache)
`## Compaction Root` in MEMORY.md is your **L1 cache** — always loaded, ≤3K tokens. It contains Active Context, Recent Patterns, Historical Summary, and Topics Index.

`memory/weekly/` and `memory/monthly/` are **L2 search index nodes** for drill-down. When the Compaction Root Topics Index suggests you have memory about a topic:
1. qmd search with relevant keywords
2. If insufficient → scan `memory/monthly/*.md`
3. Drill into `memory/weekly/*.md`
4. Read original `memory/YYYY-MM-DD.md` for full detail

Daily files are never deleted — weekly/monthly summaries are indexes, not replacements.

## Context Management
- System prompt files are loaded every API call — keep them lean
- Detailed info goes in knowledge/ (RAG searchable, not in system prompt)

## Cost Optimization
- Prefer short, precise tool calls over exploratory ones
- Batch related reads into one session
- Don't re-read files you've already read this session
- Use qmd search before reading large knowledge files

## File Limits (bootstrapMaxChars)
- Each file injected into system prompt costs tokens
- If files grow too large, they get truncated by the gateway
- Regularly prune completed tasks from SCRATCHPAD and WORKING.md
