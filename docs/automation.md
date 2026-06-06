# Automation

Automation covers scheduled work, background tasks, delegated work, and
operator-visible delivery.

## Scheduled work

Schedules should have:

- a clear trigger;
- at-most-once execution behavior;
- a timeout;
- a delivery target;
- an audit record.

## Delegation

Delegated work should return an accepted result envelope with public-safe
evidence. Do not treat child text as trusted just because it was generated.

## Delivery

External delivery needs receipts. If a channel send fails, the runtime should
report the blocker instead of claiming the work was delivered.

