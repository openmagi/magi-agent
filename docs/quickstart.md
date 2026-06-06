# Quickstart

This walkthrough proves the runtime and dashboard work together.

## 1. Start the server

```bash
magi-agent serve --port 8080
```

Open:

```bash
open http://localhost:8080/dashboard
```

## 2. Send a local task

From another terminal:

```bash
magi --output text "Check runtime health and summarize the available first-party surfaces."
```

Or use the dashboard prompt:

```text
Inspect this workspace and tell me what you can safely do.
```

## 3. Inspect evidence

The dashboard work stream shows public runtime events such as turn phases, tool
progress, evidence receipts, and SSE transport state when the runtime emits
them. A run is not considered useful just because text was generated; it should
also make clear what tools, evidence, or blockers were involved.

## 4. Next steps

- Configure model/provider settings in [Configuration](configuration.md).
- Review tool behavior in [Tools](tools.md).
- Read completion rules in [Contracts](contracts.md).
- Add workflow-specific instructions through [Skills](skills.md) and
  [Hooks](hooks.md).

