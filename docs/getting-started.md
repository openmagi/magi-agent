# Getting Started

Install Magi Agent locally, open the dashboard, or fall back to a source checkout for development.

Install with Homebrew, run `magi-agent serve --port 8080`, and open the local dashboard. Source checkout remains available for development.

## User install target

Magi Agent is available as a local CLI and HTTP dashboard install through Homebrew.

The intended Magi Agent user is a local-agent user, not a repo contributor. Install the runtime, start the local HTTP API, then finish first-run setup in the browser.

Use the source checkout only when developing the runtime itself.

- First-run setup belongs in the local web UI where possible: provider/model selection, API key entry into local secret storage, workspace path selection, default recipe/harness selection, and first agent/chat creation.
- The installed commands are `magi` for CLI work and `magi-agent` for serving the local HTTP API/dashboard.
- Open Magi Cloud remains optional managed hosting, not required for the local app flow.

### Homebrew install

```
brew install --force-bottle openmagi/tap/magi-agent
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

## Current source fallback

Clone the canonical source repository at https://github.com/openmagi/magi-agent.

For source checkout development, use `npm run magi -- ...` as a development fallback only.

Use this fallback to create starter files, verify the checkout, and start the local docs/development server before changing runtime behavior.

- `init` creates `magi-agent.yaml`, `.magi-agent/env.local`, and `.magi-agent/workspace/` with placeholders only.
- `doctor` checks local files without contacting OpenMagi Cloud, providers, Kubernetes, databases, auth, or billing.
- `start` runs the local docs/development server from the checkout; it does not activate cloud hosting, deploy infrastructure, or live runtime authority.
- Keep provider keys and service credentials outside source control.

### Source fallback

```
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
npm install
npm run magi -- init
npm run magi -- doctor
npm run magi -- start
```

## Packaging follow-up

Homebrew installation exists. The remaining packaging work is improving the local dashboard onboarding, workspace volume management, local secret storage, and upgrade/rollback behavior.

Follow-up plan: `docs/superpowers/plans/2026-05-28-magi-agent-homebrew-local-app-install.md`.

- The standalone CLI package has tested `magi` and `magi-agent` entrypoints.
- Docker image/compose bundle publishing for multi-service local stacks remains separate from the Homebrew single-runtime path.
- Local dashboard onboarding, local secret storage, workspace volume management, and upgrade/rollback behavior remain follow-up work.

## Python ADK runtime status

The Python ADK runtime is the forward substrate for Magi Agent runtime contracts, but current live authority is still gated. Public docs should describe ADK as the substrate and Magi Agent as the governing runtime contract without implying production traffic has moved to ADK by default.

Use ADK docs here to understand the architecture: policy snapshot, context projection, ToolHost, source ledger, validators, repair policy, governed output projection, and audit.

- Live model, tool, provider, MCP, browser, workspace, and production routing authority remain default-off until explicit rollout gates pass.
- Docs may describe the contract and migration direction, but must not imply ungated runtime activation.

## Managed-hosting CLI boundary

`packages/openmagi` is currently an optional managed-hosting CLI with `openmagi cloud <login|run|chat>`. It does not install or start the local OSS runtime, so local docs should keep it separate from Magi Agent source setup.
