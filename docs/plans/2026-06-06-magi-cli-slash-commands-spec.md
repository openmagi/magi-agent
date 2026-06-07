# Magi CLI Slash-Command Parity Spec (vs opencode)

- **Date:** 2026-06-06
- **Target repo:** `openmagi/magi-agent` (canonical runtime). This monorepo holds the design only.
- **Status:** Draft for review.
- **Source of comparison:** `anomalyco/opencode` slash-command surface (code-level survey, main @ `1399323`).

## 1. Goal

Bring magi-agent's CLI/TUI slash-command surface up to (and selectively past)
opencode's, by **filling magi's already-built discovery seams** rather than
inventing a parallel system. Scope was agreed as: dynamic command system +
session-control builtins, web-IDE-only commands excluded, heavy-dependency
commands deferred to a documented Phase 3.

## 2. Current state (magi-agent, code-verified)

magi already has a *more structured* command framework than opencode:

- **Command types** (`magi_agent/cli/contracts.py`):
  - `PromptCommand` — `build_prompt()` returns `list[ContentBlock]`, expands into a model turn.
  - `LocalCommand` — returns `Text | Compact | Skip`, no model round-trip.
  - `WidgetCommand` — TUI-only interactive; structurally rejected in headless.
- **Registry + dispatch** (`magi_agent/cli/commands/registry.py`): per-cwd
  memoized, first-wins, surface-masked, live availability predicates.
- **Discovery + precedence** (`magi_agent/cli/commands/discovery.py`): 7-tier
  shadow merge (`bundled → builtin-plugin → skill-dir → workflow → plugin →
  plugin-skills → builtins`).
- **Builtins** (`magi_agent/cli/commands/builtins.py`): `/status`, `/reset`,
  `/compact`, `/help` (4, all `LocalCommand`).
- **Runtime boundary** (`magi_agent/runtime/slash_control_boundary.py`) already
  *recognizes* a richer intent vocabulary the CLI does **not** expose:
  `compact, reset, status, onboarding, plan, goal, superpowers`.

**The problem:** of the 7 discovery tiers, only **2 are real** today —
`skill-dir` (`<cwd>/.claude/commands/*.md` → `MarkdownPromptCommand`) and
`builtins`. The other five (`bundled`, `builtin-plugin`, `workflow`, `plugin`,
`plugin-skills`) are empty forward-compat seams. So the framework exists but
delivers almost no commands.

## 3. Gap inventory vs opencode

| # | Item | opencode origin | magi today | Phase | Effort |
|---|------|-----------------|------------|-------|--------|
| 1 | `/init` (guided AGENTS.md setup) | bundled `PromptCommand` | bundled seam empty | P1 | Low |
| 2 | `/review` (review changes, subtask) | bundled `PromptCommand` | empty | P1 | Low |
| 3 | Markdown command **arg substitution** (`$1..$N`, `$ARGUMENTS`) + **frontmatter** (`description/agent/model/subtask`) | `hints()` + config schema | `MarkdownPromptCommand` returns verbatim text only | P1 | Low–Med |
| 4 | **Skills → commands** | `source:"skill"` | `SKILL.md` scan exists, not wired to `plugin-skills` tier | P1 | Low–Med |
| 5 | Expose magi-native intents `/plan` `/goal` `/onboarding` `/superpowers` | n/a (magi-native) | boundary recognizes them; CLI does not surface | P1 | Low |
| 6 | `/fork` (fork session) | UI action | `runtime/fork_runner.py` exists | P1 | Low–Med |
| 7 | **MCP prompts → commands** | `source:"mcp"` | MCP adapter exposes `tools/list` only, **no `prompts/list`** | P2 | Med |
| 8 | `/model` (choose model), `/agent` (switch agent), `/mcp` (toggle) | UI action / widget | no runtime switch seam found | P2 | Med |
| 9 | `/new` (new session) | UI action | session model exists, no explicit "new" command | P2 | Med |
| 10 | `/undo` `/redo` | UI action | **no per-message revert/checkpoint** | P3 | High |
| 11 | `/share` `/unshare` | UI action | session share URL is hosted (Clawy Pro), not OSS | P3 | High, OSS-boundary |
| — | `/open` `/terminal` `/workspace` | web-IDE only | — | **excluded (YAGNI)** | — |

## 4. Design decisions

1. **Reuse, don't rebuild.** Every Phase-1/2 command plugs into the existing
   `DiscoverySources` tiers or registers as a builtin. No new dispatch path.
2. **Align to magi-native vocabulary.** Where magi already has an intent
   (`plan/goal/onboarding/superpowers/compact/reset/status`), expose that —
   do **not** port opencode's naming on top.
3. **Argument substitution is a `PromptCommand` concern**, computed at
   `build_prompt()` time from `args`, mirroring opencode `hints()` semantics
   (`$1..$N` positional, `$ARGUMENTS` = full remainder).
4. **Surface honesty.** Model-free → `LocalCommand`; model-expanding →
   `PromptCommand`; interactive picker → `WidgetCommand` (TUI-only, headless
   rejected by existing dispatch rule).
5. **OSS/hosted boundary stays clean.** Phase-3 `/share` is a hosted concern;
   the OSS spec only defines the *seam*, never the hosted URL service.

## 5. Phase 1 — fill the real seams (highest leverage)

### 5.1 Bundled commands `/init`, `/review`
- New module `magi_agent/cli/commands/bundled.py` returning `list[Command]`,
  wired into `DiscoverySources.bundled` via `discovery.discover_commands`.
- `InitCommand(PromptCommand)`: `build_prompt` returns a bundled template
  (`magi_agent/cli/commands/templates/initialize.txt`) with `${path}` →
  `ctx.cwd`/worktree. Mirrors opencode `command/index.ts` `Default.INIT`.
- `ReviewCommand(PromptCommand)`: bundled `review.txt`, `subtask=True`
  semantics (run in a child runner). Mirrors opencode `Default.REVIEW`.
- Description strings: `/init` = "guided AGENTS.md setup", `/review` =
  "review changes [commit|branch|pr], defaults to uncommitted".

### 5.2 Markdown command arg substitution + frontmatter
- Extend `MarkdownPromptCommand` (`discovery.py`):
  - Parse YAML frontmatter at load (`markdown_commands`): `description`,
    `agent`, `model`, `subtask`. Strip frontmatter from the captured `text`.
  - At `build_prompt`, substitute `$1..$N` from positional `args` tokens and
    `$ARGUMENTS` from the full argument string. Add a `hints` field computed
    the same way as opencode `hints()` for autocomplete display.
- Backward compatible: files with no frontmatter / no placeholders behave
  exactly as today (verbatim text).

### 5.3 Skills → commands (`plugin-skills` tier)
- New `magi_agent/cli/commands/skill_commands.py`: reuse the existing
  `SKILL.md` scan locations (`skills/`, `.magi/skills/`, `docs/superpowers/`,
  bundled `magi_agent/skills/bundled`) from `plugins/native/skills.py`.
- Each `SKILL.md` → a `PromptCommand` named by skill `name` (frontmatter),
  `description` from frontmatter, `build_prompt` returns the skill body.
- Populate `DiscoverySources.plugin_skills`. Precedence already ensures a
  project `.claude/commands/*.md` (skill-dir tier) can shadow a skill name.

### 5.4 Expose magi-native intents
- Add `LocalCommand`/`PromptCommand` builtins for `/plan`, `/goal`,
  `/onboarding`, `/superpowers` that project through the existing
  `SlashControlBoundary` (same delegation pattern as `builtins.py` uses for
  `compact/reset/status`). No new parsing — the boundary already recognizes
  these. Update `BUILTIN_COMMAND_NAMES` and `/help` output accordingly.

### 5.5 `/fork`
- `ForkCommand` builtin backed by `runtime/fork_runner.py`. Define the CLI →
  fork-runner call shape; if fork requires an active session id, gate
  availability via the registry `is_enabled` predicate (hidden when no session).

## 6. Phase 2 — runtime-switch commands (short-term)

### 6.1 MCP prompts → commands
- Add a `prompts/list` + `prompts/get` capability to the MCP adapter
  (`plugins/mcp_adapter.py`) paralleling its existing `tools/list`. This is
  **net-new** (magi's MCP is tool-centric today).
- Discovery: connected MCP server prompts → `PromptCommand` (`source:"mcp"`)
  into the `plugin` tier, args mapped `$1..$N` to prompt arguments (opencode
  `command/index.ts:112-139` pattern). Lazy template resolution.

### 6.2 `/model`, `/agent`, `/mcp` (TUI widgets)
- `WidgetCommand`s (TUI-only; headless dispatch already rejects widgets):
  - `/model` → model picker; needs a runtime "set active model" seam (none
    found — must be added in the runner/session layer).
  - `/agent` → agent switch/cycle; same, needs an "active agent" seam.
  - `/mcp` → toggle MCP servers for the session.
- Headless equivalents (optional): accept an explicit arg (`/model <id>`) as a
  `LocalCommand` so headless users aren't blocked by the widget-only path.

### 6.3 `/new`
- `LocalCommand` (or runner control) that starts a fresh session via the
  existing `session_continuity` / `session_identity` layer.

## 7. Phase 3 — deferred seams

**Status (2026-06-07):** shipped as default-off protocol seams in
`magi_agent/cli/commands/session_history.py` (PR5). All five commands
(`/fork`, `/undo`, `/redo`, `/share`, `/unshare`) are registered by
`build_registry` via `register_session_history_commands`, always findable via
`lookup`, and hidden from `list_for` until a real controller is wired onto
`ctx.runtime`. Live activation is deferred (no real fork/revert/share logic in
this module).

**`/fork` reclassification:** the original spec listed `runtime/fork_runner.py`
as the substrate for `/fork`. This is incorrect — `fork_runner.py` is a
prompt-cache parallel-child primitive (runs independent sub-tasks), NOT session
branching. The real substrate for session forking is the `cli/session_log.py`
DAG: its `append` method already supports a `parent_uuid` parameter enabling
tree-shaped history, but no session manager wires that path today. The
`SessionForker` protocol seam defines the contract a future session manager
must satisfy.

**Phase-2 runtime-switch commands** (`/model`, `/agent`, `/mcp`, `/new`) also
shipped as default-off protocol seams in `magi_agent/cli/commands/control.py`
(PR4), following the same pattern.

Remaining dependencies before live activation:
- **`/undo`, `/redo`:** require a per-message **revert/checkpoint** subsystem
  (no message-level snapshot exists in `runtime/`). The `SessionRevert`
  protocol seam (`can_undo`, `undo`, `can_redo`, `redo`) defines the contract.
  Visibility predicates are context-sensitive: `/undo` shown only when
  `can_undo()` is True; `/redo` only when `can_redo()` is True.
- **`/share`, `/unshare`:** session sharing produces a public URL — a hosted
  (Clawy Pro) concern outside the OSS runtime. The `SessionShareProvider`
  protocol seam defines the contract. `/unshare` is shown only when
  `shared_url()` is not None (session is currently shared).

## 8. Testing

- Unit (per source): `discover_commands` shadow/precedence with each new tier
  populated; arg-substitution + frontmatter parsing; bundled `/init`/`/review`
  template rendering; skill scan → command mapping; magi-native builtins
  project through the boundary correctly.
- Surface: headless rejects the Phase-2 widgets; `/help` lists the new builtin
  set; markdown back-compat (no frontmatter, no placeholders) unchanged.
- Follow the repo's focused `uv run --extra dev pytest magi_agent/cli/...`
  pattern matching touched files.

## 9. Out of scope

- `/open`, `/terminal`, `/workspace` (opencode web-IDE-only UI actions).
- The hosted share-URL service itself (only the OSS seam is specified).
- Any change to dispatch, registry precedence, or the `WidgetCommand` headless
  rejection rule — all reused as-is.

## 10. Sequencing

1. P1.2 (arg substitution/frontmatter) and P1.1 (`/init`,`/review`) first —
   they unlock the most user value with zero new runtime seams.
2. P1.3 (skills) + P1.4 (magi-native) + P1.5 (`/fork`).
3. P2.1 (MCP prompts) — gated on the new `prompts/list` adapter capability.
4. P2.2/P2.3 (`/model`,`/agent`,`/mcp`,`/new`) — gated on runtime switch seams.
5. P3 documented; implement only when revert / hosted-share are prioritized.
