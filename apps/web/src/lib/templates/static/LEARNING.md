# Learning Protocol

Hipocampus remains the primary long-term memory system. Do not replace,
rewrite, or bypass Hipocampus memory, compaction, qmd recall, or
`memory/ROOT.md`.

## Memory Roles

- `USER.md` stores stable user profile, preferences, constraints, and working
  style. Update it only when a preference or durable user fact becomes clear.
- `memory/` stores chronological durable history. Daily logs are source
  records; weekly, monthly, and root files are indexes and compactions.
- `knowledge/` stores durable reference material and domain notes that should
  be searchable without bloating the always-loaded prompt.
- `skills-learned/<name>/SKILL.md` stores procedural memory: reusable methods
  learned from successful multi-step work.

## Learned Skills

Create a learned skill only after a successful task when all of these are true:

1. The task revealed a reusable procedure or decision pattern.
2. The pattern is not already covered by `skills/`.
3. The procedure can be written as a short, concrete `SKILL.md`.

Rules:

- Keep learned skills under 4 KB.
- Use standard skill frontmatter plus concrete steps.
- Prefer specific procedures over broad advice.
- Do not store secrets, credentials, private keys, or raw user data.
- Do not create a learned skill for routine one-off work.

## Reflection Cadence

At task completion, briefly decide whether the work produced:

- a user/profile update for `USER.md`
- a historical note for `memory/YYYY-MM-DD.md`
- reference material for `knowledge/`
- a procedural skill for `skills-learned/`

Most tasks should update none or one of these. Keep Hipocampus concise and
let runtime recall retrieve details when needed.
