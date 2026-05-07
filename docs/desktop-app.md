# Magi Desktop App

Magi has two open-source desktop paths:

- install the self-hosted `/app` workbench as a browser PWA
- build the included Tauri shell in `apps/desktop`

Both paths use the same local runtime. Provider credentials, local LLM server
settings, workspace path, skills, harness rules, memory, crons, and tools stay
in the runtime and `magi-agent.yaml`; the app stores only the runtime URL,
server token, session key, and optional per-turn model override.

## Run The Local Runtime

```bash
export MAGI_AGENT_SERVER_TOKEN=$(openssl rand -hex 24)
npx tsx src/cli/index.ts serve --port 8080
```

Then open:

```text
http://localhost:8080/app
```

## Build The Native Shell

Install Tauri's platform prerequisites for your OS, then run:

```bash
npm --prefix apps/desktop install
npm --prefix apps/desktop run check
npm run desktop:dev
```

To build an installer/package:

```bash
npm run desktop:build
```

The default desktop shell loads:

```text
http://127.0.0.1:8080/app
```

Change `apps/desktop/src-tauri/tauri.conf.json` if you want a different local
port or self-hosted URL.

## Local LLMs

The desktop app is provider-agnostic because it talks only to the local Magi
runtime. To use a local model server, configure the runtime with the
OpenAI-compatible adapter:

```yaml
llm:
  provider: openai-compatible
  model: llama3.1
  baseUrl: http://127.0.0.1:11434/v1
  # apiKey: ${LOCAL_LLM_API_KEY}  # optional

server:
  gatewayToken: ${MAGI_AGENT_SERVER_TOKEN}
```

This works with servers that expose OpenAI-style `/v1/chat/completions`
streaming, such as Ollama, LM Studio, vLLM, llama.cpp server variants, LiteLLM,
or your own gateway.

## Packaging Boundary

The open-source desktop package is only a local workbench wrapper. It should
not include:

- hosted Magi Cloud auth, billing, entitlements, or customer data contracts
- private download, signing, auto-update, or telemetry infrastructure
- managed social-browser credential broker flows
- production admin or operator backoffice routes

Signed releases, notarization, auto-update, and managed desktop telemetry are
separate hosted-product concerns. The OSS shell stays buildable by users who
want their own local Codex-like Magi app with their own model provider.
