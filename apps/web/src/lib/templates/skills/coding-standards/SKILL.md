---
name: coding-standards
description: "Use when writing, reviewing, or committing code. Covers TDD, plan mode, self-review, git conventions, and code quality rules."
---

# Coding Standards

## Code Change Rules (MANDATORY)

Any task that touches code requires ALL of the following:

### Core Rules

1. **Understand first** — Read all related source before writing code. Partial reads cause debug spirals. For integrations (MCP, API, SDK): read the full implementation, not just the schema.
2. **Plan first** — 3+ steps → write `plans/CURRENT-PLAN.md` before touching code
3. **TDD** — Test first → confirm failure → minimal impl → refactor. No test = no code.
4. **Verification** — Before committing: tests pass + diff review + self-review
5. **No one-shot coding** — Small increments, testing as you go
6. **Check MCP catalog before external integrations** — Before building a new integration (payments, search, cloud, blockchain, etc.), search the internal MCP catalog first: `qmd search "keyword"`. Full catalog: `knowledge/useful-mcps.md`.

### Project Sandbox Protocol

Before writing source files for a coding task:

1. Create or reuse a dedicated project sandbox with the native `CodeWorkspace` tool, or manually use `/workspace/code/<project>/`.
2. Initialize git inside that directory before editing so `GitDiff` and `CommitCheckpoint` can capture evidence.
3. Keep project files, dependencies, build outputs, generated artifacts, and test fixtures inside the sandbox. Do not scatter code in `/workspace/`.
4. Run verification from the sandbox with `TestRun`, then capture changed files with `GitDiff` before claiming completion.
5. No Docker-in-Docker, privileged containers, root operations, or host Docker socket mounts are available. If Docker config is requested, author it as an artifact and verify with the closest source-native command.

## Plan Mode

When to enter: 3+ files, new feature, architecture change, external system integration, debugging failed 2x.

1. Declare "Entering plan mode" — no file modifications
2. Explore related code, dependencies, tests (read-only)
3. Write plan to `plans/CURRENT-PLAN.md`
4. Execute after approval, step by step with TDD

## Self-Review (before every commit)

1. Diff — only intended changes
2. Edge cases — error handling, boundaries, null/empty
3. Clean up — no debug logs, commented-out code, unrelated formatting
4. Test coverage — tests for all added/changed logic
5. Security — no hardcoded secrets, no injection vectors

## Git

- Commit frequently — one logical change per commit
- Messages: `feat:`, `fix:`, `refactor:`, `test:`, `chore:`, `docs:`
- Never commit secrets

## Code Quality

- DRY, YAGNI, explicit over clever
- Error handling at boundaries (user input, external APIs)
- Cover edge cases in tests
