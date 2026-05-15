# Vibe Coding Guide

> **What this is:** Your companion throughout development.
> Keep this file in your project and reference it whenever you need guidance.

---

# TABLE OF CONTENTS

1. [Mindset](#1-mindset)
2. [Prompting](#2-prompting)
3. [Workflows](#3-workflows)
4. [Context Management](#4-context-management)
5. [Debugging](#5-debugging)
6. [Common Mistakes](#6-common-mistakes)
7. [When Stuck](#7-when-stuck)
8. [Model Selection](#8-model-selection)
9. [MCP-First Principle](#9-mcp-first-principle)
10. [CLAUDE.md Authoring](#10-claudemd-authoring)
11. [Skills & Agents](#11-skills--agents)
12. [Hooks System](#12-hooks-system)
13. [Project Type Patterns](#13-project-type-patterns)

---

# 1. MINDSET

## Vibe Coding ≠ No Thinking

Vibe coding means natural language → code, but you still need to:
- **Know what you want** (outcome, not implementation)
- **Review what's generated** (don't blindly accept)
- **Guide direction** (course-correct early)

## MVP First, Always

```
❌ "Build a full e-commerce platform with inventory, payments, analytics..."
✅ "Build a page that lists 3 products with a buy button"
```

Start embarrassingly small. Expand after it works.

## The Fatal Triad

Three things working against you:
1. **Speed** — AI generates faster than you can review
2. **Non-determinism** — Different output each time
3. **Cost pressure** — Temptation to skip verification

**Solution:** Slow down. Review. Verify. It's still faster than manual coding.

## "Vibe Coding" vs "Production"

| Mode | Mindset | Acceptable |
|------|---------|------------|
| **Vibe** | Prototype, explore, learn | Messy code, no tests, quick & dirty |
| **Production** | Ship to users | Tests required, code review, proper error handling |

**Know which mode you're in.** Don't ship vibe code to production.

---

# 2. PROMPTING

## The Golden Rule

```
Specific input → Specific output
Vague input → Vague output (or wrong output)
```

## Bad → Good Examples

### Too Vague
```
❌ "Add authentication"
```

### Just Right
```
✅ "Add email/password login:
   - Use the existing User model in src/models/user.ts
   - Store sessions in localStorage
   - Redirect to /dashboard after login
   - Show error message if credentials invalid"
```

### Over-Specified (also bad)
```
❌ "Add authentication using bcrypt with 12 rounds, 
    JWT with RS256 algorithm, refresh tokens stored in 
    HttpOnly cookies with 7 day expiry, CSRF protection
    using double submit pattern..."
```

Let Claude make reasonable choices. Specify only what matters to you.

## Structure Your Prompts

```markdown
## What I Want
[Describe the feature/change]

## Current State
[What exists now, relevant files]

## Constraints
[Things that must be true]

## Out of Scope
[What NOT to do - prevents over-engineering]
```

## The "Keep It Simple" Modifier

Claude tends to over-engineer. Add this when needed:

```
Keep this simple. No abstractions I didn't ask for.
One file if possible. No factories, no DI, no patterns I didn't request.
```

## Provide Context

Help Claude make good decisions:

```
"This runs on every request — needs to be fast"
"This is a prototype we'll throw away — working > perfect"  
"This is core business logic — needs thorough testing"
"Users are non-technical — error messages should be friendly"
```

## Give Verification Targets

```
The function should pass these test cases:
- input: [] → output: 0
- input: [1,2,3] → output: 6
- input: [-1, 1] → output: 0
```

---

# 3. WORKFLOWS

## Plan Mode (Recommended for Complex Tasks)

**Problem:** Jumping straight to code often solves the wrong problem.

**Solution:** Plan first, implement second.

```
Phase 1: Enter Plan Mode (Shift+Tab twice)
> read src/auth and understand how we handle sessions

Phase 2: Ask for plan
> I want to add Google OAuth. What files need to change?

Phase 3: Review plan, then exit Plan Mode and implement
> implement the OAuth flow from your plan

Phase 4: Verify and commit
> run tests, then commit with a descriptive message
```

### When to Use Plan Mode
- New features touching multiple files
- Unfamiliar parts of codebase
- Anything you can't describe in one sentence

### When to Skip
- Simple changes (typo fix, add log line)
- You know exactly what needs to change
- Single-file modifications

## Visual Planning (For UI/Complex Flows)

**Problem:** Describing visual things through text is exhausting.

**Solution:** Plan visually before writing code.

### When to Use Visual Planning

- UI-heavy features (screens, components, layouts)
- Complex user flows (multi-step wizards, state machines)
- Architecture that benefits from diagrams

### The Visual Planning Workflow

```
Phase 1: Write plan document
- Requirements
- User stories
- Success criteria

Phase 2: Generate visual diagrams
"Create Flowy visualizations for this feature:
 1. Navigation flow (how users get there)
 2. State machine (internal logic)
 3. UI mockups (all screens)"

Phase 3: Iterate visually
- Edit diagrams in browser
- Drag nodes, adjust layouts
- Claude sees JSON changes

Phase 4: Implement from spec
- Follow visual spec exactly
- One screen at a time
- Compare against mockups
```

### Why This Works

Implementation becomes almost mechanical when:
- Every screen is designed
- Every state transition is mapped
- Every edge case is visible

### Tools

- **Flowy** — JSON diagrams Claude can read/write
- **Pencil.dev** — Design on canvas → code
- **Figma + MCP** — Connect Figma to Claude

See `resources.md` → Visual & Design Tools for setup.

## Conversation Scoping

**One conversation = One task**

```
✅ Conversation 1: "Add user login"
✅ Conversation 2: "Add password reset"
✅ Conversation 3: "Fix login redirect bug"

❌ One conversation: "Add login, then password reset, 
   oh also fix that bug from yesterday, and maybe 
   refactor the user model while we're at it..."
```

Why? Context gets cluttered. Claude gets confused. Quality drops.

## The Implementation Loop

```
1. Describe what you want
2. Claude implements
3. Verify it works (tests, manual check)
4. If wrong → give specific feedback → goto 2
5. If right → commit
```

## Commit Often

```
✅ Small, focused commits:
   - "feat: add login form UI"
   - "feat: add login API endpoint"
   - "feat: connect form to API"
   - "fix: handle invalid credentials"

❌ Giant commits:
   - "add authentication system"
```

Small commits = easy to revert if something breaks.

## TDD: Your Safety Net

**In vibe coding, TDD is not optional — it's essential.**

Why? AI generates code faster than you can review. Without tests, you have no idea if it actually works. Tests are your verification layer.

### The RED-GREEN-REFACTOR Cycle

```
1. RED    — Write a failing test first
2. GREEN  — Write minimal code to pass
3. REFACTOR — Clean up, tests still pass
```

### Why This Order Matters

```
❌ Code first, test later
   → You don't know if it works
   → Tests become an afterthought
   → Bugs ship to production

✅ Test first, code second
   → You define "done" upfront
   → Claude has a clear target
   → You know immediately if it works
```

### TDD with Claude

```
Step 1: Write the test
"Write a test for a function that calculates total price 
 with 10% discount for orders over $100"

Step 2: Run it (should fail)
$ pnpm test
FAIL: calculateTotal is not defined

Step 3: Implement
"Now implement calculateTotal to pass this test"

Step 4: Run it (should pass)
$ pnpm test
PASS

Step 5: Refactor if needed
"Clean up the implementation, keep tests passing"
```

### Test Structure (Arrange-Act-Assert)

```typescript
describe('calculateTotal', () => {
  it('should apply 10% discount for orders over $100', () => {
    // Arrange
    const items = [{ price: 60 }, { price: 50 }]; // $110 total
    
    // Act
    const result = calculateTotal(items);
    
    // Assert
    expect(result).toBe(99); // 110 - 10% = 99
  });

  it('should not apply discount for orders under $100', () => {
    // Arrange
    const items = [{ price: 30 }, { price: 40 }]; // $70 total
    
    // Act
    const result = calculateTotal(items);
    
    // Assert
    expect(result).toBe(70); // No discount
  });
});
```

### TDD Rules

1. **Never write production code without a failing test**
2. **One assertion per test** (when possible)
3. **Descriptive test names:** `should_apply_discount_when_total_exceeds_100`
4. **Run tests after every change**
5. **Target 80%+ coverage** for critical paths

### When Claude Skips Tests

Claude sometimes jumps straight to implementation. Push back:

```
❌ Claude: "Here's the implementation..."
✅ You: "Wait — write a failing test first, then implement"
```

Or be explicit upfront:

```
"Using TDD: First write a failing test for [feature], 
 then implement just enough to pass it."
```

### The Verification Loop

```
You: "Add user registration"
Claude: [writes test]
You: "Run it"
Claude: [test fails - expected]
Claude: [implements]
You: "Run it again"
Claude: [test passes]
You: "Good, commit"
```

This loop is your safety net. Don't skip it.

---

# 4. CONTEXT MANAGEMENT

## The 200K Limit

Claude has ~200,000 tokens of context. Sounds like a lot, but:
- CLAUDE.md loads every time
- File contents add up fast
- Quality degrades around 20-40% usage

## The 3-Strike Rule

```
Corrected Claude 3+ times on the same issue?
    ↓
Context is polluted with failed attempts
    ↓
/clear and start fresh with a better prompt
```

A clean start with a good prompt beats a long conversation with accumulated confusion.

## Use Subagents for Research

When Claude needs to read many files, those files fill YOUR context.

**Instead:**
```
Use a subagent to investigate how our auth system works
and summarize the key patterns.
```

The subagent reads files in ITS context, returns only a summary to yours.

### Good Subagent Tasks
- "Explore how X works in our codebase"
- "Review this code for security issues"
- "Analyze all test files and summarize patterns"
- "Find all usages of function Y"

## Session Management

```bash
# Name your session before switching tasks
/rename auth-feature

# Clear context for new task
/clear

# Come back later
claude --resume   # pick from list
claude --continue # most recent
```

## When to /clear

- Starting unrelated task
- Context feels "polluted"
- Claude keeps making same mistake
- You've been going in circles

## When to /compact

- Long conversation but still on same task
- Want to preserve some context
- Custom: `/compact Focus on the API changes, drop the debugging attempts`

---

# 5. DEBUGGING

## The Debugging Workflow

```
1. Reproduce the issue
   > "When I click X, Y happens instead of Z"

2. Locate the problem
   > "Add logging to find where it goes wrong"

3. Understand root cause
   > "Why is this variable undefined here?"

4. Write a failing test (if possible)
   > "Write a test that captures this bug"

5. Fix it
   > "Fix the issue so the test passes"

6. Verify
   > "Run all tests, make sure nothing else broke"

7. Clean up
   > "Remove the debug logging"
```

## Effective Bug Reports to Claude

```
## What I Expected
Clicking "Save" should save the form and show "Saved!" message

## What Actually Happens
Nothing happens. No error in console. Network tab shows no request.

## What I've Tried
- Checked onClick handler exists (it does)
- Added console.log in handler (doesn't fire)

## Relevant Files
- src/components/SaveButton.tsx
- src/hooks/useSaveForm.ts
```

## When Claude's Fix Doesn't Work

```
Your fix didn't work. Here's what happened:

[Paste exact error or describe behavior]

The issue seems to be [your hypothesis if any].
```

Don't just say "it doesn't work" — be specific.

---

# 6. COMMON MISTAKES

## Mistake: Vague Prompts

```
❌ "Make it better"
❌ "Fix the bugs"
❌ "Add some tests"

✅ "Improve the loading state UX — add a spinner and disable the button"
✅ "Fix the null pointer error on line 45 of UserService.ts"
✅ "Add tests for the validateEmail function covering empty, invalid, and valid inputs"
```

## Mistake: Too Many Things at Once

```
❌ "Add login, signup, password reset, and email verification"

✅ "Add login with email/password"
(then next conversation)
✅ "Add signup form"
(then next conversation)
✅ "Add password reset"
```

## Mistake: Not Reviewing Generated Code

Just because it runs doesn't mean it's good.

**Always check for:**
- Hardcoded values that should be configurable
- Missing error handling
- Security issues (SQL injection, XSS)
- Console.logs left in
- Commented-out code

## Mistake: Fighting Claude

If Claude keeps doing something wrong after 3 attempts:
- Your prompt might be unclear
- The approach might be wrong
- Context might be polluted

**Don't keep trying the same thing.** Step back and reconsider.

## Mistake: Skipping Verification

```
❌ "Looks good" → commit → push → production → bug

✅ "Looks good" → run tests → manual check → commit
```

## Mistake: Scope Creep

```
You: "Add a save button"
Claude: "I've added a save button with auto-save, 
        undo/redo, conflict resolution, and cloud sync"
You: "Cool!" → 3 days debugging cloud sync

Should've been:
You: "Just a simple save button. No auto-save, no cloud. 
     Click → save to localStorage → show 'Saved!' message."
```

---

# 7. WHEN STUCK

## The Checklist

```
□ Is my prompt specific enough?
□ Did I provide enough context?
□ Am I trying to do too many things at once?
□ Should I /clear and start fresh?
□ Is there a simpler approach?
□ Do I need to break this into smaller steps?
```

## Strategies

### 1. Simplify
```
Instead of: "Build the full feature"
Try: "Build the smallest possible version that does one thing"
```

### 2. Show Don't Tell
```
Instead of: "Handle errors properly"
Try: "Here's the pattern I want:
      try { ... } catch (e) { toast.error(e.message); }"
```

### 3. Reframe
```
Instead of: "Manage state transitions"
Try: "Think of it as a state machine with states: idle, loading, success, error"
```

### 4. Step Back
```
Instead of: Continuing to fight the current approach
Try: "Let's forget what we've tried. What's the simplest way to [goal]?"
```

### 5. Use Resources
```
Check resources.md for tools that might help:
- Need better UI? → ui-ux-pro-max-skill
- Security review? → security-reviewer agent
- Database help? → supabase/agent-skills
```

## The Nuclear Option

When nothing works:

```
1. /clear
2. Copy the essential code/context
3. Start a new conversation
4. Paste only what's needed
5. Explain the problem fresh, with what you've learned
```

Sometimes a clean slate is faster than untangling a mess.

---

# 8. MODEL SELECTION

## When to Use Sonnet (Fast, Cheap)

- Clear, well-defined tasks
- Boilerplate code generation
- Refactoring with a clear plan
- Simple bug fixes
- Documentation updates
- Following established patterns

## When to Use Opus (Slow, Powerful)

- Architecture decisions
- Complex tradeoff analysis
- Difficult debugging (root cause unclear)
- System design
- Code review requiring deep analysis
- Novel problem-solving

## Switching Workflow

```
1. Start with Opus for planning/architecture
   "Design the auth system architecture"

2. Shift+Tab → Switch to Sonnet for implementation
   "Implement the login endpoint per the plan"

3. Shift+Tab → Back to Opus if stuck
   "This error doesn't make sense, help me debug"
```

## Cost-Aware Pattern

```
Opus:  "Plan how to add user settings feature"
       → Get architecture, file list, approach

Sonnet: "Implement step 1: create Settings model"
Sonnet: "Implement step 2: add settings API routes"
Sonnet: "Implement step 3: create settings UI"

Opus:  "Review the implementation for issues"
```

---

# 9. MCP-FIRST PRINCIPLE

## The Problem Without MCP

When Claude Code needs external services, it often:
- Reads documentation pages → Outdated, web scraping fragile
- Uses CLI tools manually → Auth issues, output parsing errors
- Makes raw API calls → Token management, error handling

**All of these are error-prone.**

## The Rule

```
Need external service?
    → Check if MCP exists
        → YES: Use MCP (structured, authenticated, reliable)
        → NO: Fall back to CLI/API (last resort)
```

## Why MCP is Better

| Approach | Problems |
|----------|----------|
| CLI tools | Auth issues, parsing errors, rate limits |
| Reading docs | Outdated, scraping fragile |
| Raw API calls | Token management, error handling |
| **MCP** | ✅ Structured API, auto-auth, type-safe |

## Common Services with MCPs

| Service | MCP Package |
|---------|-------------|
| GitHub | `@modelcontextprotocol/server-github` |
| PostgreSQL | `@modelcontextprotocol/server-postgres` |
| Slack | `@modelcontextprotocol/server-slack` |
| Google Drive | `@modelcontextprotocol/server-gdrive` |
| Supabase | `@supabase/mcp` |
| Filesystem | `@modelcontextprotocol/server-filesystem` |

## MCP Discovery

1. **Official:** https://github.com/modelcontextprotocol/servers
2. **Community:** https://github.com/punkpeye/awesome-mcp-servers
3. **Search:** "mcp server [service-name]"

## Adding MCP

```bash
claude mcp add github -- npx -y @modelcontextprotocol/server-github
```

## Context Warning

- Keep **<10 MCPs** enabled per project
- Each MCP consumes context even when idle
- Disable unused ones in settings

---

# 10. CLAUDE.md AUTHORING

## The 6 Core Areas

Every CLAUDE.md needs these sections:

```markdown
# Project: [Name]

## Overview
[One-line description]

## Tech Stack
- Language: TypeScript 5.x
- Framework: Next.js 14
- Database: PostgreSQL + Prisma
- Testing: Jest + RTL

## Commands
- `pnpm dev` — Development server
- `pnpm build` — Production build
- `pnpm test` — Run tests
- `pnpm lint` — Lint check
- `pnpm typecheck` — Type check

## Code Style
[Conventions with examples]

## Boundaries
### ✅ Always
### ⚠️ Ask first
### 🚫 Never

## Project Notes
[Unique context, gotchas]
```

## Key Principles

### Keep It Short
- **Target:** ~150-200 instructions max
- **Hard limit:** 500 lines
- Claude's adherence drops with more instructions

### Include WHY
```markdown
# Bad
- Use pnpm

# Good
- Use pnpm (not npm) — workspace hoisting configured for it
```

### Use Code Examples
```markdown
# Bad
- Use early returns

# Good
- Use early returns:
  ```typescript
  // ✅ Good
  if (!user) return null;
  return user.name;
  
  // ❌ Avoid
  if (user) {
    return user.name;
  } else {
    return null;
  }
  ```
```

### Be Specific in Boundaries

```markdown
## Boundaries

### ✅ Always
- Run `pnpm test` before committing
- Use TypeScript strict mode
- Handle errors explicitly

### ⚠️ Ask first
- Database schema changes
- New dependencies
- Changes to auth logic

### 🚫 Never
- Commit secrets or API keys
- Disable TypeScript strict checks
- Delete tests without fixing underlying issue
```

## Common Mistake: Too Many Rules

More rules → worse adherence to each.

**Solution:** 
- Keep CLAUDE.md lean
- Move detailed workflows to Skills
- Move specialized tasks to Agents

---

# 11. SKILLS & AGENTS

## When to Create a Skill

Create a skill when you have a **repeatable workflow**:

- TDD process → `tdd-workflow` skill
- Code review checklist → `code-review` skill
- Deployment process → `deploy` skill
- Bug fix workflow → `debug` skill

## Skill Structure

```markdown
<!-- .claude/skills/[name]/SKILL.md -->
---
name: skill-name
description: When to use (Claude matches against this)
---

# Skill Title

## When to Use
[Trigger conditions]

## Process
[Step-by-step]

## Examples
[Code examples]
```

**Key:** The `description` is the trigger — be specific about when to apply.

## When to Create an Agent

Create an agent when you need a **specialized role**:

- Security review → `security-reviewer` agent
- Code quality → `code-reviewer` agent
- Documentation → `docs-agent` agent

## Agent Structure

```markdown
<!-- .claude/agents/[name].md -->
---
name: agent-name
description: When to invoke
tools: Read, Grep, Glob, Edit, Bash
---

You are a [role] for this project.

## Your Task
[What this agent does]

## Checklist
[What to check/do]

## Output Format
[How to report findings]

## Boundaries
- ✅ Always: [required behaviors]
- 🚫 Never: [forbidden actions]
```

## Skill vs Agent vs CLAUDE.md

| Type | Purpose | Scope |
|------|---------|-------|
| CLAUDE.md | Project settings | Always loaded |
| Skill | Workflow/process | Loaded when matched |
| Agent | Specialized role | Separate context |

## Built-in Subagents

Claude Code has built-in subagents you can use:

- **Explore** — Read-only codebase analysis
- **Plan** — Context gathering for planning

```
"Use a subagent to explore how auth works in this codebase"
"Use the plan subagent to analyze what files need to change"
```

---

# 12. HOOKS SYSTEM

## What Hooks Are

Code that runs automatically before/after Claude's actions.

## Common Use Cases

### Auto-format on Edit
```json
{
  "hooks": {
    "postToolUse": [{
      "matcher": "tool == 'Edit' && file_path matches '\\.(ts|tsx)$'",
      "command": "npx prettier --write \"$file_path\""
    }]
  }
}
```

### Lint Warning
```json
{
  "matcher": "tool == 'Edit' && file_path matches '\\.(ts|tsx)$'",
  "command": "npx eslint \"$file_path\" --max-warnings 0"
}
```

### Console.log Warning
```json
{
  "matcher": "tool == 'Edit' && file_path matches '\\.(ts|tsx|js|jsx)$'",
  "command": "grep -n 'console.log' \"$file_path\" && echo '[Warning] Remove console.log before commit' || true"
}
```

## Hook Location

Add to `~/.claude/settings.json` or project `.claude/settings.json`

## When to Use Hooks

- **Auto-formatting** — Prettier, Black
- **Lint checks** — ESLint, Ruff
- **Type checking** — After TypeScript edits
- **Test running** — After test file edits
- **Custom warnings** — Project-specific rules

---

# 13. PROJECT TYPE PATTERNS

## Frontend Projects

### CLAUDE.md Should Include
- Component structure (atomic, feature-based, etc.)
- Styling approach (Tailwind, CSS Modules, etc.)
- State management (React Query, Zustand, etc.)
- Routing conventions

### Recommended Skills
- `ui-ux-pro-max-skill` — Design system generation
- `vercel-labs/agent-skills` — React best practices

### Common Boundaries
```markdown
### ✅ Always
- Use semantic HTML
- Ensure keyboard navigation works
- Add loading states for async operations

### 🚫 Never
- Inline styles (use Tailwind)
- Direct DOM manipulation in React
- Skip accessibility attributes
```

## Backend Projects

### CLAUDE.md Should Include
- API design style (REST conventions)
- Error handling patterns (error codes, formats)
- Auth approach (JWT, sessions)
- Database access patterns

### Recommended Skills
- `supabase/agent-skills` — PostgreSQL best practices
- `security-review` skill

### Common Boundaries
```markdown
### ✅ Always
- Validate all inputs
- Use parameterized queries
- Log errors with context

### 🚫 Never
- Expose internal errors to clients
- Store secrets in code
- Skip auth middleware
```

## Fullstack Projects

### CLAUDE.md Should Include
- Code separation rules (what goes where)
- Shared types location
- API contract management
- Build/deploy awareness

### Recommended Setup
```
project/
├── apps/
│   ├── web/        # Frontend
│   └── api/        # Backend
├── packages/
│   └── shared/     # Shared types
└── CLAUDE.md       # Root config
```

### Common Boundaries
```markdown
### ✅ Always
- Keep API types in sync with frontend
- Run both frontend and backend tests

### ⚠️ Ask first
- Changes to shared types
- New API endpoints
```

## Library Projects

### CLAUDE.md Should Include
- Public API design principles
- Semver versioning strategy
- Documentation requirements
- Bundle size considerations

### Common Boundaries
```markdown
### ✅ Always
- Document all public APIs
- Include usage examples
- Maintain backward compatibility

### ⚠️ Ask first
- Breaking changes
- New dependencies

### 🚫 Never
- Expose internal implementation details
- Skip tests for public API
```

---

# QUICK REFERENCE

## Commands
| Command | What it does |
|---------|--------------|
| `Shift+Tab` | Switch model (Opus ↔ Sonnet) |
| `Shift+Tab x2` | Enter Plan Mode |
| `/clear` | Clear context |
| `/compact` | Compress context |
| `/rewind` | Restore to checkpoint |
| `Esc` | Stop Claude |
| `Esc + Esc` | Rewind menu |

## Rules of Thumb
- **Specific > Vague**
- **Small > Big** (commits, features, conversations)
- **Verify > Assume**
- **Fresh start > Long struggle**
- **MVP > Perfect**

## The Mantra

```
Describe what you want clearly.
Review what you get carefully.
Verify that it works actually.
Commit when it's done properly.
```

---

# Remember

Vibe coding is powerful, but it's not magic. You're still the developer. 
Claude is your very fast, very capable assistant — but you're the one 
who knows what you're building and why.

When in doubt: slow down, think, and be specific.

Happy vibing! 🎸