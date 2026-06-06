# Automation

Automation covers scheduled work, background tasks, delegated work, and
operator-visible delivery.

## Automation Principle

An automated run should be observable and reversible enough for the operator to
trust it. Do not treat a generated promise as automation. There must be a real
job, receipt, artifact, delivery, or blocker.

## Scheduled work

Schedules should have:

- a clear trigger;
- at-most-once execution behavior;
- a timeout;
- a delivery target;
- an audit record.

Recommended fields:

- schedule id;
- trigger expression or event source;
- workspace and model profile;
- allowed tools;
- approval requirements;
- retry and idempotency behavior;
- max runtime;
- public delivery target;
- evidence requirements.

## Delegation

Delegated work should return an accepted result envelope with public-safe
evidence. Do not treat child text as trusted just because it was generated.

Use delegation when a subtask has a bounded goal and a concrete output. Avoid
delegation for vague "keep working" loops without acceptance criteria.

## Delivery

External delivery needs receipts. If a channel send fails, the runtime should
report the blocker instead of claiming the work was delivered.

Delivery receipts should identify the target type, target digest or safe label,
artifact id or body digest, delivery status, and timestamp. Raw channel tokens,
private URLs, and secret-bearing payloads should not be projected to the user.

## Human Approval

Automation that mutates external systems, spends money, sends messages, changes
files, or uses high-authority credentials should require explicit approval
unless the workspace has deliberately allowed unattended execution.
