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

## Packaging follow-up

Homebrew installation exists. The remaining packaging work is improving the local dashboard onboarding, workspace volume management, local secret storage, and upgrade/rollback behavior.

- The standalone CLI package has tested `magi` and `magi-agent` entrypoints.
- Docker image/compose bundle publishing for multi-service local stacks remains separate from the Homebrew single-runtime path.
- Local dashboard onboarding, local secret storage, workspace volume management, and upgrade/rollback behavior remain follow-up work.

## Python ADK runtime status

The Python ADK runtime is the forward substrate for Magi Agent runtime contracts, but current live authority is still gated. Public docs should describe ADK as the substrate and Magi Agent as the governing runtime contract without implying production traffic has moved to ADK by default.

Use ADK docs here to understand the architecture: policy snapshot, context projection, ToolHost, source ledger, validators, repair policy, governed output projection, and audit.

- The LOCAL `magi` CLI runs a real model plus first-party local tools (file read/write/edit, patch, Bash) once a provider key is set, behind permission-mode prompts. This is not gated off.
- The default-off authority refers to external delivery/integrations (MCP,
  browser, channel) and the enforcement boundary layer — not to whether the
  local agent can run a task.
- Docs may describe the contract and rollout direction, but must not imply
  ungated external authority.

## Cloud CLI boundary

`packages/openmagi` is a separate cloud CLI surface. It does not install or start
the local OSS runtime, so local docs should keep it separate from Magi Agent
source setup.
