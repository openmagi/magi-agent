# Architecture

Magi Agent is a Python ADK runtime wrapped with policy, tools, evidence, and
projection.

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

