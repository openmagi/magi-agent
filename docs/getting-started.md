# Getting Started

Install Magi Agent locally, start the runtime, and open the dashboard.

## Install with Homebrew

```bash
brew update
brew install --force-bottle openmagi/tap/magi-agent
```

`--force-bottle` keeps Homebrew on the prebuilt package path. If Homebrew still
tries to build from source, update the tap metadata and reinstall:

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

## Use the CLI

```bash
magi
magi --help
magi -p "Inspect this repository and summarize the runnable surfaces"
magi --output text "Summarize this repository"
magi-agent --help
magi-agent serve --help
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

## First checks

- `magi-agent serve --port 8080` starts without missing configuration errors.
- `http://localhost:8080/dashboard` shows runtime health.
- `magi --help` and `magi-agent --help` print command help.
- A simple prompt streams a response or a clear local configuration error.

