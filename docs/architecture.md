# Architecture

Magi Agent is a local agent runtime wrapped with policy, tools, evidence, and
projection. It uses model-runner primitives while keeping the user-facing
contract centered on work: context, tools, receipts, validation, repair, and
safe output.

## Core layers

| Layer | Role |
| --- | --- |
| Context projector | Builds model-visible context |
| Runner boundary | Calls the model/runtime runner |
| ToolHost | Owns tool execution and approvals |
| Evidence ledger | Records public-safe receipts |
| Validators | Check claims, actions, and completion |
| Repair policy | Decides retry, ask, downgrade, block, or abstain |
| Output projector | Renders user-visible output |

## Design rule

The model can propose. The runtime decides what becomes state, memory, file
content, external side effect, or user-visible output.

## Data Flow

```text
user request
  -> context projector
  -> model runner
  -> tool and permission boundary
  -> evidence ledger
  -> validators and repair policy
  -> output projector
  -> dashboard, CLI, API, or channel surface
```

## Runtime-only State

The model should not receive everything the runtime knows. Runtime-only state
can include private logs, raw tool output, credential-bearing payloads, hidden
provider details, and audit data. The context projector decides which bounded
context is safe and useful for the next model step.

## Public Surface

The public OSS package exposes:

- `magi` for terminal work;
- `magi-agent serve` for the HTTP API and dashboard;
- `/dashboard` for local operator visibility;
- `/health` and `/healthz` for readiness;
- optional streaming and tool-admin routes when enabled.

## Extension Surface

Skills, hooks, contracts, tool policy, memory policy, and integration settings
extend the runtime without requiring a second agent implementation.
