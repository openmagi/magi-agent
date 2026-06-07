# Getting Started

Status: ✅ Active — Homebrew install plus one provider key gives a real local model and first-party tools today.

Install Magi Agent locally, open the dashboard, or fall back to a source checkout for development.

Install with Homebrew, run `magi-agent serve --port 8080`, and open the local dashboard. Source checkout remains available for development.

## User install target

Magi Agent is available as a local CLI and HTTP dashboard install through Homebrew.

The intended Magi Agent user is a local-agent user, not a repo contributor. Install the runtime, set one provider key (or create `~/.magi/config.toml`), and run the `magi` CLI or start the local HTTP API.

Use the source checkout only when developing the runtime itself.

- To configure a provider today, set ONE provider env key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, or `FIREWORKS_API_KEY`), or create `~/.magi/config.toml` (override the path with `MAGI_CONFIG`). Either one builds a real model-backed runner; with neither, the CLI falls back to a model-free stub.
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
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / FIREWORKS_API_KEY
magi -p "What is 2+2?"
```

A pure question answers directly. For a tool-using task, run the interactive `magi` TUI and approve the tool, or pass `--permission-mode acceptEdits` headlessly (otherwise `default` mode asks per tool and cannot auto-resolve without an input stream).

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
