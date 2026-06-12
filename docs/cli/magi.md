# magi CLI

Type: Reference — the full flag, output-mode, exit-code, keybinding, and
sub-command reference. For the concise overview and happy-path guide, see
[CLI](/docs/cli).

Magi is the headless and interactive CLI for Magi Agent. It uses the same
`magi_agent` package as the local HTTP API, dashboard, and ADK session layer.

## Installation

Install Magi Agent with Homebrew:

```sh
brew install --force-bottle openmagi/tap/magi-agent
```

That installs both commands:

```sh
magi --help
magi-agent --help
```

For source checkout development, install the optional CLI extra with `uv` or
`pip`.

## Running

```sh
# Interactive TUI (stdin is a tty, no prompt arg)
magi

# Headless — pass a prompt directly
magi -p "summarise this codebase"
magi "list the open GitHub issues"

# Read prompt from stdin (non-tty auto-selects headless)
echo "what is 2+2" | magi
cat prompt.txt | magi
```

## Headless mode (`-p` / `--print`)

Headless mode runs when any of the following is true:

- A positional `[prompt]` argument is supplied.
- The `--print` / `-p` flag is set.
- `sys.stdin.isatty()` returns `False` (stdin is piped or redirected).

### Output modes (`--output`)

| Mode | Description |
|------|-------------|
| `text` (default) | Prints only the final assistant text, then exits. |
| `json` | Prints a single JSON `result` object on stdout. |
| `stream-json` | Live NDJSON stream — one frame per line, flushed immediately. |

`--output` only affects headless mode; the TUI always uses its own rendering.

#### `text` example

```sh
magi -p "say hello" --output text
# Hello!
```

#### `json` example

```sh
magi -p "say hello" --output json
# {"type":"result","subtype":"success","result":"Hello!","is_error":false,...}
```

#### `stream-json` example

```sh
magi -p "say hello" --output stream-json
```

Each line is a complete JSON object. Frame types in order:

```jsonl
{"type":"system","subtype":"init","session_id":"...","tools":[],"model":"magi","cwd":"/home/user","uuid":"..."}
{"type":"assistant","session_id":"...","message":{"role":"assistant","content":"Hello!"},"uuid":"..."}
{"type":"result","subtype":"success","result":"Hello!","is_error":false,"usage":{...},"total_cost_usd":0.0,"errors":[],"uuid":"..."}
```

Frame type taxonomy:

| `type` | When emitted |
|--------|-------------|
| `system` / `init` | First frame; session metadata and tool list. |
| `system` / `status` | Progress, task events, tool status updates. |
| `assistant` | Assistant text chunk or tool-use call. |
| `user` | Tool result (tool-use response from the runtime). |
| `stream_event` | Raw engine events (only when `--include-partial-messages`). |
| `result` | Terminal frame; always the last line. |

#### `--include-partial-messages`

Adds raw `stream_event` frames for every engine event (token deltas, tool
progress, etc.) before the projected `assistant`/`user` frames. Useful for
latency-sensitive consumers that want to render tokens as they arrive.

```sh
magi -p "count to 3" --output stream-json --include-partial-messages
```

### Piping and the inbound control channel

When `--output stream-json` is used with an explicit prompt arg (not stdin), the
process's `stdin` is free to act as an inbound NDJSON control channel. The
runtime reads:

- `{"type":"control_response", ...}` — answer a pending `control_request` frame
  (permission gate ask).
- `{"type":"control_cancel_request"}` — cancel the in-flight turn.

When the prompt is read from stdin, the inbound channel is unavailable.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Turn completed successfully. |
| `1` | Turn completed with an error (engine error, max turns, or aborted). |
| `2` | CLI disabled via `MAGI_CLI_ENABLED=0`. |

## Interactive TUI

When `magi` is run without a prompt argument and stdin is a tty, the Textual TUI
launches. The TUI uses the same engine driver as headless mode.

### Default keybindings

Keybindings are resolved by context. The built-in defaults are:

#### Global (active everywhere)

| Key | Action |
|-----|--------|
| `Ctrl+C` | Cancel in-flight turn (`chat:cancel`) |
| `Ctrl+D` | Quit (`global:quit`) |
| `Ctrl+Q` | Quit (`global:quit`) |

#### Chat (prompt input active)

| Key | Action |
|-----|--------|
| `Enter` | Submit prompt (`chat:submit`) |
| `Escape` | Cancel in-flight turn (`chat:cancel`) |
| `Shift+Enter` | Insert newline (`chat:newline`) |
| `Ctrl+S` | Stash current input (`chat:stash`) |
| `Ctrl+X` then `Ctrl+K` | Kill/cancel all agents (`chat:killAgents`) |

#### Autocomplete overlay

| Key | Action |
|-----|--------|
| `Tab` | Accept selected completion (`autocomplete:accept`) |
| `Down` | Next item (`autocomplete:next`) |
| `Up` | Previous item (`autocomplete:prev`) |
| `Escape` | Dismiss overlay (`autocomplete:dismiss`) |

#### Confirmation modal

| Key | Action |
|-----|--------|
| `Y` | Allow (`confirmation:allow`) |
| `N` | Deny (`confirmation:deny`) |

**v1.1 follow-ups:** vim mode and keybindings hot-reload remain deferred.
User `keybindings.json` loading **is** wired (see below).

## Keybindings customization (`keybindings.json`)

The TUI loads a user keybindings config on startup from
`<MAGI_CLI_SESSION_DIR or ~/.magi>/keybindings.json` — the same config root
the session log, history, and theme settings use. When the file is absent the
TUI uses the built-in defaults only; when present, its bindings are merged over
the defaults (user overrides win). The loader never raises: a missing,
malformed, or unknown-action entry is gracefully ignored and degrades to the
built-in defaults, so the app always has a usable keymap.

When a chord is partially typed (e.g. `Ctrl+X` of `Ctrl+X Ctrl+K`), a
**which-key overlay** appears showing the candidate continuations for the
pending chord; it hides when the chord resolves or is cancelled.

There is no `--keybindings` flag; the config path is the fixed
`keybindings.json` under the config root above. Vim mode and hot-reload of an
edited file are deferred to v1.1.

To override defaults, place a `keybindings.json` file in your config directory
(`~/.magi/` by default, or `$MAGI_CLI_SESSION_DIR` when set).

### Format

The file is a JSON object with a single `"bindings"` array. Each element in the
array is a binding block for one context:

```json
{
  "bindings": [
    {
      "context": "Chat",
      "bindings": {
        "ctrl+enter": "chat:submit",
        "ctrl+x ctrl+k": "chat:killAgents",
        "alt+w": null
      }
    }
  ]
}
```

- **`context`** — one of the 18 context names: `Global`, `Chat`, `Autocomplete`,
  `Confirmation`, `Help`, `Transcript`, `HistorySearch`, `Task`, `ThemePicker`,
  `Settings`, `Tabs`, `Attachments`, `Footer`, `MessageSelector`, `DiffDialog`,
  `ModelPicker`, `Select`, `Plugin`.
- **Keys in `bindings`** — keystroke or chord string. Modifiers: `ctrl`/`alt`/
  `shift`/`meta`/`cmd`. Chords: space-separated keystrokes, e.g. `ctrl+x ctrl+k`.
- **Value** — an action string (see below) or `null` to unbind the key.

### Valid actions

Closed action set for v1:

| Action | Meaning |
|--------|---------|
| `global:quit` | Exit the TUI. |
| `chat:submit` | Submit the prompt. |
| `chat:cancel` | Cancel the in-flight turn. |
| `chat:newline` | Insert a newline in the prompt. |
| `chat:stash` | Stash the current input. |
| `chat:killAgents` | Cancel all running agents. |
| `autocomplete:accept` | Accept the highlighted completion. |
| `autocomplete:next` | Move to the next completion. |
| `autocomplete:prev` | Move to the previous completion. |
| `autocomplete:dismiss` | Dismiss the autocomplete overlay. |
| `confirmation:allow` | Allow the pending tool call. |
| `confirmation:deny` | Deny the pending tool call. |
| `command:<name>` | Dispatch a slash-command (Chat context only). |

### Reserved shortcuts

The following shortcuts are reserved and cannot be rebound:

- `Ctrl+C`, `Ctrl+D`, `Ctrl+M` (Enter) — system-level interrupt/exit.
- `Ctrl+\` — terminal SIGQUIT (error if rebind attempted).
- `Ctrl+Z` — terminal SIGTSTP (warning if rebind attempted).

The loader validates the file on startup and emits non-fatal warnings for any
violation; it never raises — it degrades to the built-in defaults on error.

## Permission modes (`--permission-mode`)

| Mode | Behavior |
|------|----------|
| `default` | Prompts for each tool call that requires approval. |
| `acceptEdits` | Automatically allows edit-class tools (file writes, patches). |
| `bypassPermissions` | Allows all tool calls without prompting. |
| `smartApprove` | Opt-in: auto-approves low-risk tool calls and only prompts for higher-risk ones. Never selected automatically — pass `--permission-mode smartApprove`. |

```sh
magi -p "apply the patch" --permission-mode acceptEdits
magi -p "apply the patch" --permission-mode smartApprove
```

## Agent mode (`--mode`)

`--mode` selects the agent's working mode:

| Mode | Behavior |
|------|----------|
| `act` (default) | Full tool access — the agent reads, writes, and runs commands. |
| `plan` | Plan-first mode restricted to read-only tools; the agent drafts a plan before acting. |

```sh
magi --mode plan "refactor the auth module"
```

## `MAGI_CLI_ENABLED`

The CLI is enabled by default. Set `MAGI_CLI_ENABLED=0` (or `false` / `no` /
`off`) to disable it; the process exits immediately with code 2 without writing
any output to stdout.

```sh
MAGI_CLI_ENABLED=0 magi -p "test"   # exits 2 immediately
```

## Flags reference

```
magi [OPTIONS] [PROMPT]

Arguments:
  PROMPT  Prompt to send to the agent. [optional]

Options:
  -p, --print                    Print response and exit (headless mode).
  --output [text|json|stream-json]
                                 Output format for headless mode. [default: text]
  --include-partial-messages     Include partial streaming events in stream-json.
  --permission-mode [default|acceptEdits|bypassPermissions|smartApprove]
                                 Permission mode.  [default: default]
  --resume TEXT                  Resume a session by id.
  --continue / --no-continue     Continue the most-recent session.
  --model TEXT                   Model to use.
  --mode [plan|act]              Agent mode: plan (read-only tools) | act (full
                                 tools).  [default: act]
  -V, --version                  Print the magi version and exit.
  --help                         Show this message and exit.
```

**Note:** `--version` / `-V` is handled by the launcher (`__main__.py` Layer-0
fast path) BEFORE the agent or any heavy imports run: it prints the package
version and exits 0.

**Note:** `--resume` and `--continue` thread a session id today; true session
rehydration (replaying prior turn history into the engine) is a v1.1 follow-up —
engine rehydration is not yet implemented.

## `magi doctor`

`magi doctor` runs local environment diagnostics so a first-time user can see
what is and is not configured before running a turn. It checks:

- **Provider config resolvable** — whether a provider key (`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `FIREWORKS_API_KEY`)
  or a `~/.magi/config.toml` (path overridable via `MAGI_CONFIG`) selects a real
  model. If nothing is configured, the CLI falls back to a model-free stub.
- **`litellm` importable** — whether the optional provider runtime dependency is
  installed (a configured provider needs it; if missing, turns return an install
  hint instead of a model answer).
- **Config path readable** — whether the resolved config file path can be read.
- **Working directory writable** — whether the current working directory can be
  written to.

```sh
magi doctor
```

## Other sub-commands

```sh
magi auth composio status   # show Composio authentication status (implemented)
magi legalbench ...         # run the LegalBench evaluation harness (implemented)
```

`magi auth composio status` reports whether the optional Composio integration is
configured. `magi legalbench` drives the LegalBench measurement harness; it
requires `MAGI_LEGAL_HARNESS_ENABLED=1` and a dataset under `--data-root`
(`--manifest`, `--max-tasks`, `--ablation` options), and prints harness/baseline/
lift as JSON.

### Stub sub-commands

These resolve but are not yet implemented:

```sh
magi config   # manage configuration (stub)
magi mcp      # manage MCP connections (stub)
```
