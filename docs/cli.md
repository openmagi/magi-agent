# CLI

Document the installed Magi Agent CLI, local dashboard command, and source checkout fallback.

Use `magi` for CLI work and `magi-agent serve --port 8080` for the local HTTP API and dashboard.

## Installed CLI commands

Homebrew installs both `magi` and `magi-agent`.

`magi` is the headless and interactive CLI. `magi-agent serve --port 8080` starts the local HTTP API and dashboard.

First-run provider/model/API-key/workspace/recipe/agent setup should happen in the local web UI where possible.

### CLI command surface

```
brew install --force-bottle openmagi/tap/magi-agent
magi --help
magi-agent --help
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

## Current source fallback

Magi Agent source fallback is currently a source-checkout script exposed through `npm run magi -- ...`.

Use source checkout commands only when developing the runtime itself.

Do not present the source docs/development server as cloud hosting or production authority.

- Use source-checkout commands only as the current fallback.
- Do not present the managed-hosting CLI as the local OSS runtime installer.
- Do not use private tokens in docs examples.

## Local source commands

`init` creates starter files, `doctor` checks the checkout, and `start` runs the local docs/development server. These are source fallback only.

`start` does not contact OpenMagi Cloud, providers, Kubernetes, databases, auth, or billing, and it does not enable production runtime authority.

### Source commands

```
npm run magi -- init
npm run magi -- init --force
npm run magi -- doctor
npm run magi -- start
npm run magi -- start --port 3010
npm run magi -- --help
```
