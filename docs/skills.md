# Skills

Status: ✅ Active — 14 first-party skills ship bundled under `magi_agent/skills/bundled/superpowers/`; each is a `SKILL.md` the model can load to follow a procedure.

Skills package workflow knowledge and can participate in runtime policy.

Magi Agent skills should explain procedures while harnesses and runtime surfaces enforce evidence, approvals, repair, projection, and audit.

## Skill role

A skill can teach the model how to perform a workflow, name expected tools, and provide examples. For governed workflows, the skill should pair with runtime policy rather than relying only on prose.

When a skill needs hard guarantees, define the corresponding harness state, boundary checks, receipts, and projection rules.

## Bundled skills

The runtime ships a "superpowers" skill pack under
`magi_agent/skills/bundled/superpowers/`. Each skill is a directory containing a
`SKILL.md` with YAML front matter (`name`, `description`) followed by the
procedure. The 14 bundled skills are:

| Skill | What it teaches |
|---|---|
| `brainstorming` | Turn an idea into an approved design before any implementation. |
| `writing-plans` | Write a structured implementation plan from a spec. |
| `executing-plans` | Execute a written plan with review checkpoints. |
| `subagent-driven-development` | Run independent plan tasks via subagents. |
| `dispatching-parallel-agents` | Fan out 2+ independent tasks with no shared state. |
| `test-driven-development` | Red → green → refactor before writing implementation. |
| `systematic-debugging` | Diagnose a bug before proposing a fix. |
| `verification-before-completion` | Run verification and confirm output before claiming done. |
| `requesting-code-review` | Request review against requirements before merge. |
| `receiving-code-review` | Apply review feedback with technical rigor, not blind agreement. |
| `finishing-a-development-branch` | Decide how to integrate completed work (merge / PR / cleanup). |
| `using-git-worktrees` | Create an isolated worktree for feature work. |
| `writing-skills` | Author / edit / verify a new skill. |
| `using-superpowers` | Find and use the right skill at the start of a task. |

## How a skill is used

A skill is loaded as instructions the model follows; it is not a CLI subcommand.
For example, the `brainstorming` skill's `SKILL.md` front matter is:

```yaml
---
name: brainstorming
description: "You MUST use this before any creative work - creating features,
  building components, adding functionality, or modifying behavior. Explores
  user intent, requirements and design before implementation."
---
```

When the task matches that description (e.g. "add a feature"), the agent loads
`brainstorming/SKILL.md` and follows its procedure — here, a `<HARD-GATE>` that
forbids writing any code until a design is presented and the user approves it.
The skill supplies the procedure; the runtime's boundaries and evidence
contracts supply the enforcement.
