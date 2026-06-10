# General Automation

Worked example covering approval gates, delivery boundaries, artifact verification, and commit boundaries using openmagi.office-automation and openmagi.spreadsheet-automation packs.

A general automation workflow uses boundary modules (artifacts/delivery_boundary.py, runtime/commit_boundary.py) and evidence types (FileDeliver, ArtifactVerify, CommitCheckpoint) to govern background and scheduled tasks. Relevant first-party packs: openmagi.office-automation, openmagi.spreadsheet-automation.

## Automation scenario

User request: Generate the weekly report, deliver it as a PDF to the team channel, and save a summary to memory.

This workflow crosses multiple trust boundaries: file creation (workspace mutation), artifact delivery (delivery_boundary), memory write (write_boundary), and commit (commit_boundary). Each boundary has a dedicated boundary module that validates proposals before they become durable.

## Implemented boundary modules

The runtime includes dedicated boundary modules that enforce policy at specific control points. These are the real enforcement surfaces, not conceptual hook names:

- artifacts/delivery_boundary.py -- governs artifact delivery to channels and external targets
- runtime/commit_boundary.py -- validates state before committing results
- memory/write_boundary.py -- validates memory writes for safety and policy
- runtime/child_runner_boundary.py -- governs child agent result imports
- evidence/tool_boundary.py -- validates tool execution and produces evidence records
- evidence/enforcement_boundary.py -- enforces evidence contract verdicts
- runtime/activity_boundary.py -- general activity boundary for tool execution

## Delivery and artifact evidence

The FileDeliver and ArtifactVerify builtin evidence types track delivery operations. FileDeliver records that a file was delivered to a destination. ArtifactVerify records that an artifact was checked for correctness before publication.

Evidence contracts can require FileDeliver evidence at the beforeCommit trigger to ensure deliveries completed before the run commits. The delivery_boundary module validates that deliveries meet channel-safe projection requirements.

### Delivery evidence contract (Python, uses real types)

```
delivery_contract = EvidenceContract(
    id='automation.delivery-complete',
    description='File delivery must complete before commit',
    triggers=('beforeCommit',),
    requirements=(
        EvidenceRequirement(type='FileDeliver'),
    ),
    on_missing='audit',
    retry_message='Deliver the artifact before completing.',
)

artifact_contract = EvidenceContract(
    id='automation.artifact-verified',
    description='Artifact must be verified before delivery',
    triggers=('afterToolUse',),
    requirements=(
        EvidenceRequirement(type='ArtifactVerify'),
    ),
    on_missing='audit',
)
```

## Commit checkpoints

The CommitCheckpoint evidence type records a durable checkpoint at the commit boundary. This allows long-running or scheduled tasks to create resumption points. The commit_boundary module validates the checkpoint before it becomes durable.

The verification pack includes verifier gates (answer-quality, self-claim, deterministic-evidence) that apply to automation tasks on main runs. The hard-safety gates (permission-arbiter, path-safety, secret-safety, sealed-file-policy, git-safety) always apply and cannot be opted out.

## Relevant hook points

The 15 lifecycle hook points (HookPoint enum) are: beforeTurnStart, afterTurnEnd, beforeLLMCall, afterLLMCall, beforeToolUse, afterToolUse, beforeCommit, afterCommit, onAbort, onError, onTaskCheckpoint, beforeCompaction, afterCompaction, onRuleViolation, onArtifactCreated.

For automation tasks, the most relevant triggers are afterToolUse (check evidence after each operation), beforeCommit (validate before committing), onTaskCheckpoint (record progress), and onArtifactCreated (verify artifacts before delivery).
