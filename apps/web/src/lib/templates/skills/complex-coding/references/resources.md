# Claude Code Resources Reference

> **What this is:** Permanent reference for tools, plugins, skills, MCPs, and patterns.
> Consult during initialization and ongoing development.

---

# TABLE OF CONTENTS

1. [Plugins](#1-plugins)
2. [Auto Memory](#2-auto-memory)
3. [External Skills](#3-external-skills)
4. [MCP Servers](#4-mcp-servers)
5. [MCP Skills (Templates)](#5-mcp-skills-templates)
6. [Agent Templates](#6-agent-templates)
7. [Skill Templates](#7-skill-templates)
8. [Visual & Design Tools](#8-visual--design-tools)
9. [Security Tools](#9-security-tools)
10. [Command Templates](#10-command-templates)
11. [Workflow Patterns](#11-workflow-patterns)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick Lookup](#13-quick-lookup)

---

# 1. PLUGINS

## Core Plugins (Always Install)

```bash
claude plugin install superpowers@claude-plugins-official
claude plugin install frontend-design@claude-plugins-official
claude plugin install code-review@claude-plugins-official
claude plugin install firecrawl@claude-plugins-official
claude plugin install ralph-loop@claude-plugins-official
```

| Plugin | What it does | Key Commands |
|--------|--------------|--------------|
| **superpowers** | TDD, debugging, planning, code review, skill authoring | `/brainstorm`, `/debug`, `/tdd` |
| **frontend-design** | UI/UX patterns, design system generation | Design workflows |
| **code-review** | Security, performance, quality checks | `/review` |
| **firecrawl** | Web scraping → LLM-ready markdown | Web data extraction |
| **ralph-loop** | Autonomous development loops | `/ralph-loop`, `/cancel-ralph` |

## Optional Plugins (Project-Specific)

| Plugin | Use for | Install |
|--------|---------|---------|
| **context7** | Live documentation injection | `claude plugin install context7@claude-plugins-official` |
| **typescript-lsp** | Type checking in workflow | `claude plugin install typescript-lsp@claude-plugins-official` |
| **playwright** | Browser automation | `claude plugin install playwright@claude-plugins-official` |
| **posthog** | Analytics integration | `claude plugin install posthog@claude-plugins-official` |

## Plugin Management

```bash
# List installed
claude plugin list

# Install from marketplace
claude plugin install [name]@[publisher]

# Remove
claude plugin remove [name]
```

## Ralph Loop Usage

**When to use:** Long, multi-task development sessions

```bash
# Start loop
/ralph-loop

# Claude reads PRD, works on tasks, commits, repeats
# Context resets between tasks (no pollution)

# Stop loop
/cancel-ralph
```

**Requirements:**
- Clear PRD (Product Requirements Document)
- Well-defined tasks
- ⚠️ Uses lots of tokens — watch your quota

---

# 2. AUTO MEMORY

## What is Auto Memory?

Claude's built-in cross-session memory. Claude writes notes for itself based on discoveries during sessions.

## Location

```
~/.claude/projects/<project>/memory/
├── MEMORY.md          # Index (first 200 lines auto-loaded)
├── debugging.md       # Debugging patterns
├── api-conventions.md # API decisions
└── ...
```

## Enable/Disable

```bash
# Enable (add to ~/.zshrc or ~/.bashrc)
export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0

# Disable
export CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
```

## How It Works

1. **Session start:** "Recalled X memories" — loaded past context
2. **During session:** Claude writes discoveries to memory files
3. **Session end:** "Wrote X memories" — saved new context

## Commands

```bash
# Open memory files for editing
/memory

# Check memory directory
ls ~/.claude/projects/
```

## Best Practices

- **Don't manually edit too much** — Let Claude manage
- **Review periodically** — Remove incorrect notes
- **MEMORY.md first 200 lines** — Keep concise
- **Detailed notes** — Move to separate topic files

## Limitations

- 200-line limit for auto-load
- Quality varies — Claude decides what to remember
- Can accumulate bloat over time
- Project-specific (doesn't share across projects)

---

# 3. EXTERNAL SKILLS

## Quick Install

```bash
npx skills add <owner/repo>
```

## By Category

### Frontend / UI

| Skill | What it does | Install |
|-------|--------------|---------|
| **ui-ux-pro-max-skill** | 57 UI styles, 95 color palettes | `npx skills add nextlevelbuilder/ui-ux-pro-max-skill` |
| **vercel-react-best-practices** | 40+ React/Next.js rules | `npx skills add vercel-labs/agent-skills` |
| **web-design-guidelines** | 100+ UI/UX audit rules | `npx skills add anthropics/web-design-guidelines` |

### Backend / Database

| Skill | What it does | Install |
|-------|--------------|---------|
| **postgres-best-practices** | Query optimization, RLS | `npx skills add supabase/agent-skills` |
| **backend-patterns** | API design, error handling | Manual (see below) |
| **security-review** | Security vulnerability checklist | Manual (see below) |

### Fullstack

| Skill | What it does | Install |
|-------|--------------|---------|
| **obra/superpowers** | TDD, debugging, planning | `npx skills add obra/superpowers` |
| **everything-claude-code** | Complete config collection | Manual (see below) |

### Mobile

| Skill | What it does | Install |
|-------|--------------|---------|
| **expo/skills** | React Native + Expo | `npx skills add expo/skills` |
| **react-native-best-practices** | RN optimization | `npx skills add callstackincubator/agent-skills` |

### Security

| Skill | What it does | Install |
|-------|--------------|---------|
| **ethical-hacking-methodology** | Pentest workflow | Manual (see below) |
| **pentest-checklist** | Security audit checklist | Manual (see below) |
| **aws-penetration-testing** | AWS security testing | Manual (see below) |

### Documents

| Skill | What it does | Install |
|-------|--------------|---------|
| **anthropics/skills** | docx, pdf, pptx, xlsx | `/plugin marketplace add anthropics/skills` |

## Manual Installation

### everything-claude-code (Backend)

```bash
git clone --depth 1 https://github.com/affaan-m/everything-claude-code.git /tmp/ecc
mkdir -p .claude/skills .claude/agents
cp -r /tmp/ecc/skills/backend-patterns .claude/skills/
cp -r /tmp/ecc/skills/security-review .claude/skills/
cp /tmp/ecc/agents/*.md .claude/agents/
rm -rf /tmp/ecc
```

### antigravity (Security)

```bash
git clone --depth 1 https://github.com/sickn33/antigravity-awesome-skills.git /tmp/ag
mkdir -p .claude/skills
cp -r /tmp/ag/skills/ethical-hacking-methodology .claude/skills/
cp -r /tmp/ag/skills/pentest-checklist .claude/skills/
cp -r /tmp/ag/skills/aws-penetration-testing .claude/skills/
rm -rf /tmp/ag
```

## Discovery

- **Leaderboard:** https://skills.sh
- **GitHub Search:** "claude code skill [topic]"
- **Anthropic Official:** https://github.com/anthropics/skills

---

# 4. MCP SERVERS

## What is MCP?

MCP (Model Context Protocol) = structured API access to external services.

**MCP-First Rule:** Always use MCP over CLI/docs when available.

## Installation

```bash
claude mcp add <name> -- <command>
```

## Available MCPs

### Development

| Service | Command |
|---------|---------|
| **GitHub** | `claude mcp add github -- npx -y @modelcontextprotocol/server-github` |
| **PostgreSQL** | `claude mcp add postgres -- npx -y @modelcontextprotocol/server-postgres` |
| **Filesystem** | `claude mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem` |

### Project Management

| Service | Command |
|---------|---------|
| **Linear** | `claude mcp add linear -- npx -y @linear/mcp` |
| **Notion** | `claude mcp add notion -- npx -y @notionhq/mcp` |
| **Slack** | `claude mcp add slack -- npx -y @modelcontextprotocol/server-slack` |

### Database

| Service | Command |
|---------|---------|
| **Supabase** | `claude mcp add supabase -- npx -y @supabase/mcp` |
| **MongoDB** | Search awesome-mcp |
| **Redis** | Search awesome-mcp |

## Context Warning

- Keep **< 10 MCPs** enabled per project
- Keep **< 80 active tools** total
- Each MCP consumes context even when idle
- Run `/context` to see breakdown

## Debugging Context

```
/context

Context Usage: 87,432 / 200,000 tokens (43.7%)
Breakdown:
- System Prompt: 10,234 tokens
- MCP Servers: 15,678 tokens
  - supabase-mcp: 8,234 tokens
  - github-mcp: 4,123 tokens
- CLAUDE.md: 2,345 tokens
- Conversation: 57,941 tokens
```

## Discovery

- **Official:** https://github.com/modelcontextprotocol/servers
- **Community:** https://github.com/punkpeye/awesome-mcp-servers

## Knowledge Base / Personal Search

For users with large note collections, docs, or meeting transcripts:

| Tool | What it does | Install |
|------|--------------|---------|
| **qmd** | Local markdown search (BM25 + Vector + LLM rerank) | `bun install -g https://github.com/tobi/qmd` |

**Features:**
- Hybrid search: keyword + semantic + LLM re-ranking
- 100% local (Ollama) — no data leaves your machine
- MCP server for Claude Code integration

**Setup:**
```bash
# Index your notes
cd ~/notes && qmd add .
qmd embed  # Generate embeddings

# Search
qmd search "project timeline"    # Fast keyword
qmd vsearch "how to deploy"      # Semantic
qmd query "quarterly planning"   # Hybrid (best)
```

**Claude Code MCP integration:**
```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "qmd": { "command": "qmd", "args": ["mcp"] }
  }
}
```

**When to use:**
- You have 100s/1000s of markdown notes
- AI assistant use case (searching personal knowledge)
- Need context from past meetings/docs

## External Memory Solutions

For maintaining context across Claude Code sessions beyond Auto Memory:

| Tool | What it does | Install |
|------|--------------|---------|
| **claude-supermemory** | Session-to-session memory, auto context injection | `/plugin install claude-supermemory` |
| **Mem0 MCP** | Vector DB memory, 90% token savings | `claude mcp add mem0 -- npx -y @mem0/mcp` |
| **claude-mem** | Plugin with compression | `git clone https://github.com/thedotmack/claude-mem` |

**claude-supermemory features:**
- Auto-injects relevant memories at session start
- Captures tool usage (Edit, Write, Bash) automatically
- Learns user preferences over time
- Requires Supermemory API key (https://console.supermemory.ai)

**When to use external memory:**
- Auto Memory 200-line limit is not enough
- Need cross-project memory sharing
- Team collaboration on shared context

---

# 5. MCP SKILLS (TEMPLATES)

**Why MCP Skills?** Don't re-learn MCP usage every time. Capture workflows once, reuse forever.

## Template: Supabase MCP

```markdown
---
name: mcp-supabase
description: Supabase database operations via MCP. Use when user mentions "database", "Supabase", "query", "RLS", "migration", or "schema".
---

# Supabase MCP Workflow

## Connection Check
Before operations:
- `claude mcp list` should show supabase
- If not: `claude mcp add supabase -- npx -y @supabase/mcp`

## Common Operations

### Query Data
```sql
SELECT * FROM users WHERE created_at > NOW() - INTERVAL '7 days'
```

### Create Migration
1. Create file: `supabase/migrations/YYYYMMDD_name.sql`
2. Run: `supabase db push`
3. Verify: Query to check

### RLS Policy
```sql
CREATE POLICY "Users can view own data"
ON table_name FOR SELECT
USING (auth.uid() = user_id);
```

### Edge Function
```typescript
// supabase/functions/function-name/index.ts
import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

serve(async (req) => {
  return new Response(JSON.stringify({ ok: true }))
})
```

## Error Handling
| Error | Cause | Solution |
|-------|-------|----------|
| Connection refused | Supabase not running | `supabase start` |
| Permission denied | RLS blocking | Check policies |
| Schema mismatch | Local != remote | `supabase db pull` |

## Do NOT
- Modify production directly without migration
- Skip RLS for user tables
- Hardcode service role key in frontend
```

## Template: GitHub MCP

```markdown
---
name: mcp-github
description: GitHub operations via MCP. Use when user mentions "GitHub", "PR", "issue", "commit", or "repository".
---

# GitHub MCP Workflow

## Common Operations

### Create PR
1. Ensure changes committed
2. Push branch
3. Create PR with description

### List Issues
- Filter by label, assignee, state
- Sort by created, updated, comments

### Review PR
1. Get PR diff
2. Check files changed
3. Add comments/approve

## Best Practices
- Use conventional commits
- Link issues in PR description
- Request reviews for significant changes

## Error Handling
| Error | Cause | Solution |
|-------|-------|----------|
| Auth failed | Token expired | Refresh GitHub token |
| Not found | Wrong repo | Check repository name |
```

## Template: Slack MCP

```markdown
---
name: mcp-slack
description: Slack operations via MCP. Use when user mentions "Slack", "message", "channel", or "notification".
---

# Slack MCP Workflow

## Common Operations

### Send Message
```
channel: #engineering
text: "Deployment complete ✅"
```

### Search Messages
```
query: "authentication bug"
channel: #bugs
limit: 10
```

### Create Channel
```
name: project-xyz
is_private: false
```

## Best Practices
- Use threads for discussions
- Mention users sparingly
- Use appropriate channels

## Do NOT
- Spam channels
- Send sensitive data
```

---

# 6. AGENT TEMPLATES

## security-reviewer.md

```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities
tools: Read, Grep, Glob
---

You are a security engineer.

## Checklist
- [ ] Input validation
- [ ] No injection vulnerabilities
- [ ] No hardcoded secrets
- [ ] Auth checks present
- [ ] HTTPS enforced
- [ ] CORS configured
- [ ] Rate limiting

## Output
```
[SEVERITY] file:line
Issue: [description]
→ Fix: [recommendation]
```

## Boundaries
- ✅ Read any file
- 🚫 Never modify code
```

## code-reviewer.md

```markdown
---
name: code-reviewer
description: Reviews code for quality
tools: Read, Grep, Glob
---

You are a senior engineer.

## Checklist
- [ ] DRY violations
- [ ] Error handling
- [ ] Edge cases
- [ ] Tests exist
- [ ] No N+1 queries
- [ ] Appropriate abstractions

## Output
```
[SEVERITY] file:line
Issue: [description]
→ Suggestion: [recommendation]
```

## Boundaries
- ✅ Read any file
- 🚫 Never modify code
```

## plan-reviewer.md

```markdown
---
name: plan-reviewer
description: Reviews implementation plans
tools: Read, Grep, Glob
---

You are a staff engineer.

## Review Areas
1. **Architecture** — Design, coupling, scalability, security
2. **Code Quality** — Organization, DRY, error handling
3. **Tests** — Coverage, quality, edge cases
4. **Performance** — N+1, memory, caching

## For Each Issue
1. Describe with file/line
2. Present 2-3 options
3. Recommend with reasoning
4. **Ask before proceeding**

## Workflow
Ask first:
- A) BIG: Section by section
- B) SMALL: One question per section

## Boundaries
- ✅ Read any file
- 🚫 Never modify without approval
```

## docs-agent.md

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

## test-agent.md

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

## refactor-agent.md

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

## api-agent.md

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

# 7. SKILL TEMPLATES

## Progressive Disclosure Pattern

From Anthropic's official guide:

| Level | Loaded When | Content |
|-------|-------------|---------|
| **1** | Always (system prompt) | YAML frontmatter only |
| **2** | When relevant | SKILL.md body |
| **3** | On demand | references/, scripts/ |

## Writing Good Descriptions

**Formula:** `[WHAT] + [WHEN] + [KEYWORDS] + [NEGATIVE TRIGGERS]`

```yaml
# ❌ Bad
description: Helps with projects.

# ❌ Bad — no triggers
description: Creates documentation.

# ✅ Good
description: |
  Manages sprint planning and task creation.
  Use when user mentions "sprint", "create tickets", "plan iteration".
  Do NOT use for general project questions.
```

## Skill Template

```markdown
---
name: [kebab-case-name]
description: |
  [What it does].
  Use when user mentions "[keyword1]", "[keyword2]".
  Do NOT use for [negative triggers].
---

# [Skill Name]

## When to Use
- [Condition 1]
- [Condition 2]

## Process
1. [Step 1]
2. [Step 2]
3. [Step 3]

## Examples

### Example 1
User: "[prompt]"
Actions:
1. [Action]
Result: [Outcome]

## Troubleshooting
| Issue | Solution |
|-------|----------|
| [Issue] | [Fix] |
```

## fix-issue/SKILL.md

```markdown
---
name: fix-issue
description: Fix a GitHub issue
disable-model-invocation: true
---

# Fix GitHub Issue: $ARGUMENTS

## Process
1. `gh issue view $ARGUMENTS` — get details
2. Understand the problem
3. Find relevant files
4. Implement fix
5. Write tests
6. Verify tests pass
7. Create PR with description

## PR Description Template
Fixes #$ARGUMENTS

## Changes
- [what changed]

## Testing
- [how tested]
```

Usage: `/fix-issue 1234`

## deploy/SKILL.md

```markdown
---
name: deploy
description: Deploy to production
disable-model-invocation: true
---

# Deploy to Production

## Pre-deploy Checklist
- [ ] All tests pass
- [ ] No lint errors
- [ ] No type errors
- [ ] Version bumped
- [ ] Changelog updated

## Process
1. `pnpm build` — production build
2. `pnpm test` — final test run
3. [deployment command]
4. Verify deployment
5. Monitor for errors

## Rollback
If issues detected:
[rollback command]
```

Usage: `/deploy`

---

# 8. VISUAL & DESIGN TOOLS

## Flowy

JSON-based diagrams Claude can read/write.

**Commands:**
- `/flowy-flowchart` — Process, state machines
- `/flowy-ui-mockup` — iPhone mockups

## Pencil.dev

Design on canvas → export to code.

**Website:** https://www.pencil.dev

## Figma + MCP

```bash
claude mcp add figma -- npx -y @anthropic/mcp-figma
```

---

# 9. SECURITY TOOLS

## cc-safe

Audit risky approved commands in settings.

```bash
# Install
npm install -g cc-safe

# Scan current project
npx cc-safe .

# Scan all projects
npx cc-safe ~/projects
```

**Detects:**
- `sudo`, `rm -rf`, `chmod 777`
- `curl | sh`, `wget | bash`
- `git reset --hard`, `git push --force`
- `npm publish`, `docker run --privileged`

**Why:** One user approved `rm -rf ~/` and deleted their home directory.

---

# 10. COMMAND TEMPLATES

> Copy to `.claude/commands/[name].md`

## debug.md

```markdown
# Debug

1. Reproduce the issue
2. Add minimal logging
3. Form hypothesis
4. Write failing test capturing bug
5. Fix the issue
6. Verify test passes
7. Remove debug logging
8. Run full test suite
```

## review.md

```markdown
# Review

1. Run verification (`pnpm typecheck && pnpm lint && pnpm test`)
2. Use security-reviewer agent on changed files
3. Use code-reviewer agent on changed files
4. Check test coverage for changes
5. Summarize findings by severity
```

## plan.md

```markdown
# Plan

1. Enter Plan Mode (Shift+Tab x2)
2. Explore relevant code areas
3. Identify all affected files
4. Break into small tasks
5. Write plan to SCRATCHPAD.md
6. Exit Plan Mode
7. Confirm before implementing
```

## refactor.md

```markdown
# Refactor

1. Ensure tests exist for target code
2. Run tests (must pass)
3. Make ONE refactoring change
4. Run tests (must still pass)
5. Commit with clear message
6. Repeat for next change
```

## pr.md

```markdown
# Create PR

1. Run full verification
2. Write clear PR title (conventional format)
3. Describe changes in body
4. Link related issues
5. Request reviewers if needed
6. `gh pr create`
```

---

# 11. WORKFLOW PATTERNS

## Plan Mode Workflow

```
Phase 1: Plan Mode ON (Shift+Tab x2)
> understand how [X] works
> identify files that need changes

Phase 2: Create plan
> write implementation plan

Phase 3: Plan Mode OFF → Implement
> work through tasks
> write tests

Phase 4: Commit
> descriptive message
```

## Subagent Delegation

**Why Subagents Matter:**
> "Since context is your fundamental constraint, subagents are one of the most powerful tools available." — Official Best Practices

When Claude explores a codebase, every file read consumes YOUR context. Subagents run in **separate context** and return only summaries.

```
# For research
Use subagent to investigate how auth works

# For verification
Use a subagent to review this code for security

# For exploration
Use a subagent to analyze test patterns
```

**Good Subagent Tasks:**
- "Explore how X works in our codebase"
- "Review this code for security issues"
- "Analyze all test files and summarize patterns"
- "Find all usages of function Y"

## Context Management

- **One conversation = one task**
- **3-Strike Rule** — 3+ corrections? `/clear` and restart
- **Use `/compact`** — when context large
- **Use subagents** — for exploration

## Session Management

```bash
# Name sessions
/rename auth-feature

# Resume later
claude --resume   # pick from list
claude --continue # most recent

# Checkpoint restore
/rewind
```

---

# 12. TROUBLESHOOTING

## Skill Won't Trigger

1. Check `description` has trigger keywords
2. Add more specific phrases
3. Test: "When would you use [skill-name] skill?"

## Skill Triggers Too Often

1. Add negative triggers: "Do NOT use for..."
2. Be more specific in description

## MCP Connection Failed

1. `claude mcp list` — verify installed
2. Check environment variables
3. Reinstall: `claude mcp remove X && claude mcp add X ...`

## Context Too Large

1. `/context` — see breakdown
2. Disable unused MCPs
3. `/compact` with focus
4. Use subagents for exploration

## Auto Memory Issues

1. Check: `ls ~/.claude/projects/`
2. Enable: `export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0`
3. Edit: `/memory`

---

# 13. QUICK LOOKUP

## Commands
| Command | Action |
|---------|--------|
| `Shift+Tab` | Switch model |
| `Shift+Tab x2` | Plan Mode |
| `/clear` | Clear context |
| `/compact` | Compress context |
| `/context` | Show breakdown |
| `/memory` | Edit memory files |
| `/rewind` | Restore checkpoint |
| `/ralph-loop` | Start autonomous loop |
| `!command` | Execute immediately |

## CLI
| Command | Action |
|---------|--------|
| `claude --continue` | Resume last |
| `claude --resume` | Pick session |
| `claude -p "..."` | Headless |
| `claude plugin list` | List plugins |
| `claude mcp list` | List MCPs |

## Tips
- **Verification first** — always have tests
- **Specific prompts** — include files, constraints
- **Plan complex work** — don't jump to coding
- **Subagents for research** — preserve main context
- **Use `!command`** — for quick checks
- **Check MCP skill first** — before using MCP