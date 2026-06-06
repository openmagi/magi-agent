# Hooks

Hooks let projects attach policy and evidence behavior without rewriting the
agent core.

## Useful hook points

- context building;
- model request preparation;
- tool call validation;
- tool result normalization;
- evidence extraction;
- completion classification;
- final projection;
- audit reporting.

## Hook rules

- Keep hooks narrow and testable.
- Do not hide external side effects inside prompt text.
- Prefer digest-safe public evidence over raw private payloads.
- Make disabled/default behavior explicit.

