# Common tasks → command

Status: ✅ Active — the local `magi` CLI runs a real model and first-party tools once a provider key is set (`magi_agent/cli/app.py`, `magi_agent/tools/catalog.py`).

A task-oriented index: find what you want to do, copy the command, follow the
linked doc. Rows tagged 🚧 are default-off / shadow surfaces — they record intent
rather than performing the live side effect (see
[what-works-today.md](what-works-today.md)).

| I want to… | Command | Doc |
|---|---|---|
| Ask a question (no tools, no file changes) | `magi -p "explain this error message"` | [cli/magi.md](cli/magi.md) |
| Work interactively with the agent in a repo | `magi` (then approve tool calls as prompted) | [cli/magi.md](cli/magi.md) |
| Let the agent read & edit files without approving every edit | `magi -p --permission-mode acceptEdits "fix the type error in utils.py"` | [cli/magi.md](cli/magi.md) |
| Plan only — read-only, no mutations | start in plan mode (the agent uses `EnterPlanMode`; only read tools run) | [tools.md](tools.md) |
| Run with no approval prompts at all (careful) | `magi -p --permission-mode bypassPermissions "..."` | [cli/magi.md](cli/magi.md) |
| Get a single JSON result for automation | `magi -p "summarise the README" --output json` | [cli/magi.md](cli/magi.md) |
| Stream NDJSON frames for a live pipeline | `magi -p "count to 3" --output stream-json` | [cli/magi.md](cli/magi.md) |
| Include raw engine/token events in the stream | `magi -p "..." --output stream-json --include-partial-messages` | [cli/magi.md](cli/magi.md) |
| Open the local dashboard in a browser | `magi-agent serve --port 8080` | [quickstart.md](quickstart.md) |
| See what's live vs shadow vs planned | — | [what-works-today.md](what-works-today.md) |
| Send a message to Telegram / Discord | 🚧 default-off — adapters record send intent only, no live delivery | [channels.md](channels.md) |
| Apply a recipe pack as live runtime policy | 🚧 not available — recipes compile to metadata snapshots; no execution engine consumes them | [recipes.md](recipes.md) |
| Use evidence / approval enforcement to block bad output | 🚧 observe-only — boundaries record verdicts but do not block today | [what-works-today.md](what-works-today.md) |

## Notes

- **Output modes.** `--output` takes `text` (default), `json` (one result
  object), or `stream-json` (one NDJSON frame per line). It only affects
  headless (`-p` / `--print`) runs; the interactive TUI uses its own rendering.
- **Permission modes.** `--permission-mode` is one of `default` (ask),
  `acceptEdits` (auto-allow file edits), or `bypassPermissions` (no prompts).
  First-party tools are on by default; dangerous tools (`Bash`, `TestRun`) still
  require approval under `default`.
- **Provider key required.** The agent needs a configured provider key
  (Anthropic / OpenAI / Gemini / Fireworks) before any of the above will call a
  model. See [getting-started.md](getting-started.md).
