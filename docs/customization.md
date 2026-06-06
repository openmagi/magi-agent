# Customization

Customize Magi Agent with skills, hooks, model profiles, and workflow policy.

## Skills

Skills are local instruction packages, usually stored as `SKILL.md` files. A
skill should explain when it applies, what inputs it expects, what tools it can
use, and what evidence counts as completion.

## Hooks

Runtime hooks can inspect or modify specific stages such as context projection,
tool calls, evidence extraction, final projection, or repair. Hooks should be
small, testable, and explicit about what they are allowed to change.

## Workflow rules

Write workflow-specific rules in source-controlled Markdown or config files
when possible. Good rules say:

- what triggers the rule;
- which tools or evidence are required;
- whether the runtime should ask, repair, downgrade, block, or continue;
- what is safe to show to the user.

## Model profiles

Model profiles describe capability, context length, latency, cost, and tool
behavior. Route by capability rather than brand.

