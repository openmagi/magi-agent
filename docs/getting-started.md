# Getting Started

Install Magi Agent locally, start the runtime, and open the dashboard.

## Prerequisites

- macOS or Linux with a normal shell environment.
- Homebrew for the recommended install path.
- A model provider key if you want live model calls.
- Git and `uv` only when developing from source.

Do not paste provider keys into prompts, docs, committed files, or terminal
transcripts you plan to share.

## Install with Homebrew

```bash
brew update
brew install --force-bottle openmagi/tap/magi-agent
```

If Homebrew tries to build from source on macOS, update the tap metadata and
reinstall the prebuilt bottle:

```bash
brew update
brew reinstall openmagi/tap/magi-agent --force-bottle
```

## Start the local runtime and dashboard

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

The dashboard is served by the same Python runtime. It does not require a
separate Node or Next.js process.

The default local path can start without production environment variables. Set
explicit runtime environment only when you want to connect real models,
channels, external tools, or a self-hosted network surface.

## Use the CLI

```bash
magi
magi --help
magi -p "Inspect this repository and summarize the runnable surfaces"
magi --output text "Summarize this repository"
magi-agent --help
magi-agent serve --help
```

`magi` is the terminal work interface. `magi-agent` starts and manages the local
HTTP server and dashboard.

## Configure a model

Magi Agent can run against provider-specific or OpenAI-compatible model paths
when configured. Keep credentials in environment variables:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
export CORE_AGENT_MODEL=...
```

For local development without production wiring, the server falls back to a
local diagnostic configuration. For strict self-hosted startup checks, set:

```bash
export MAGI_AGENT_REQUIRE_ENV=1
```

## Run from source

Use source mode when changing the runtime itself:

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
uv sync --extra dev --extra cli
uv run --extra cli magi --help
uv run magi-agent serve --port 8080
```

Source mode is for contributors. Normal users should prefer the Homebrew
formula so the installed `magi` and `magi-agent` commands match the released
package.

## First checks

- `magi-agent serve --port 8080` starts without missing configuration errors.
- `http://localhost:8080/dashboard` shows runtime health.
- `magi --help` and `magi-agent --help` print command help.
- A simple prompt streams a response or a clear local configuration error.
- `curl http://localhost:8080/healthz` returns an `ok` health payload or a
  specific blocker.

## Agent Handoff Prompt

Paste this into an AI coding agent when you want it to set up a clean local
checkout:

```text
Clone https://github.com/openmagi/magi-agent.git into ./magi-agent.
Read README.md and docs/ before editing.
Use Homebrew for a normal user install, or `uv sync --extra dev --extra cli`
only when changing source.
Start the local server with `magi-agent serve --port 8080`.
Open http://localhost:8080/dashboard and report what loads.
Do not expose secrets. Ask before changing API contracts, auth, billing,
database schema, or production deployment behavior.
```
