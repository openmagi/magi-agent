# Security

Magi Agent treats permissions and evidence as runtime concerns.

## Secrets

- Keep provider and integration keys out of prompts.
- Do not commit `.env` files.
- Do not project raw auth material to users or logs.
- Use digest-safe evidence when a secret-bearing action must be audited.

## Workspace safety

Path-sensitive tools should stay inside the configured workspace root. File
mutation tools should record receipts and support rollback or repair where
appropriate.

## External authority

External systems should require explicit integration settings and scoped
credentials. Avoid broad credentials when a narrower token or toolkit scope is
available.

