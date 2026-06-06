# Customization

Customize Magi Agent with recipes, harnesses, hooks, and projection rules.

Recipes and harnesses are runtime policy extensions, not prompt snippets. They define state, evidence rules, boundary checks, repair behavior, and projection rules.

## Recipes are not prompt snippets

A recipe or harness should define the route, policy snapshot inputs, model-visible context projection, ToolHost boundaries, validators and guardrails, repair behavior, and governed output projection.

Prompt text can help the model cooperate, but the runtime should own whether a proposal becomes state, output, memory, artifact, or side effect.

## Composable runtime surfaces

Harnesses compose by declaring the state they own and the boundaries they gate. A source-verification harness can own source receipts and claim links. A delivery harness can own output projection and delivery receipts. An approval harness can own approval receipts and action digests.

- State declarations define runtime-only records.
- Evidence rules define what receipts satisfy claims or actions.
- Boundary checks define when validators run.
- Repair behavior defines retry, downgrade, abstain, block, or approval.
- Projection rules define what enters context, output, memory, artifacts, or channels.
