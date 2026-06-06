# Work Console

The local dashboard for inspecting, approving, and managing agent work.

The work console provides a local UI for monitoring agent runs, reviewing proposals, and managing approvals.

## Runtime events and progress signals

The work console is planned as part of the local app installer. The information below describes the runtime events and status signals that the console will surface.

During a run, the runtime emits events that surface progress to the work console. Tool evidence records (ToolEvidenceRecord) show each tool call with its status, duration, and sanitized argument/result summaries. Evidence contract verdicts show whether required evidence has been collected. Boundary decisions show whether side effects were allowed or blocked.

These events are streamed via SSE (Server-Sent Events) from the Node.js chat-proxy to connected clients. The chat-proxy observes the agent's SSE output and enriches it with skill tracking metadata and pipeline status.

## Evidence records as work receipts

Every tool call produces a ToolEvidenceRecord with kind, tool_name, status (ok/failed/unknown), arg_summary, result_summary, args_hash, and result_hash fields. These records serve as receipts that the work console can display to show what the agent did and what evidence it collected.

Builtin evidence types include GitDiff, TestRun, CodeDiagnostics, CommitCheckpoint, FileDeliver, ArtifactVerify, WebSearch, KnowledgeSearch, SourceInspection, PlanVerifier, Calculation, DateRange, Clock, and TelegramDeliveryAck. Each type captures domain-specific fields relevant to that class of work.

## Boundary decisions as status updates

Each boundary module produces typed decisions that the console can display. For example, the evidence enforcement boundary produces EvidenceEnforcementDecision with allowed/blocked status. The commit boundary produces CommitBoundaryPlan describing what will be committed. The artifact delivery boundary produces ArtifactChannelDeliveryDecision for file delivery operations.

These decisions are part of the runtime-only control plane and are not visible to the model. The work console is the primary surface where operators see boundary decisions and their outcomes.

## Transport routes and SSE streaming

The Python ADK runtime exposes transport routes via FastAPI (transport/ directory, 9 files): health.py provides /health and /healthz endpoints, chat.py provides POST /v1/chat/completions with Gate5B canary checks, and shadow_generations.py / shadow_invocations.py provide diagnostic testing routes. Additional admin routes in transport/plugins.py and transport/tools.py expose metadata.

SSE streaming is implemented in the Node.js chat-proxy. The Python ADK runtime events (EventKind: status, token, tool, control, artifact, error) are internal to the agent process and are surfaced to the chat-proxy through the agent's stdout event stream. A dedicated work console UI is planned as part of the local app installer but is not yet shipped.
