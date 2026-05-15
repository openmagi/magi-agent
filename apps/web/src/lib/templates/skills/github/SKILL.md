---
name: github
description: GitHub patterns using gh CLI for pull requests, code review, branching strategies, and repository automation. Use when working with GitHub PRs, merging, or repository management tasks.
---

# GitHub Patterns

## Tools

Use `gh` CLI for all GitHub operations via `system.run`.

## Common Operations

### Create PR

```
system.run ["gh", "pr", "create", "--title", "<title>", "--body", "<summary>"]
```

### List PRs

```
system.run ["gh", "pr", "list"]
```

### Check PR Status

```
system.run ["gh", "pr", "status"]
```

### View PR Details

```
system.run ["gh", "pr", "view", "<number>"]
```

### Merge PR

```
system.run ["gh", "pr", "merge", "<number>", "--squash"]
```

### Create Issue

```
system.run ["gh", "issue", "create", "--title", "<title>", "--body", "<body>"]
```

## Branching Strategy

- `main` -- stable, deployed
- Feature branches for significant changes
- Commit frequently, one logical change per commit
- Squash merge PRs to keep main history clean

## PR Description Format

```markdown
## Summary
- [2-3 bullet points of what changed]

## Test Plan
- [ ] [Verification steps]
```

## Red Flags

**Never:**
- Force-push to main
- Merge without passing tests
- Skip PR description
- Push secrets or credentials
