# Customization

Customize Magi Agent with skills, hooks, model profiles, and workflow policy.

## Customization Map

Use customization when the agent needs to follow durable rules, not just one-off
prompt style. Good customization is explicit, testable, and easy to disable.

- Skills package repeatable instructions and optional workflow knowledge.
- Hooks observe or gate runtime lifecycle stages.
- Tool policy controls what the model may ask the runtime to do.
- Contracts describe what evidence must exist before a run can claim success.
- Model profiles describe model capability rather than brand preference.
- [Modes](modes.md) are saved agent postures — a system prompt plus a tool
  scope — that you switch on per turn from the chat composer.

## Skills

Skills are local instruction packages, usually stored as `SKILL.md` files. A
skill should explain when it applies, what inputs it expects, what tools it can
use, and what evidence counts as completion.

Recommended shape:

```markdown
---
name: source-grounded-research
description: Use when answers must be backed by inspected source material.
---

1. Inspect allowed sources before making factual claims.
2. Record source spans or file paths for every material claim.
3. Downgrade or abstain when sources do not support the answer.
4. Final output must separate supported facts from open questions.
```

## Hooks

Runtime hooks can inspect or modify specific stages such as context projection,
tool calls, evidence extraction, final projection, or repair. Hooks should be
small, testable, and explicit about what they are allowed to change.

Prefer hooks for rules that must run at a specific boundary:

- before a model request is built;
- before a tool call is approved;
- after a tool result is normalized;
- before a memory write is accepted;
- before the final answer is projected.

## Workflow rules

Write workflow-specific rules in source-controlled Markdown or config files
when possible. Good rules say:

- what triggers the rule;
- which tools or evidence are required;
- whether the runtime should ask, repair, downgrade, block, or continue;
- what is safe to show to the user.

For reusable governed workflows, prefer a recipe. Recipes can declare selected
harnesses, tool categories, approvals, evidence requirements, repair policy, and
projection behavior. See [Recipes](recipes.md).

## Model profiles

Model profiles describe capability, context length, latency, cost, and tool
behavior. Route by capability rather than brand.

Useful profile fields:

- reasoning strength;
- context window and output limit;
- tool-call reliability;
- latency and cost;
- structured-output behavior;
- provider-specific safety or rate-limit constraints.

## Safe Rollout

Start rules in observe/audit mode when possible, review the evidence they would
require, then make them blocking only when the rule is specific enough. Avoid
workspace-wide rules that match every task unless the behavior is truly
universal.

Harnesses are the safety layer for custom workflows. They should fail closed,
record public-safe receipts, and reject unsupported claims or side effects. See
[Harnesses](harnesses.md).
