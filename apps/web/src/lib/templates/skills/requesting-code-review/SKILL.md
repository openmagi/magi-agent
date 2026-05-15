---
name: requesting-code-review
description: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
---

# Requesting Code Review

Self-review your work systematically before committing or moving on.

**Core principle:** Review early, review often.

## When to Review

**Mandatory:**
- After completing a major feature or task
- Before merge to main
- After completing a plan's task batch

**Optional but valuable:**
- When stuck (fresh perspective from re-reading)
- Before refactoring (baseline check)
- After fixing complex bug

## Self-Review Process

1. **Get the diff:**
```
system.run ["git", "diff", "--stat"]
system.run ["git", "diff"]
```

2. **Review checklist:**

- [ ] Does the code match the plan/requirements?
- [ ] Are there any unintended changes?
- [ ] Are edge cases handled?
- [ ] Are there security issues? (hardcoded secrets, injection points, permissions)
- [ ] Are tests covering the new/changed behavior?
- [ ] Is error handling appropriate?
- [ ] Is the code DRY? No unnecessary duplication?
- [ ] YAGNI -- no over-engineering or unused features?

3. **Act on findings:**
- Fix critical issues immediately
- Fix important issues before proceeding
- Note minor issues in SCRATCHPAD for later

## For PR Reviews (when using GitHub)

```
system.run ["git", "log", "--oneline", "main..HEAD"]
system.run ["git", "diff", "main..HEAD", "--stat"]
```

Review each commit's changes against the plan.

## Red Flags

**Never:**
- Skip review because "it's simple"
- Ignore issues found during review
- Proceed with unfixed critical issues
- Commit without reviewing diff
