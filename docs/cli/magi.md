# magi CLI

Magi is the headless and interactive CLI for the OpenMagi Python agent runtime.
It is the third consumer of the `magi_agent` package alongside the
FastAPI server and the ADK session layer.

## Installation

The CLI deps (`textual`, `rich`, `rapidfuzz`, `typer`) are an optional extra so
the headless/server core stays lean.

```sh
pip install ".[cli]"
```

After installation the `magi` console-script is available on your PATH.

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

**v1.1 follow-ups:** vim mode, keybindings hot-reload, and **user
`keybindings.json` loading** are not yet implemented. In v1, the TUI
ships with built-in defaults only (`load_keybindings(None)`); there is no
`--keybindings` flag and no config-path auto-load. Custom keymap loading
is deferred to v1.1.

## Keybindings customization (`keybindings.json`) — v1.1

> **v1 status:** The TUI loads built-in defaults only. User-supplied
> `keybindings.json` files are **not loaded in v1** — there is no
> `--keybindings` CLI flag and no automatic config-path lookup. The
> format below is accurate (the loader supports it), but the file will
> not be read until v1.1 wires up the flag and auto-load path.

To override defaults in v1.1, place a `keybindings.json` file in your
config directory (path to be passed via a `--keybindings` flag or via
the default config path, both planned for v1.1).

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

```sh
magi -p "apply the patch" --permission-mode acceptEdits
```

## `MAGI_CLI_ENABLED`

The CLI is **default-ON** as of Track 18 Stream F. Set `MAGI_CLI_ENABLED=0` (or
`false` / `no` / `off`) to disable it; the process exits immediately with code 2
without writing any output to stdout.

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
  --permission-mode [default|acceptEdits|bypassPermissions]
                                 Permission mode.  [default: default]
  --resume TEXT                  Resume a session by id.
  --continue / --no-continue     Continue the most-recent session.
  --model TEXT                   Model to use.
  -V, --version                  Print the magi version and exit.
  --help                         Show this message and exit.
```

**Note:** `--version` / `-V` is handled by the launcher (`__main__.py` Layer-0
fast path) BEFORE the agent or any heavy imports run: it prints the package
version and exits 0.

**Note:** `--resume` and `--continue` thread a session id today; true session
rehydration (replaying prior turn history into the engine) is a v1.1 follow-up —
engine rehydration is not yet implemented.

## Stub sub-commands

The following sub-commands resolve but are not yet implemented:

```sh
magi config   # manage configuration (stub)
magi doctor   # environment diagnostics (stub)
magi mcp      # manage MCP connections (stub)
magi auth     # manage authentication (stub)
```
