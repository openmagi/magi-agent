# Deployment

Status: ✅ Active — local self-host runs today; enforcement boundaries are default-off (shadow).

This page covers running Magi Agent yourself. The local CLI and HTTP server run a
real model and first-party tools today (see [what works today](/docs/what-works-today));
what ships default-off is the enforcement/governance layer, external channel
delivery, and external integrations.

## Local (Homebrew)

```bash
brew install --force-bottle openmagi/tap/magi-agent
export ANTHROPIC_API_KEY=...        # or any one supported provider key
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

`magi-agent serve` runs the local HTTP API and dashboard. The same provider key
also powers the `magi` CLI. See [configuration](/docs/configuration) and the
[environment variable reference](/docs/env-reference).

## Container

The repository ships a `Dockerfile` that installs the package and runs
`python -m magi_agent` on port 8080.

```bash
docker build -t magi-agent .
docker run --rm -p 8080:8080 -e ANTHROPIC_API_KEY=... magi-agent
```

Pass the provider key (or mount a config file) as the only required configuration
for a local run. The hosted runtime's `BOT_ID` / `GATEWAY_TOKEN` / `CORE_AGENT_*`
/ service-URL / Redis variables are not required for a standalone container.

## Self-host posture

- Keep provider and tool credentials out of source control; supply them via
  environment or a mounted `~/.magi/config.toml`.
- Tool execution is gated by permission modes (`default` / `acceptEdits` /
  `bypassPermissions`); choose the mode that matches how much autonomy you want.
- Keep mutation surfaces least-privilege and require approval for external side
  effects.

## Default-off rollout posture

Enforcement boundaries and external delivery/integrations start default-off (see
[default-off gates](/docs/default-off-gates)). This governs *enforcement*, not the
agent's ability to run work. Enable additional authority only after contract
tests, replay, shadow/canary evidence, security review, and a rollback plan are in
place.

## Managed hosting

Open Magi Cloud remains optional managed hosting for teams that do not want to
operate the runtime themselves.
