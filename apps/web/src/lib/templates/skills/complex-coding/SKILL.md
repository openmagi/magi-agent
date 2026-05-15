---
name: complex-coding
description: Use when the user asks for complex coding tasks — building apps, refactoring codebases, writing multi-file projects. Delegates to Claude Code CLI as a specialized coding agent while the bot acts as PM.
user_invocable: true
metadata:
  author: openmagi
  version: "2.0"
---

# /complex-coding — Agent-to-Agent Coding

You have access to a professional coding agent (Claude Code CLI). For complex coding tasks, delegate the work instead of doing it yourself. You are the **PM** — the coding agent is the **developer**.

## When to Use

- Building multi-file projects (APIs, apps, libraries)
- Complex refactoring across multiple files
- Tasks requiring iterative build/test cycles
- Anything beyond simple single-file edits

## When NOT to Use

- Simple single-file edits (do it yourself)
- Questions about code (answer directly)
- Config changes or one-liner scripts

## PM Workflow

### Step 1: Analyze & Plan

Before delegating, understand what the user wants:
- What are they building? (scope)
- What language/framework? (constraints)
- Any existing code to work with? (context)
- Vibe mode or production mode? (quality bar)

Summarize the spec back to the user in 2-3 sentences for confirmation.

### Step 2: Initialize Project Sandbox

The coding agent works best with a properly set up project sandbox. **Always initialize before delegating.**

#### Project Sandbox Protocol

Use the native `CodeWorkspace` tool when available. If you only have shell access, create the same layout manually:

```bash
TASK_ID=$(date +%s)
PROJECT_NAME="${PROJECT_NAME:-project-$TASK_ID}"
PROJECT_SLUG=$(printf "%s" "$PROJECT_NAME" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g; s/\.{2,}/./g')
PROJECT_SLUG="${PROJECT_SLUG:-project-$TASK_ID}"
WORK_DIR="/workspace/code/$PROJECT_SLUG"

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# Initialize git for diff, checkpoint, and rollback evidence.
git init

cat > "$WORK_DIR/.sandbox.json" << SANDBOX
{
  "taskId": "$TASK_ID",
  "project": "$PROJECT_SLUG",
  "root": "$WORK_DIR",
  "createdAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "bot-pod",
  "docker": "unavailable"
}
SANDBOX

cat > "$WORK_DIR/.gitignore" << 'GITIGNORE'
node_modules/
.next/
dist/
build/
coverage/
.env
.env.*
*.log
GITIGNORE
```

**Docker boundary:** The user bot pod is already a non-root Kubernetes container. There is **No Docker-in-Docker**, no privileged containers, and no `/var/run/docker.sock` mount. You may write a `Dockerfile`, `compose.yaml`, or devcontainer config as project artifacts, but do not treat `docker build`, `docker run`, or `docker compose up` as available verification. Verify with in-workspace commands such as `npm test`, `npm run build`, `pytest`, `go test ./...`, `cargo test`, `TestRun`, and `GitDiff`.

If the user provides existing code or a repo URL:
```bash
# Copy provided files into the sandbox, not the workspace root.
cp -R /path/to/source/. "$WORK_DIR/"

# Or clone directly into the sandbox.
cd "$WORK_DIR" && git clone --depth 1 "$REPO_URL" .
```

**Create CLAUDE.md for the coding agent's workspace.** This is critical — it tells the coding agent how to work:

```bash
cat > "$WORK_DIR/CLAUDE.md" << 'CLAUDEMD'
# Project: [NAME]

## Tech Stack
- Language: [e.g., TypeScript 5.x]
- Framework: [e.g., Next.js 15 / FastAPI / Express]
- Testing: [e.g., Vitest / Pytest]
- Package Manager: [e.g., npm / pnpm / pip]

## Commands
- `[package-manager] dev` — Start dev server
- `[package-manager] build` — Production build
- `[package-manager] test` — Run tests
- `[package-manager] lint` — Lint check

## Verification
**Run after every change:**
1. Lint check
2. Type check (if applicable)
3. Run tests

## TDD Workflow
1. **RED** — Write failing test first
2. **GREEN** — Write minimal code to pass
3. **REFACTOR** — Clean up, tests still pass

## Code Style
### Naming
- Variables/functions: camelCase
- Components/classes: PascalCase
- Constants: UPPER_SNAKE_CASE
- Files: kebab-case

### Imports
- Group: external → internal → relative

## Boundaries
### Always
- Write failing test before implementing
- Run verification before completing
- Handle errors explicitly
- Keep all project code, dependencies, build outputs, and generated test artifacts inside this project sandbox.
- Docker-in-Docker, privileged containers, root operations, and host Docker socket mounts are unavailable.
- If Docker files are requested, author them as artifacts and verify behavior with the closest source-native command available in this sandbox.

### Never
- Skip type checking
- Leave console.logs in production code
- Over-engineer — YAGNI
CLAUDEMD
```

**Customize the CLAUDE.md** based on what the user is building. The template above is a starting point — adapt tech stack, commands, and style to match the project.

### Step 3: Delegate to Coding Agent

Write a detailed spec following the vibe coding principle: **specific input → specific output**.

```bash
cat > /tmp/coding-spec-$TASK_ID.md << 'SPEC'
## Task
[Clear, specific description of what to build]

## Requirements
- [Requirement 1 — be specific, not vague]
- [Requirement 2]

## Tech Stack
- [Language/framework — already in CLAUDE.md but repeat here for clarity]

## Constraints
- [Things that must be true]
- [Performance requirements if any]

## Out of Scope
- [What NOT to build — prevents over-engineering]

## Verification Targets
- [How to know it's done — test cases, expected behavior]

## Quality Bar
- [Vibe mode: working > perfect, skip tests OK]
- [Production mode: tests required, error handling, proper types]
SPEC

cd "$WORK_DIR" && claude-agent.sh "$(cat /tmp/coding-spec-$TASK_ID.md)"
```

**CRITICAL rules for writing specs:**
- The coding agent has ZERO context about the user's conversation — include everything
- Be specific: "Add email/password login with JWT stored in httpOnly cookies" not "Add authentication"
- Include out-of-scope to prevent scope creep
- Add verification targets so the agent knows when it's done

### Step 4: Review Results (PM Review)

After the agent completes, **review like a PM — not a rubber stamp:**

```bash
# Check what was created
find "$WORK_DIR" -type f \( -name '*.ts' -o -name '*.js' -o -name '*.py' -o -name '*.go' -o -name '*.rs' \) | head -20

# Review key files
cat "$WORK_DIR/<entry-point>"

# Run tests if they exist
cd "$WORK_DIR" && npm test 2>&1 | tail -20
```

**PM review checklist:**
- Does the output match the user's request?
- Are there obvious errors or missing pieces?
- Did the agent over-engineer? (common — check for unnecessary abstractions)
- Are there hardcoded values that should be configurable?
- If issues found → **re-invoke with specific fix instructions, not vague feedback**

**If review fails 3 times on the same issue:** Break the task into smaller sub-tasks and delegate each separately. Don't keep fighting the same problem.

### Step 5: Iterative Refinement (Optional)

For complex projects, use multi-round delegation:

```
Round 1: "Build the data models and API endpoints"
  → Review → Approve
Round 2: "Add the frontend UI using the API from round 1"
  → Review → Approve
Round 3: "Add tests and error handling"
  → Review → Approve
```

This mirrors the Opus→Sonnet workflow from vibe coding: plan first (PM), implement in focused rounds (coding agent).

### Step 6: Deliver Results

**Option A: Inline delivery (few files)**
Read each file and present the code to the user with explanation.

**Option B: Gist delivery (many files)**
```bash
FILES_JSON=$(find "$WORK_DIR" -type f \( -name '*.ts' -o -name '*.js' -o -name '*.py' -o -name '*.go' -o -name '*.rs' -o -name '*.json' -o -name '*.yaml' -o -name '*.toml' -o -name '*.html' -o -name '*.css' \) ! -path '*/node_modules/*' ! -path '*/.git/*' | while read f; do
  FNAME=$(echo "$f" | sed "s|$WORK_DIR/||")
  CONTENT=$(cat "$f" | jq -Rs .)
  echo "\"$FNAME\": {\"content\": $CONTENT}"
done | paste -sd, -)

GIST_RESULT=$(curl -s -X POST https://api.github.com/gists \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"description\": \"Generated by Open Magi coding agent\", \"public\": false, \"files\": {$FILES_JSON}}")

echo "$GIST_RESULT" | node -e "const d=require('fs').readFileSync('/dev/stdin','utf8');console.log(JSON.parse(d).html_url||'Gist creation failed')"
```

**Option C: File attachment**
For individual files, use the chat attachment system (see TOOLS.md → File Attachments).

### Step 7: Cleanup

Preserve `/workspace/code/$PROJECT_SLUG` unless the user explicitly asked for a throwaway prototype. It is the project artifact and git evidence store.

```bash
rm -f "/tmp/coding-spec-$TASK_ID.md"
```

For explicitly temporary experiments only:

```bash
rm -rf "$WORK_DIR"
```

## Cost Awareness

Coding agent uses credits (routed through the bot's own router). For large tasks:
- Warn the user: "이 작업은 크레딧을 사용합니다. 진행할까요?"
- Estimate: simple (1-2 turns, ~$0.05) / medium (5-10 turns, ~$0.20) / complex (10-30 turns, ~$0.50-2.00)

## Error Handling

If `claude-agent.sh` fails:
1. Check exit code and stderr
2. Common issues:
   - Timeout (10min) → break task into smaller pieces
   - API error → retry once, then inform user
3. Never expose raw error output to user — summarize the issue

## Example Interaction

**User:** Next.js로 블로그 만들어줘. MDX 지원, 다크모드, 태그 필터링.

**Bot (PM):**
> Next.js 15 + MDX + Tailwind로 블로그를 만들겠습니다.
> - MDX 파일 기반 포스트, frontmatter로 메타데이터
> - 다크모드 토글 (next-themes)
> - 태그별 필터링
> - 코딩 에이전트에 위임합니다. 잠시만요...

*[Initializes workspace with CLAUDE.md customized for Next.js + MDX]*
*[Writes detailed spec with out-of-scope: "No CMS, no comments, no auth"]*
*[Invokes claude-agent.sh]*
*[Reviews: checks pages render, MDX parses, dark mode toggles, tags filter]*

> 완료! 8개 파일이 생성됐어요:
> - `app/page.tsx` — 포스트 목록 + 태그 필터
> - `app/blog/[slug]/page.tsx` — 개별 포스트
> - `components/theme-toggle.tsx` — 다크모드 토글
> - `lib/mdx.ts` — MDX 파서
> - ...
> [Gist 링크]
