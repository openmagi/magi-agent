# Integrations

> **Note — default-off.** External side-effect surfaces (chat channels, Composio) ship gated; they require explicit scope, credentials, and approval, and most run in shadow / record-intent mode today.

Integrations are side-effect surfaces controlled by ToolHost and policy.

External systems such as Slack, documents, storage, browser sessions, and chat channels should require approvals, receipts, idempotency receipts, and governed projection.

## External side-effect boundary

An integration call is not direct model authority. The model proposes an action, ToolHost computes the action digest, policy checks approvals and idempotency receipts, then the runtime records delivery or mutation receipts.

Channel delivery is also a projection boundary. User-visible messages should be derived from governed output projection, not raw draft text or hidden tool output.

- Slack drafts require validation before they are sent.
- Document and artifact delivery require public-safe projection.
- Browser and external API actions require least privilege and auditability.
- Repeated side effects require idempotency receipts.

## Concrete integration surfaces

- **Chat channels (Telegram, Discord).** Implemented under
  `magi_agent/channels/` as adapter → boundary → dispatcher. They validate,
  redact secrets / private paths, and record send intents and receipts. Live
  send/receive is **default-off / shadow** today — the adapters produce
  local-fake receipts, not real delivery. Full guide:
  [channels.md](channels.md).

- **Composio external tools.** An optional external-integration surface lives
  under `magi_agent/composio/` (config, health, and redaction modules). It is
  **optional and default-off**: it requires an explicit Composio configuration
  (scope + credential) and approval before any external tool can be reached, and
  its outputs pass through `redact_composio_text` / `redact_composio_value`
  before they are surfaced. Treat it as a gated surface — enabling it does not
  bypass the side-effect boundary above.
