---
name: using-skills
description: Use when starting any development task - establishes how to find and use skills, requiring skill file reading before any coding action
---

# Using Skills

## The Rule

**Read relevant skills BEFORE any coding action.** Even a 1% chance a skill might apply means you should check it.

## How to Access Skills

1. Read all skill metadata (frontmatter) at once:
   ```
   system.run ["sh", "-c", "awk '/^---$/{n++} n<=2' skills/*/SKILL.md"]
   ```
2. If a skill's description matches your current task, read the full SKILL.md and follow it:
   ```
   system.run ["cat", "skills/<name>/SKILL.md"]
   ```
3. If SKILL.md references supporting files, read them as needed:
   ```
   system.run ["ls", "skills/<name>/"]
   system.run ["cat", "skills/<name>/references/<file>.md"]
   ```

## Skill Priority

When multiple skills could apply, use this order:

1. **Process skills first** (brainstorming, debugging) -- these determine HOW to approach the task
2. **Implementation skills second** (github, domain-specific) -- these guide execution

"Build X" -> brainstorming first, then implementation skills.
"Fix this bug" -> debugging first, then domain-specific skills.

## Red Flags

These thoughts mean STOP -- you're rationalizing:

| Thought | Reality |
|---------|---------|
| "This is just a simple task" | Simple tasks have root causes and edge cases. Check skills. |
| "I need more context first" | Skill check comes BEFORE investigating. |
| "This doesn't need a formal skill" | If a skill exists, check it. |
| "The skill is overkill" | Simple things become complex. Use it. |
| "I'll just do this one thing first" | Check BEFORE doing anything. |

## Skill Types

**Rigid** (TDD, debugging, verification): Follow exactly. Don't adapt away discipline.

**Flexible** (brainstorming, github): Adapt principles to context.

The skill itself tells you which.

## User Instructions

Instructions say WHAT, not HOW. "Add X" or "Fix Y" doesn't mean skip skill workflows.

## Skill Usage Log (MANDATORY after every skill)

After completing any skill from `skills/`, append ONE JSON line to `skill-usage.jsonl`:

```
system.run ["sh", "-c", "printf '%s\\n' '{\"s\":\"<skill-dir-name>\",\"ts\":\"<ISO-8601-UTC>\",\"o\":\"<outcome>\",\"n\":<steps>}' >> skill-usage.jsonl"]
```

Fields:
- `s`: skill directory name (e.g., `google-calendar`, `ad-copywriter`)
- `ts`: ISO-8601 UTC timestamp from `[Current Time]` tag
- `o`: `"ok"` = completed all steps, `"partial"` = some steps done, `"fail"` = could not complete primary objective
- `n`: number of major steps completed (integer)
- Add `,"e":"<short reason>"` field ONLY if outcome is `"fail"`

Example (success): `{"s":"google-calendar","ts":"2026-03-13T09:30:00Z","o":"ok","n":3}`
Example (failure): `{"s":"slack-integration","ts":"2026-03-13T10:15:00Z","o":"fail","n":1,"e":"OAuth token expired"}`

This is one append — takes <1 second. Never skip it.
