# Claude Code Project Initialization Protocol

> **사용법:** 새 프로젝트 시작 시 Claude Code에서:
> "initialize.md, initialize_prompt.md, resources.md를 읽고 프로젝트 초기 셋업 진행해줘"

---

# PHASE 0: PRE-CHECK

## 0.1 Already Initialized?

```
□ CLAUDE.md exists with these sections:
  □ Tech Stack
  □ Commands
  □ Verification
  □ Code Style (with examples)
  □ Boundaries
□ .claude/ directory exists with rules/, plans/, skills/
□ .gitignore exists
□ README.md exists
```

- **ALL PASS** → "Project already initialized. What would you like to work on?" → STOP
- **ANY FAIL** → Continue to Phase 0.2

## 0.2 Read Project Requirements

```
1. Read initialize_prompt.md for project specification
2. Extract:
   - Project name
   - Tech stack
   - Features/requirements
   - External services (Supabase, Slack, etc.)
   - Special constraints
```

**If initialize_prompt.md doesn't exist:** Ask 1-2 questions at a time:

| Category | Questions |
|----------|-----------|
| **Type** | Web app / API / CLI / Library / Mobile? |
| **Stack** | Language + Framework + versions? |
| **Database** | PostgreSQL / MongoDB / Supabase / None? |
| **Testing** | Jest / Vitest / Pytest / Other? (TDD is default) |
| **Package manager** | npm / pnpm / yarn / bun? |
| **New or existing?** | Fresh start or existing codebase? |
| **External services** | GitHub, Slack, Vercel, Supabase, etc.? |

## 0.3 If Existing Codebase → Explore First

Before generating files, use **Plan Mode** to explore:

```
Phase 1: Enter Plan Mode (Shift+Tab x2)
Claude can only read, not modify.

Phase 2: Explore
> read package.json, tsconfig.json, and any existing README
> explore src/ structure and identify the architecture pattern
> check for existing tests and their conventions
> look at how errors are handled, how auth works, etc.

Phase 3: Summarize
> summarize what you learned about this codebase

Phase 4: Exit Plan Mode, proceed to Phase 1
```

## 0.4 Environment Check

```bash
# Check Claude Code version
claude --version

# Check Auto Memory status
echo $CLAUDE_CODE_DISABLE_AUTO_MEMORY
# If not 0, recommend: export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0

# Check installed plugins
claude plugin list
# If missing core plugins, warn user to run setup script
```

---

# PHASE 1: PLUGIN INSTALLATION

## 1.1 Install Core Plugins

```bash
# Essential plugins (run all)
claude plugin install superpowers@claude-plugins-official
claude plugin install frontend-design@claude-plugins-official
claude plugin install code-review@claude-plugins-official
claude plugin install firecrawl@claude-plugins-official
claude plugin install ralph-loop@claude-plugins-official
```

## 1.2 Install Project-Specific Plugins (based on initialize_prompt.md)

| Project Type | Additional Plugins |
|--------------|-------------------|
| **Web/Frontend** | `claude plugin install context7@claude-plugins-official` |
| **Full-stack** | Above + TypeScript LSP if TS |
| **Mobile (React Native)** | `npx skills add expo/skills` |

## 1.3 Verify Installation

```bash
claude plugin list
# Should show: superpowers, frontend-design, code-review, firecrawl, ralph-loop
```

---

# PHASE 2: PROJECT STRUCTURE

## 2.1 Create Directory Structure

```bash
mkdir -p .claude/rules
mkdir -p .claude/plans/web
mkdir -p .claude/plans/api
mkdir -p .claude/plans/infra
mkdir -p .claude/scratchpad
mkdir -p .claude/skills
mkdir -p .claude/agents
mkdir -p docs/notes
```

## 2.2 Create CLAUDE.md

**Use template from [TEMPLATE-1] below.** Customize based on initialize_prompt.md.

## 2.3 Create Rules Files

Based on project type, create in `.claude/rules/`:

| File | When to Create |
|------|---------------|
| `code-style.md` | Always |
| `testing.md` | If TDD enabled (default: yes) |
| `security.md` | If auth/payments involved |
| `api-design.md` | If backend/API |
| `ui-patterns.md` | If frontend |

## 2.4 Create Plan Files

Create `.claude/plans/[area]/PLAN.md` for each major area:

```markdown
# [Area] Plan

## Current Status
- [ ] Not started

## Goals
(From initialize_prompt.md)

## Tasks
1. ...

## Decisions Made
(To be filled during development)

## Open Questions
(To be filled during development)
```

## 2.5 Create Scratchpad Files

```bash
touch .claude/scratchpad/web.md
touch .claude/scratchpad/api.md
touch .claude/scratchpad/global.md
touch SCRATCHPAD.md  # Root level for quick notes
```

---

# PHASE 3: MCP CONFIGURATION

## 3.1 Install MCPs (based on initialize_prompt.md)

| Service | Command |
|---------|---------|
| **GitHub** | `claude mcp add github -- npx -y @modelcontextprotocol/server-github` |
| **Supabase** | `claude mcp add supabase -- npx -y @supabase/mcp` |
| **PostgreSQL** | `claude mcp add postgres -- npx -y @modelcontextprotocol/server-postgres` |
| **Slack** | `claude mcp add slack -- npx -y @modelcontextprotocol/server-slack` |
| **Notion** | `claude mcp add notion -- npx -y @notionhq/mcp` |
| **Linear** | `claude mcp add linear -- npx -y @linear/mcp` |

**Rule: Keep < 10 MCPs**

## 3.2 Create MCP Skills

For each MCP installed, create a skill in `.claude/skills/mcp-[name]/SKILL.md`:

**Template: [TEMPLATE-3]**

## 3.3 Verify MCPs

```bash
claude mcp list
```

---

# PHASE 4: SKILLS INSTALLATION

## 4.1 Install External Skills (based on project type)

| Project Type | Skills |
|--------------|--------|
| **Frontend/UI** | `npx skills add nextlevelbuilder/ui-ux-pro-max-skill` |
| | `npx skills add vercel-labs/agent-skills` |
| **Supabase/PostgreSQL** | `npx skills add supabase/agent-skills` |
| **Fullstack** | All above + `npx skills add obra/superpowers` |
| **React Native** | `npx skills add expo/skills` |

## 4.2 Create Project-Specific Skills

If project has repeatable workflows, create skills using [TEMPLATE-4].

---

# PHASE 5: AGENTS SETUP

## 5.1 Create Core Agents

Always create these in `.claude/agents/`:

| Agent | Template |
|-------|----------|
| `security-reviewer.md` | [TEMPLATE-5a] |
| `code-reviewer.md` | [TEMPLATE-5b] |
| `plan-reviewer.md` | [TEMPLATE-5c] |

---

# PHASE 6: AUTO MEMORY SETUP

## 6.1 Enable Auto Memory

```bash
# Add to shell profile (~/.zshrc or ~/.bashrc)
export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0
```

## 6.2 Initialize Memory Directory

```bash
# This happens automatically, but verify:
ls ~/.claude/projects/
```

## 6.3 Seed Initial Memory (Optional)

Create `~/.claude/projects/<project>/memory/MEMORY.md`:

```markdown
# Project Memory

## Key Decisions
(Auto-populated by Claude)

## Patterns Learned
(Auto-populated by Claude)

## Common Issues
(Auto-populated by Claude)
```

---

# PHASE 7: VERIFICATION

## 7.1 Checklist

```
□ CLAUDE.md exists with all sections
□ .claude/rules/ has relevant rule files
□ .claude/plans/ has area-specific plans
□ .claude/skills/ has MCP skills for each MCP
□ .claude/agents/ has core agents
□ Plugins installed (superpowers, frontend-design, code-review, firecrawl, ralph-loop)
□ MCPs configured for external services
□ Auto Memory enabled
□ .gitignore updated (if needed)
□ README.md exists
```

## 7.2 Test Commands

```bash
# Test plugin
/superpowers

# Test MCP (if installed)
# Ask: "Test supabase connection"

# Check context
/context
```

## 7.3 Final Output

Say: **"Initialization complete. Project structure created with [X] plugins, [Y] MCPs, [Z] skills."**

Then provide a summary of what was set up.

---

# TEMPLATES

## TEMPLATE-1: CLAUDE.md

```markdown
# Project: [NAME from initialize_prompt.md]

## Tech Stack
- Language: [e.g., TypeScript 5.x]
- Framework: [e.g., Next.js 15]
- Database: [e.g., Supabase/PostgreSQL]
- Testing: [e.g., Vitest]
- Package Manager: [e.g., pnpm]

## Commands
- `pnpm dev` — Start dev server
- `pnpm build` — Production build
- `pnpm test` — Run tests
- `pnpm lint` — Lint check
- `pnpm typecheck` — Type check

## Verification
**Run after every change:**
1. `pnpm typecheck`
2. `pnpm lint`
3. `pnpm test`

## TDD Workflow
1. **RED** — Write failing test first
2. **GREEN** — Write minimal code to pass
3. **REFACTOR** — Clean up, tests still pass

## Context Management

### Plans & Scratchpads
| Area | Plan | Scratchpad |
|------|------|------------|
| Web/Frontend | `.claude/plans/web/PLAN.md` | `.claude/scratchpad/web.md` |
| API/Backend | `.claude/plans/api/PLAN.md` | `.claude/scratchpad/api.md` |
| Infrastructure | `.claude/plans/infra/PLAN.md` | `.claude/scratchpad/global.md` |

**Rule:** When working on [area], load the corresponding plan file first.

### Accumulated Knowledge
- `docs/notes/` — Update after every significant decision or PR
- Auto Memory — Claude automatically saves patterns to `~/.claude/projects/`

## MCP Skills
[List MCPs installed and their skills]
- Supabase → `.claude/skills/mcp-supabase/SKILL.md`
- GitHub → `.claude/skills/mcp-github/SKILL.md`

**Rule:** Before using MCP, read its skill file for workflow patterns.

## Code Style
[From .claude/rules/code-style.md]

### Naming
- Variables/functions: camelCase
- Components/classes: PascalCase
- Constants: UPPER_SNAKE_CASE

### Example
```typescript
// ✅ Good
const fetchUser = async (id: string): Promise<User> => {
  const res = await api.get(`/users/${id}`);
  return res.data;
};

// ❌ Bad
async function fetchUser(id) {
  return await api.get(`/users/${id}`);
}
```

## Boundaries

### ✅ Always
- Write failing test before implementing
- Run verification before commits
- Update docs/notes/ after significant changes
- Check MCP skill before using MCP

### ⚠️ Ask First
- Database schema changes
- New dependencies
- API contract changes
- Auth logic changes

### 🚫 Never
- Commit secrets/API keys
- Edit generated files
- Delete tests without fixing
- Skip type checking

## Performance Guidelines
- Keep MCP servers < 10, active tools < 80
- Run `/context` when performance degrades
- Use `!command` for quick status checks (saves tokens)
- Consider `/compact` or HANDOFF.md when context > 50%

## Plugins Installed
- superpowers — TDD, debugging, planning
- frontend-design — UI patterns
- code-review — Code quality
- firecrawl — Web scraping
- ralph-loop — Autonomous loops

## Project Notes
[Specific context from initialize_prompt.md]
```

---

## TEMPLATE-2: Rule File (.claude/rules/code-style.md)

```markdown
# Code Style Rules

## TypeScript
- Strict mode enabled
- No `any` type
- Explicit return types for functions
- Prefer `const` over `let`

## React (if applicable)
- Functional components only
- Custom hooks start with `use`
- Props interfaces named `[Component]Props`

## Naming
- Files: kebab-case
- Components: PascalCase
- Hooks: camelCase with `use` prefix
- Constants: UPPER_SNAKE_CASE

## Imports
- Group: external → internal → relative
- No default exports (except pages/components)
```

---

## TEMPLATE-3: MCP Skill (.claude/skills/mcp-[name]/SKILL.md)

```markdown
---
name: mcp-[service]
description: [Service] operations via MCP. Use when user mentions "[keywords]".
---

# [Service] MCP Workflow

## Connection Check
Before operations, verify MCP is connected:
- Check `claude mcp list`
- If not connected, run: `claude mcp add [service] -- [command]`

## Common Operations

### [Operation 1]
```
[Example code or command]
```

### [Operation 2]
```
[Example code or command]
```

## Best Practices
- [Practice 1]
- [Practice 2]

## Error Handling
| Error | Cause | Solution |
|-------|-------|----------|
| Connection refused | Service not running | [How to start] |
| Permission denied | Auth issue | [How to fix] |

## Do NOT
- [Anti-pattern 1]
- [Anti-pattern 2]
```

---

## TEMPLATE-4: Custom Skill (.claude/skills/[name]/SKILL.md)

```markdown
---
name: [skill-name]
description: [What it does]. Use when user mentions "[trigger phrases]". Do NOT use for [negative triggers].
---

# [Skill Name]

## When to Use
- [Trigger condition 1]
- [Trigger condition 2]

## Process
1. [Step 1]
2. [Step 2]
3. [Step 3]

## Examples

### Example 1: [Scenario]
User says: "[example prompt]"
Actions:
1. [Action 1]
2. [Action 2]
Result: [Expected outcome]

## Troubleshooting
| Issue | Solution |
|-------|----------|
| [Issue 1] | [Solution 1] |
```

---

## TEMPLATE-5a: Security Reviewer Agent

```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities
tools: Read, Grep, Glob
---

You are a security engineer reviewing code for vulnerabilities.

## Checklist
- [ ] Input validation on all user inputs
- [ ] No SQL/command injection
- [ ] No hardcoded secrets
- [ ] Auth checks present
- [ ] HTTPS enforced
- [ ] CORS configured properly
- [ ] Rate limiting in place

## Output Format
```
[SEVERITY] file:line
Issue: [description]
→ Fix: [recommendation]
```

Severity: CRITICAL > HIGH > MEDIUM > LOW

## Boundaries
- ✅ Read any file
- 🚫 Never modify code (report only)
```

---

## TEMPLATE-5b: Code Reviewer Agent

```markdown
---
name: code-reviewer
description: Reviews code for quality and best practices
tools: Read, Grep, Glob
---

You are a senior engineer reviewing code quality.

## Checklist
- [ ] DRY violations
- [ ] Error handling present
- [ ] Edge cases covered
- [ ] Tests exist
- [ ] No N+1 queries
- [ ] Appropriate abstractions

## Output Format
```
[SEVERITY] file:line
Issue: [description]
→ Suggestion: [recommendation]
```

## Boundaries
- ✅ Read any file
- 🚫 Never modify code (report only)
```

---

## TEMPLATE-5c: Plan Reviewer Agent

```markdown
---
name: plan-reviewer
description: Reviews implementation plans before execution
tools: Read, Grep, Glob
---

You are a staff engineer reviewing implementation plans.

## Engineering Preferences
- DRY is important — flag repetition aggressively
- Well-tested code is non-negotiable
- "Engineered enough" — not fragile, not over-engineered
- More edge cases > fewer; thoughtfulness > speed
- Explicit over clever

## Review Sections

### 1. Architecture Review
- System design and component boundaries
- Dependency graph and coupling
- Data flow and bottlenecks
- Scaling and single points of failure
- Security architecture

### 2. Code Quality Review
- Organization and structure
- DRY violations
- Error handling gaps
- Technical debt

### 3. Test Review
- Coverage gaps
- Test quality
- Missing edge cases

### 4. Performance Review
- N+1 queries
- Memory concerns
- Caching opportunities

## For Each Issue
1. Describe with file/line references
2. Present 2-3 options (including "do nothing")
3. Recommend with reasoning
4. **Ask before proceeding**

## Workflow
**BEFORE STARTING:** Ask:
- A) BIG CHANGE: Section by section, max 4 issues each
- B) SMALL CHANGE: One question per section

## Boundaries
- ✅ Read any file
- 🚫 Never modify without approval
```

---

## TEMPLATE-5d: Docs Agent

```markdown
---
name: docs-agent
description: Generates documentation from code
tools: Read, Grep, Glob, Edit
---

You are a technical writer.

## Task
- Read code in `src/`
- Generate/update docs in `docs/`
- Include code examples
- Keep API docs in sync with code

## Boundaries
- ✅ Write to `docs/` only
- 🚫 Never modify source code
```

---

## TEMPLATE-5e: Test Agent

```markdown
---
name: test-agent
description: Writes comprehensive tests
tools: Read, Grep, Glob, Edit, Bash
---

You are a QA engineer.

## Task
- Write tests for uncovered code
- Follow existing test patterns
- Aim for 80%+ coverage
- Cover edge cases and error conditions

## Test Pattern
```typescript
describe('Component/Function', () => {
  it('should [expected behavior]', () => {
    // Arrange
    // Act
    // Assert
  });
});
```

## Boundaries
- ✅ Write to test files only
- ✅ Run tests after writing
- 🚫 Never modify source code
```

---

## TEMPLATE-5f: Refactor Agent

```markdown
---
name: refactor-agent
description: Refactors code without changing behavior
tools: Read, Grep, Glob, Edit, Bash
---

You are a refactoring specialist.

## Targets
- Functions > 50 lines → break up
- Duplicated code → extract to utilities
- Complex conditionals → simplify
- Dead code → remove
- Magic numbers → constants

## Rules
- Tests must pass before AND after
- One refactoring type per commit
- Preserve all existing functionality

## Boundaries
- ✅ Run tests after every change
- ⚠️ Ask before removing code
- 🚫 Never change functionality
```

---

## TEMPLATE-5g: API Agent

```markdown
---
name: api-agent
description: Creates and maintains API endpoints
tools: Read, Grep, Glob, Edit, Bash
---

You are an API developer.

## Standards
- RESTful conventions
- Validate all inputs
- Consistent error format
- Document with JSDoc/OpenAPI

## Error Format
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Resource not found"
  }
}
```

## Boundaries
- ✅ Validate inputs, handle errors, write tests
- ⚠️ Ask before schema changes
- 🚫 Never expose sensitive data
```

---

# QUICK REFERENCE

## Commands
| Command | Action |
|---------|--------|
| `/context` | Show context usage |
| `/memory` | Open memory files |
| `/compact` | Compress context |
| `/ralph-loop` | Start autonomous loop |
| `/cancel-ralph` | Stop autonomous loop |
| `!command` | Execute without Claude processing |

## Plugin Commands
| Plugin | Commands |
|--------|----------|
| superpowers | `/brainstorm`, `/debug`, `/tdd` |
| code-review | `/review` |
| ralph-loop | `/ralph-loop`, `/cancel-ralph` |

## Files to Update Regularly
| File | When |
|------|------|
| `docs/notes/*.md` | After significant decisions |
| `.claude/plans/*/PLAN.md` | When starting new area |
| `.claude/scratchpad/*.md` | During active development |
| `CLAUDE.md` | When adding new patterns/rules |