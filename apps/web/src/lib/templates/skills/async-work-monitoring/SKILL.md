---
name: async-work-monitoring
description: Use when promising later delivery, background work, scheduled checks, reminders, cron jobs, or notifications after the current turn
---

# Async Work Monitoring

Use this skill whenever work will continue after the visible turn.

## Rules

1. Do not promise future notification unless a real background task, cron, or scheduler has been created.
2. Verify the task exists and has the intended trigger, destination, and payload.
3. If scheduling is unavailable, say you cannot notify later and complete what can be done now.
4. Do not rely on memory or intention as a scheduler.

## Required Evidence

Before saying "I'll notify you later" or equivalent, confirm:

- task id or cron id
- schedule or trigger
- delivery channel
- payload summary
- status check showing the task is registered
