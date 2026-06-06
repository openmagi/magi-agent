# Contracts

Contracts define what must be true before a run can claim success.

## Why Contracts Matter

Prompt text can ask a model to be careful, but a contract gives the runtime a
checkable work order. The contract records the goal, constraints, required
artifacts, allowed resources, evidence requirements, and completion criteria.

Use contracts for work products and hard requirements. Tone preferences belong
in instructions; source, delivery, approval, and verification requirements
belong in a contract.

## Common contract checks

- A source-sensitive claim needs source evidence.
- A coding success claim needs read, mutation, diff, and verification evidence.
- A delivery claim needs a delivery receipt.
- A memory write needs a public-safe memory receipt.
- A delegated result needs an accepted child envelope.
- A final answer should not leak private paths, auth material, raw prompts, or
  hidden provider data.

## Contract Shape

A useful task contract usually includes:

- `goal`: the user-visible objective;
- `constraints`: boundaries that must survive across turns;
- `acceptance_criteria`: exact outcomes that can pass, fail, or be waived;
- `resource_bindings`: allowed files, source URLs, artifact ids, or handles;
- `required_evidence`: source, file, calculation, test, approval, or delivery
  proof;
- `verification_mode`: none, sample, or full, depending on risk;
- `blockers`: concrete reasons the work cannot finish yet.

Example prompt block:

```xml
<task_contract>
  <verification_mode>full</verification_mode>
  <constraints>
    <item>Use only files under workspace/reports.</item>
    <item>Do not claim delivery until a file delivery receipt exists.</item>
  </constraints>
  <acceptance_criteria>
    <item id="c1">Create the requested report.</item>
    <item id="c2">Verify totals with calculation or test evidence.</item>
    <item id="c3">Deliver the output file.</item>
  </acceptance_criteria>
</task_contract>
```

## Resource Binding

Bind resources when the task must stay inside a known set of files, URLs, or
handles. A source-grounded answer should not silently switch to an arbitrary web
result. A coding task should not edit a file that was never read or approved.

## Failure behavior

When evidence is missing, the runtime should repair, ask, downgrade, abstain, or
block. Silent success is worse than a clear blocker.

Examples:

- Missing source span: inspect another allowed source or remove the claim.
- Missing test evidence: run the configured verification or report why it
  cannot run.
- Missing delivery receipt: deliver the artifact or say delivery is blocked.
- Unsafe external action: ask for approval or keep the action as a draft.
