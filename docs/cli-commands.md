# In-session Commands (slash commands)

> **Note — early surface.** Several builtins record an *intent* via the boundary rather than performing a full action yet (`magi_agent/cli/commands/builtins.py`).

Inside an interactive `magi` session (and the chat surface) you can type `/`-prefixed
commands. These are distinct from the `magi <subcommand>` CLI commands documented in
the [CLI reference](/docs/cli-reference) (`magi doctor`, `magi auth`, etc.).

## Builtin commands

| Command | What it does today |
|---|---|
| `/help` | List the available builtin command names. |
| `/status` | Summarize the current boundary decision as a redaction-safe line. |
| `/compact` | Request context compaction (consults the boundary). |
| `/reset` | Acknowledge a reset intent. |
| `/plan` | Acknowledge a plan-mode intent via the boundary. |
| `/goal` | Acknowledge a goal-setting intent via the boundary. |
| `/onboarding` | Acknowledge an onboarding intent via the boundary. |
| `/superpowers` | Acknowledge a superpowers intent via the boundary. |

Several of these (`/reset`, `/plan`, `/goal`, `/onboarding`, `/superpowers`) are
currently **intent seams**: they record a redaction-safe, reason-coded intent
through the slash-control boundary rather than performing the full action. Expect
this surface to deepen over time.

## Bundled commands

First-party commands shipped with the package (cannot be shadowed by project files):

| Command | Purpose |
|---|---|
| `/init` | Scaffold first-party project files. |
| `/review` | Run the bundled review flow. |

## Discovering your own commands

Commands are merged from several sources, first match wins
(`magi_agent/cli/commands/discovery.py`):

1. bundled (above)
2. builtin plugins
3. **project commands** — `<cwd>/.claude/commands/*.md` (the file stem is the
   command name; optional YAML frontmatter: `description`, `agent`, `model`,
   `subtask`; argument substitution `$1`..`$N`, `$ARGUMENTS`)
4. workflows
5. plugins
6. plugin skills — `SKILL.md` files in standard skill locations
7. builtins (above)

So you can add a project command by dropping a markdown file in
`.claude/commands/`. Run `/help` to see what resolved in your session.

## See also

- [CLI reference](/docs/cli-reference) — the `magi <subcommand>` surface and flags.
- [What works today](/docs/what-works-today) — live vs default-off surfaces.
