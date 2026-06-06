# Integrations

Integrations are side-effect surfaces controlled by ToolHost and policy.

External systems such as Slack, documents, storage, browser sessions, and chat channels should require approvals, receipts, idempotency receipts, and governed projection.

## External side-effect boundary

An integration call is not direct model authority. The model proposes an action, ToolHost computes the action digest, policy checks approvals and idempotency receipts, then the runtime records delivery or mutation receipts.

Channel delivery is also a projection boundary. User-visible messages should be derived from governed output projection, not raw draft text or hidden tool output.

- Slack drafts require validation before they are sent.
- Document and artifact delivery require public-safe projection.
- Browser and external API actions require least privilege and auditability.
- Repeated side effects require idempotency receipts.
