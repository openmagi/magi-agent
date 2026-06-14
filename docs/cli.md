# CLI

Type: Guide — what the Magi Agent CLI is and the happy-path commands. For the
full flag, output-mode, exit-code, and sub-command reference, see
[magi CLI](/docs/cli/magi).

Use `magi` for CLI work and `magi-agent serve --port 8080` for the local HTTP API and dashboard.

## Two commands

Homebrew installs both binaries:

- `magi` — the headless and interactive CLI (the normal user path for CLI work).
- `magi-agent serve --port 8080` — starts the local HTTP API and dashboard.

```sh
brew install --force-bottle openmagi/tap/magi-agent
magi --help
magi-agent --help
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

A source checkout runs the same CLI through `uv` (not npm), for example
`uv run --extra cli magi --help`.

## Happy path

```sh
# Interactive TUI (stdin is a tty, no prompt arg)
magi

# Headless — pass a prompt directly
magi -p "summarise this codebase"

# Check local configuration before running a turn
magi doctor
```

To use a real model, set a single provider key (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `FIREWORKS_API_KEY`, or
`OPENROUTER_API_KEY`) or
a `~/.magi/config.toml`. See [Configuration](/docs/configuration) and the
[environment variable reference](/docs/env-reference). With no provider
configured, `magi` still launches but uses a model-free stub.

## Permission modes

The CLI gates tool calls with four permission modes (set via `--permission-mode`).
When omitted, local CLI runs default to `bypassPermissions`; choose a stricter
mode explicitly when you want prompts or read-only planning.

- `default` — explicit prompting mode; prompts for each tool call that requires approval.
- `acceptEdits` — auto-allows edit-class tools (file writes, patches); everything else still prompts.
- `bypassPermissions` — allows all tool calls without prompting.
- `smartApprove` — opt-in mode that auto-approves low-risk tool calls and only
  prompts for higher-risk ones. It is never selected automatically; you must
  pass `--permission-mode smartApprove` to enable it.

First-party local tools are on by default; disable them with
`MAGI_FIRST_PARTY_TOOLS_ENABLED=0`.

## Agent mode (`--mode`)

The `--mode` flag selects the agent's working mode:

- `act` (default) — full tool access; the agent reads, writes, and runs commands.
- `plan` — plan-first mode that restricts the agent to read-only tools so it
  drafts a plan before acting.

```sh
magi --mode plan "refactor the auth module"
```

## Full reference

The complete flag table, output modes (`text` / `json` / `stream-json`), exit
codes, TUI keybindings, and the `doctor` / `config` / `mcp` / `auth`
sub-commands are documented in the reference:

- [magi CLI — full reference](/docs/cli/magi)
