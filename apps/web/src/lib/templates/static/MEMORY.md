# MEMORY.md — Long-Term Memory

## Compaction Root
<!-- last_compacted: never -->
(Empty — will be populated by first heartbeat compaction. See skills/memory-compaction/SKILL.md)

## Core (Static) — DO NOT compact or remove

These entries survive every compaction cycle. Only update when facts change.

### Identity & Config
(User name, timezone, language, role, accounts, credentials received — last 4 chars only)

### Rules & Preferences
(User decisions, chosen approaches, communication preferences — "user prefers X over Y because Z")

## Adaptive (Dynamic) — Subject to compaction

These entries evolve per session. During heartbeat consolidation, summarize or prune oldest entries first.
~50 line limit applies to Core + Adaptive sections only (excludes Compaction Root).

### Lessons Learned
(Mistakes made, patterns discovered, things that worked/didn't — prevent repeating errors)

### Active Context
(Ongoing projects, recurring topics, things to follow up on, recent key decisions)
