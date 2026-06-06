# Skills

Skills package repeatable expertise for the runtime.

## What Skills Do

A skill is a local instruction package that tells the agent when specialized
knowledge or a workflow applies. Skills are useful for recurring work such as
source-grounded research, code review, document drafting, spreadsheet checks, or
domain-specific triage.

## Skill shape

A useful skill explains:

- when to use it;
- required inputs;
- allowed tools;
- expected output;
- verification steps;
- safety constraints.

Recommended frontmatter:

```yaml
name: concise-skill-name
description: Use when the task clearly matches this workflow.
```

Recommended body:

- trigger conditions;
- step-by-step process;
- required artifacts or evidence;
- allowed and disallowed tools;
- output format;
- failure or escalation behavior.

## Local skills

Local skills can live in a workspace or repository. Keep skill text specific
enough that the runtime can choose it, but not so broad that it fires on every
task.

Good skill descriptions are concrete:

```text
Use when reviewing a pull request for correctness, regressions, and missing
tests.
```

Weak skill descriptions are vague:

```text
Use for any software work.
```

## Runtime hooks

Some skills include runtime hooks. Hooks should be explicit, public-safe, and
covered by focused tests.

## Verification

When a skill changes completion criteria, it should name the verification
evidence. Examples:

- code review: file and line references plus severity;
- research: source spans and unsupported-claim handling;
- spreadsheets: formula checks and recalculation evidence;
- documents: rendered or exported artifact verification;
- automation: delivery receipt or concrete blocker.
