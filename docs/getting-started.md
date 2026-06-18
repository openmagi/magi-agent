# Getting Started

Install Magi Agent locally, open the dashboard, or fall back to a source checkout for development.

Install with Homebrew, run `magi-agent serve --port 8080`, and open the local dashboard. Source checkout remains available for development.

## User install target

Magi Agent is available as a local CLI and HTTP dashboard install through Homebrew.

The intended Magi Agent user is a local-agent user, not a repo contributor. Install the runtime, set one provider key (or create `~/.magi/config.toml`), and run the `magi` CLI or start the local HTTP API.

Use the source checkout only when developing the runtime itself.

- To configure a provider today, set ONE provider env key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `FIREWORKS_API_KEY`, or `OPENROUTER_API_KEY`), or create `~/.magi/config.toml` (override the path with `MAGI_CONFIG`). Either one builds a real model-backed runner; with neither, the CLI falls back to a model-free stub.
- The installed commands are `magi` for CLI work and `magi-agent` for serving the local HTTP API/dashboard.
- A local install does not require a cloud account.

### Homebrew install

```
brew install --force-bottle openmagi/tap/magi-agent
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

Then set one provider key and run your first task:

```
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / FIREWORKS_API_KEY / OPENROUTER_API_KEY
magi -p "What is 2+2?"
```

A pure question answers directly. For a tool-using task, the local CLI defaults to `bypassPermissions` when `--permission-mode` is omitted, so tools can execute without approval prompts. Pass `--permission-mode default` when you want per-tool approval prompts.

## Runtime profiles

A profile is a named bundle of runtime feature flags selected by `MAGI_RUNTIME_PROFILE`. A Homebrew install seeds `~/.magi/profile.env` with the **`full`** profile, which turns on the standard governance and harness modules. The lean profiles (`safe`, `eval`, `minimal`, `conservative`, `off`) keep the runtime at its conservative code defaults.

Pick a profile two ways:

- **Per run** — set the env var for a single invocation:

  ```
  MAGI_RUNTIME_PROFILE=lab magi -p "..."
  MAGI_RUNTIME_PROFILE=lab magi-agent serve --port 8080
  ```

- **Persisted** — put it in `~/.magi/profile.env`. The CLI loads this file at startup and `setdefault`s each `MAGI_*` line, so an explicit shell env var still wins. Precedence: **shell env > `profile.env` > code default**.

### `lab` — experimental dogfood profile

`MAGI_RUNTIME_PROFILE=lab` is the `full` profile plus the complete experimental flag set (extra verification gates, the learning loop, memory, deep web research, document-coverage in non-blocking `advisory` mode, and more). It is a single opt-in switch — you do not list the individual flags. For a clean `lab` profile, make `~/.magi/profile.env` a single line:

```
MAGI_RUNTIME_PROFILE=lab
```

Notes:

- **Don't just flip the profile line in the Homebrew-seeded `full` file.** That file also pins many flags explicitly, and an explicit line wins over `lab`'s own choice for that flag (for example, document coverage `block` in `full` vs `advisory` in `lab`). Replace the whole file with the single `MAGI_RUNTIME_PROFILE=lab` line to get a pure `lab` profile.
- **Walk back any single feature** with `MAGI_<FLAG>=0` (a shell env var or a `profile.env` line) — `setdefault` semantics mean your explicit `0` wins.
- **Updates keep your choice.** `brew upgrade` never overwrites an existing `~/.magi/profile.env`, so once you set `lab` it persists across updates; the upgrade only swaps the runtime. Delete the file (or run once with `MAGI_RUNTIME_PROFILE=safe`) to fall back to the conservative code defaults.

## Source checkout (contributors only)

Clone the canonical source repository at https://github.com/openmagi/magi-agent.

Use the source checkout only when developing the runtime itself; normal users should install with Homebrew.

- Keep provider keys and service credentials outside source control.

```
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
uv sync --extra dev --extra cli
uv run --extra cli magi --help
```

## Runtime contract

The Python ADK runtime is the substrate for Magi Agent runtime contracts. Use the
runtime docs to understand policy snapshots, context projection, ToolHost, source
ledgers, validators, repair policy, governed output projection, and audit.

The local `magi` CLI runs a real model plus first-party local tools once a
provider key is set, behind permission-mode prompts. External delivery,
integrations, and high-authority mutations require explicit configuration,
credentials, and approval policy.
