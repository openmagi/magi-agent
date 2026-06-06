# Configuration

Configuration makes model, workspace, memory, tools, and evidence behavior
explicit.

## Configuration model

The Homebrew install can start in a local diagnostic mode. Real work normally
adds three kinds of configuration:

- model/provider settings for live model calls;
- workspace and tool boundaries for local files and integrations;
- server/auth settings when the HTTP API is exposed beyond trusted localhost.

Prefer environment variables or a local secret manager. Do not store credentials
in prompts or source-controlled Markdown.

## Provider Settings

Magi Agent can run with OpenAI-compatible endpoints and provider-specific
adapters when configured. Keep provider credentials in environment variables or
your local secret manager.

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
export CORE_AGENT_MODEL=...
```

Do not paste provider keys into prompts, docs, or workspace files.

## Runtime Environment

When `MAGI_AGENT_REQUIRE_ENV=1` is enabled, the server requires explicit runtime
environment. The core variables are:

```bash
export BOT_ID=local-bot
export USER_ID=local-user
export GATEWAY_TOKEN="$(openssl rand -hex 24)"
export CORE_AGENT_API_PROXY_URL=http://127.0.0.1:0
export CORE_AGENT_CHAT_PROXY_URL=http://127.0.0.1:0
export CORE_AGENT_REDIS_URL=redis://127.0.0.1:0/0
export CORE_AGENT_MODEL=local-dev
export MAGI_AGENT_REQUIRE_ENV=1
```

For a local-only dashboard, the default fallback values are enough to inspect
the UI and runtime health. For self-hosted access, set a real `GATEWAY_TOKEN`
and avoid exposing the server without authentication.

## Local Server Token

The HTTP dashboard and API use the runtime gateway token. Set it before binding
the service to a network reachable by other users:

```bash
export GATEWAY_TOKEN="$(openssl rand -hex 24)"
magi-agent serve --port 8080
```

Do not reuse a model provider key as a runtime token.

## Workspace

The workspace contains local files, knowledge, memory, artifacts, skills, and
harness rules. Path-sensitive tools stay inside the configured workspace root.

Set `MAGI_AGENT_WORKSPACE` when the HTTP streaming route should run from a
specific working directory:

```bash
export MAGI_AGENT_WORKSPACE=/path/to/workspace
```

## Feature Flags

Experimental surfaces should be explicit. Defaults should avoid surprising
external side effects. Prefer enabling one capability, verifying it, then
opening the next.

Common flags:

```bash
MAGI_STREAMING_CHAT=1
MAGI_FIRST_PARTY_TOOLS_ENABLED=1
MAGI_COMPOSIO_ENABLED=auto
MAGI_RIPGREP_ENABLED=1
MAGI_APPLY_PATCH_ENABLED=1
MAGI_ERROR_RECOVERY_ENABLED=1
MAGI_CONTEXT_COMPACTION_ENABLED=1
```

Treat feature flags as authority grants. Turn on only the surface you intend to
test, then verify health, logs, and evidence before widening access.

## Optional Integrations

Optional connectors such as Composio require their own credentials and explicit
toolkit scope:

```bash
export COMPOSIO_API_KEY=...
export MAGI_COMPOSIO_ENABLED=auto
export MAGI_COMPOSIO_TOOLKITS=github,slack
```

Setting a credential should not be treated as permission to perform external
actions. Pair integration config with approval policy and receipt requirements.
