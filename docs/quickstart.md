# Quickstart

This walkthrough proves the runtime and dashboard work together.

## 1. Install and start the server

```bash
brew install openmagi/tap/magi-agent
magi-agent serve --port 8080
```

Leave the server terminal running.

## 2. Open the dashboard

```bash
open http://localhost:8080/dashboard
```

The dashboard is served by the same local process as the API. If the page does
not load, verify that no other process already owns port `8080` and restart with
a different port:

```bash
magi-agent serve --port 8090
open http://localhost:8090/dashboard
```

## 3. Check server health

From another terminal:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/healthz
```

Use health output to separate server startup issues from model or tool
configuration issues.

## 4. Send a local task

From another terminal:

```bash
magi --output text "Check runtime health and summarize the available first-party surfaces."
```

Or use the dashboard prompt:

```text
Inspect this workspace and tell me what you can safely do.
```

For automation, use JSON or streaming JSON output:

```bash
magi --output json "Summarize the current directory"
magi --output stream-json "Summarize the current directory"
```

## 5. Inspect evidence

The dashboard work stream shows public runtime events such as turn phases, tool
progress, evidence receipts, and SSE transport state when the runtime emits
them. A run is not considered useful just because text was generated; it should
also make clear what tools, evidence, or blockers were involved.

## 6. Try plan mode

Plan mode exposes a read-oriented tool surface for review before mutation:

```bash
magi --mode plan "Inspect these docs and propose the smallest safe improvement."
```

Use act mode only when writes and tool execution are intended:

```bash
magi --mode act "Apply the approved docs improvement and run a focused check."
```

## 7. Next steps

- Configure model/provider settings in [Configuration](configuration.md).
- Review tool behavior in [Tools](tools.md).
- Read completion rules in [Contracts](contracts.md).
- Add workflow-specific instructions through [Skills](skills.md) and
  [Hooks](hooks.md).
