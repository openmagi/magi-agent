# Contracts

Contracts define what must be true before a run can claim success.

## Common contract checks

- A source-sensitive claim needs source evidence.
- A coding success claim needs read, mutation, diff, and verification evidence.
- A delivery claim needs a delivery receipt.
- A memory write needs a public-safe memory receipt.
- A delegated result needs an accepted child envelope.
- A final answer should not leak private paths, auth material, raw prompts, or
  hidden provider data.

## Failure behavior

When evidence is missing, the runtime should repair, ask, downgrade, abstain, or
block. Silent success is worse than a clear blocker.

