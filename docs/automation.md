# Automation

Automation runs through task contracts and durable checkpoints.

Scheduled, background, and delegated work should be controlled by runtime contracts, receipts, repair policy, and append-only audit checkpoints.

## Task contracts and checkpoints

Automation should not grant direct model authority over time, channels, or external systems. It should create a task contract, run under policy, record checkpoints, and project status safely.

Durable checkpoints let the runtime resume, repair, or explain work without copying raw private traces into user-visible output.

- Cron and scheduled work need explicit delivery and channel policy.
- Background child work needs parent-safe summaries and receipt-backed imports.
- Long-running tasks need checkpoints that separate private state from user-visible projection.
