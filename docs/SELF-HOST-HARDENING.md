# Self-Host Hardening

Magi Agent can run as a local workbench or as a self-hosted HTTP runtime. The
runtime can read and write its configured workspace, run tools, call provider
APIs, and deliver scheduled work, so treat the HTTP surface as an operator API.

## Minimum Baseline

- Set `MAGI_AGENT_SERVER_TOKEN` to a high-entropy value and rotate it if it is
  pasted into a shared browser profile or chat log.
- Keep provider API keys in `.env`, shell environment variables, Docker
  secrets, or another server-side secret store. Do not write raw provider keys
  into browser-readable files.
- Bind local-only deployments to `127.0.0.1` or keep them behind a private
  network boundary.
- Put remote deployments behind HTTPS and a reverse proxy that enforces
  authentication before traffic reaches the Magi runtime.
- Back up the workspace directory if memory, generated files, cron state, or
  task history matter.
- Run one runtime per trusted workspace. Do not point a shared public app at a
  workspace that contains unrelated private files.

## Runtime API Boundary

All `/v1/app/*` endpoints require `Authorization: Bearer
$MAGI_AGENT_SERVER_TOKEN` when `server.gatewayToken` is configured. These
endpoints intentionally expose local operator controls such as workspace file
inspection, memory search, task stop, cron edits, skill reload, config writes,
and harness-rule edits.

The app API is meant for a trusted owner/operator, not anonymous users. If you
publish the app on the internet, add an external auth layer and rate limits in
front of it.

## Provider And Local Model Keys

Prefer this pattern:

```yaml
llm:
  provider: openai-compatible
  model: llama3.1
  baseUrl: http://host.docker.internal:11434/v1
  apiKey: ${OPENAI_API_KEY}
```

The Magi App config editor writes environment variable references such as
`${OPENAI_API_KEY}`. It reports whether a secret is set, but it does not return
the raw value to the browser.

## Reverse Proxy Checklist

- Terminate TLS at the proxy.
- Require login, mutual TLS, VPN access, or an equivalent access gate.
- Forward only the routes you intend to expose.
- Keep request body limits high enough for chat/file work but low enough for
  your host.
- Disable proxy buffering for SSE streaming routes such as
  `/v1/chat/completions`.
- Log route, status, and timing, but avoid logging request bodies that may
  contain prompts, file content, credentials, or memory.

## Workspace And Memory

The runtime stores durable state under the configured workspace, including:

- `memory/` and `MEMORY.md`
- `core-agent/bg-tasks/`
- `core-agent/crons/`
- generated artifacts and user-created files
- `harness-rules/*.md`
- workspace `skills/`

Back up this directory before moving hosts, changing volume mounts, or running
destructive shell/file tools.

## Desktop Builds

The PWA and Tauri shell are local app surfaces over the same HTTP runtime. They
do not remove the need to protect the runtime token and workspace. Keep desktop
builds pointed at a runtime URL you control.

## Hosted-Service Boundary

The open-source app does not include Magi Cloud auth, billing, fleet
provisioning, managed Telegram/Discord orchestration, hosted credential
brokering, or managed desktop updates. Self-hosting operators are responsible
for those layers when they expose Magi beyond a local machine.
