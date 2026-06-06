# Hook Points

Lifecycle hook points with HookRegistry registration and HookBus dispatch.

The HookPoint enum defines 15 lifecycle points. Hooks are registered via HookManifest into the HookRegistry (hooks/registry.py), then dispatched by HookBus (hooks/bus.py) which filters by point + harness scope, executes each enabled hook, catches exceptions (fail_open: log, !fail_open + blocking: block turn), and returns a HookBusRunResult with final_action (continue/block/pending_control_request).

## Turn lifecycle hooks

Turn hooks fire at the start and end of each agent turn. They observe the full turn lifecycle and can inject context or record audit events.

- beforeTurnStart: fires before the turn begins processing. ADK mapping: before_agent_callback. Use for context injection, policy setup, or blocking a turn before it starts.
- afterTurnEnd: fires after the turn is committed, blocked, or aborted. Payload includes userMessage, assistantText, status (committed | aborted), and optional reason. Use for audit logging, cleanup, or follow-up actions.

## Model call hooks

Model hooks fire before and after each LLM API call within a turn. A single turn may include multiple model calls if the agent uses tools.

- beforeLLMCall: fires before each LLM API request. ADK mapping: before_model_callback. Use for context injection, prompt modification, or blocking specific model calls.
- afterLLMCall: fires after each LLM API response. ADK mapping: after_model_callback. Use for response validation, quality checks, or evidence collection.

## Tool use hooks

Tool hooks fire around individual tool executions. They are the primary mechanism for tool policy enforcement and evidence collection.

- beforeToolUse: fires before a tool is executed. ADK mapping: before_tool_callback. Use for tool approval, input validation, or blocking denied tools. This is where tool denial (denied, not_found, not_exposed) is enforced.
- afterToolUse: fires after a tool completes execution. ADK mapping: after_tool_callback. Use for result validation, evidence recording, and contract trigger evaluation (EvidenceTrigger afterToolUse).

## Commit hooks

Commit hooks fire around the turn commit phase. The beforeCommit hook is the primary enforcement point for verifiers and evidence contracts with trigger beforeCommit.

- beforeCommit: fires before the turn is committed. Payload includes assistantText, toolCallCount, toolReadHappened, userMessage, retryCount, toolNames, filesChanged. Blocking hooks at this point can reject the turn and trigger a retry.
- afterCommit: fires after a successful commit. Payload includes assistantText. Use for post-commit audit, notifications, or side effects.

## Error and abort hooks

Error hooks fire when something goes wrong during a turn.

- onAbort: fires when a turn is aborted before completion. Payload includes reason. Use for cleanup and error reporting.
- onError: fires when an unhandled error occurs. Use for error logging and recovery.

## Task, context, policy, and artifact hooks

These hooks fire at specific lifecycle events outside the core turn loop.

- onTaskCheckpoint: fires after each completed turn with summary data. Payload includes userMessage, assistantText, toolCallCount, toolNames, filesChanged, startedAt, endedAt. Use for progress tracking and task-level audit.
- beforeCompaction: fires before context compaction. Use for preserving important context before it is compacted.
- afterCompaction: fires after context compaction completes. Use for updating references to compacted content.
- onRuleViolation: fires when a HarnessRule is violated. Use for audit logging of policy violations.
- onArtifactCreated: fires when an artifact is created. Use for artifact tracking, delivery verification, or evidence collection.

## HookRegistry, HookBus, and HookManifest configuration

Hooks are registered into HookRegistry (hooks/registry.py) as HookRegistration records (manifest, enabled, protected). The registry supports enable/disable/resolve/list_enabled operations. At each lifecycle point, HookBus (hooks/bus.py) dispatches via HookBus.run(point, context, harness_state) which returns HookBusRunResult with final_action (continue/block/pending_control_request), observation (effective_hooks, skipped_by_scope, failed_open, failed_closed, blocked_by), and permission_boundary.

- blocking=True + fail_open=False: hook failure blocks the operation (strictest).
- blocking=True + fail_open=True: hook failure logs a warning but allows the operation to continue.
- blocking=False: hook runs asynchronously and cannot block the operation regardless of fail_open.
- security_critical=True: marks hooks that enforce security invariants. These hooks should not be opted out of.
- Hooks at the same point are ordered by priority (lower values run first, default 100).

### HookManifest fields

```
class HookManifest(BaseModel):
    name: str              # unique hook identifier
    point: HookPoint       # lifecycle point (15 enum values)
    description: str       # human-readable purpose
    source: ToolSource     # where the hook code lives
    priority: int = 100    # execution order (lower = first)
    blocking: bool = True  # can block the operation
    fail_open: bool = False # on failure: True=continue, False=fail
    timeout_ms: int = 5000 # max execution time (ms)
    enabled: bool = True   # active or inactive
    security_critical: bool = False  # security invariant hook
    if_condition: str | None = None   # conditional execution
    scope: HookScope       # scoping to specific contexts
    opt_out: bool = True   # can be opted out of
```
