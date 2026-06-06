# CLI

Magi Agent installs two commands:

```bash
magi
magi-agent
```

`magi` is the terminal work interface. `magi-agent` starts the local runtime
server and dashboard.

## First commands

```bash
magi --help
magi --version
magi -p "Plan a coding fix"
magi --output text "Summarize this repository"
magi --output json "Summarize this repository"
magi --output stream-json "Summarize this repository"
magi --mode plan "Review the docs and propose edits"
magi --mode act "Apply the approved edits"
```

```bash
magi-agent --help
magi-agent serve --port 8080
```

## Interactive use

Run:

```bash
magi
```

The interactive interface keeps a local session and streams public runtime
events when available.

## One-shot use

```bash
magi -p "Inspect this repository and list the runnable surfaces"
cat notes.md | magi --output text "Extract decisions and open questions"
```

Use one-shot mode for scripts, checks, and quick operator tasks.

The CLI enters non-interactive mode when any of these is true:

- a prompt argument is provided;
- `--print` or `-p` is provided;
- stdin is piped or redirected.

## Output modes

Use `--output text` for human-readable terminal output, `--output json` when a
script needs one structured result, and `--output stream-json` when a caller
wants incremental runtime events.

```bash
magi --output text "List the docs pages"
magi --output json "Return a short status object"
magi --output stream-json --include-partial-messages "Stream progress"
```

## Plan and act modes

Use plan mode when you want the agent to inspect and propose before writing:

```bash
magi --mode plan "Find the likely cause of this failing test"
```

Use act mode only when writes and tool execution are intended:

```bash
magi --mode act "Implement the approved fix and run focused tests"
```

Plan mode is read-oriented. Act mode exposes the fuller local tool surface.

## Permission modes

Permission mode controls how write and execution requests are handled by the
local CLI runtime:

```bash
magi --permission-mode default "Inspect this repository"
magi --permission-mode acceptEdits "Apply the approved patch"
magi --permission-mode bypassPermissions "Run the approved local smoke"
```

Use bypass mode only in a trusted workspace where unattended tool execution is
intended.

## Sessions

Use `--resume` to continue a named local CLI session:

```bash
magi --resume docs-session "Continue the docs review"
```

The session log defaults to `~/.magi` and can be relocated with:

```bash
export MAGI_CLI_SESSION_DIR=/path/to/session-log-root
```

## Diagnostics

```bash
magi doctor
magi auth composio status
```

These commands report local optional integration state without granting tool
authority by themselves.

## Server command

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

`magi-agent` accepts `serve` as an explicit command and also supports the same
port flag at the top level:

```bash
magi-agent --port 8080
```

Use `magi-agent serve --help` to inspect the current server arguments.
