# Integrations

Integrations connect Magi Agent to external systems. They should be explicit,
scoped, and reversible.

## Integration Rules

- Install optional dependencies only for integrations you intend to use.
- Keep credentials in environment variables or a secret manager.
- Scope toolkits and accounts narrowly.
- Require approval for external mutation unless unattended execution is
  explicitly allowed.
- Record delivery or action receipts.
- Redact credential material from logs, events, and final answers.

## Composio

Composio can be used as an optional connector layer. Open-source users should
create their own Composio account and provide their own credentials when they
want to connect external apps.

Do not enable an external integration just because a credential exists. Require
an explicit integration setting, toolkit scope, and user approval for
credentialed actions.

Example:

```bash
export COMPOSIO_API_KEY=...
export MAGI_COMPOSIO_ENABLED=auto
export MAGI_COMPOSIO_TOOLKITS=github,slack
magi doctor
magi auth composio status
```

The status commands report integration readiness. They do not grant approval for
tool calls by themselves.

## Local and custom tools

Custom tools should declare:

- required credentials;
- allowed operations;
- mutation behavior;
- approval requirements;
- receipt fields;
- public projection rules.

## Channels

Channel adapters such as chat, push, Telegram, Discord, or other delivery
surfaces should be configured as external authority. A channel send is not
complete until the runtime records a delivery status or reports a blocker.

## MCP and Tool Servers

When connecting MCP or other tool servers, document:

- server label and URL;
- credential source;
- allowed tools;
- timeout and retry behavior;
- whether calls are read-only or mutating;
- what evidence is recorded.
