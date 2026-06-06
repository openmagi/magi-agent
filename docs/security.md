# Security

Magi Agent treats permissions and evidence as runtime concerns.

## Secrets

- Keep provider and integration keys out of prompts.
- Do not commit `.env` files.
- Do not project raw auth material to users or logs.
- Use digest-safe evidence when a secret-bearing action must be audited.

Common secret sources:

- model provider keys;
- integration API keys;
- channel tokens;
- server gateway tokens;
- local files that contain credentials;
- external connector session material.

## Workspace safety

Path-sensitive tools should stay inside the configured workspace root. File
mutation tools should record receipts and support rollback or repair where
appropriate.

Use plan mode for inspection-only work:

```bash
magi --mode plan "Review this repository and propose a fix"
```

Use act mode only when mutations are intended:

```bash
magi --mode act "Apply the approved fix"
```

## External authority

External systems should require explicit integration settings and scoped
credentials. Avoid broad credentials when a narrower token or toolkit scope is
available.

High-authority actions include:

- writing files;
- running shell commands;
- sending channel messages;
- calling external APIs with credentials;
- spending money or consuming quota;
- updating memory or durable knowledge;
- scheduling background work.

## Public Projection

Public events and final answers should avoid:

- raw provider request or response bodies;
- hidden reasoning;
- private filesystem paths;
- auth headers and cookies;
- secret-bearing URLs;
- customer or workspace payloads that were not meant for display.

Prefer digest-safe receipts and short public summaries.

## Local Server Safety

`magi-agent serve` is convenient for local work. Before exposing it beyond
trusted localhost, set `GATEWAY_TOKEN`, review enabled feature flags, and put the
server behind an authenticated network boundary.
