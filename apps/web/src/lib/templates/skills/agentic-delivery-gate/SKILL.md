---
name: agentic-delivery-gate
description: Use before delivering a completed artifact, report, code change, or operational result; checks that the work is real, accessible, verified, and honestly described
---

# Agentic Delivery Gate

Use this skill at the end of non-trivial work.

## Gate

Before final delivery, confirm:

1. The requested artifact or change exists.
2. The user can access it through the promised channel.
3. Verification evidence supports any success claims.
4. Remaining risks or skipped checks are stated.
5. The final answer does not include hidden reasoning, secrets, or irrelevant process logs.

## Artifact Delivery

If this turn created a user-facing file, report, document, spreadsheet, image,
chart, archive, or persistent artifact, the user must be able to access it from
the chat or KB before you finish.

- Web/app chat: run `file-send.sh <path> <channel>` and include the exact
  `[attachment:<id>:<filename>]` marker in the final answer.
- KB persistence: run `kb-write.sh --add <collection> <filename> --stdin` or the
  knowledge-write integration before saying it is saved.
- A plain workspace path or `fileRead:` reference is not delivery. It is only a
  fallback when attachment/KB delivery is temporarily unavailable, and you must
  say that explicitly.

## Delivery Language

Say what changed, where it is, and what was verified. Do not say "done", "fixed", "passing", or "deployed" without evidence.
