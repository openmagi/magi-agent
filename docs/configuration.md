# Configuration

Configuration makes model, workspace, memory, tools, and evidence behavior
explicit.

## Provider settings

Magi Agent can run with OpenAI-compatible endpoints and provider-specific
adapters when configured. Keep provider credentials in environment variables or
your local secret manager.

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GOOGLE_GENERATIVE_AI_API_KEY=...
```

Do not paste provider keys into prompts, docs, or workspace files.

## Local server token

If you expose the local server outside a trusted localhost process, set a
gateway token:

```bash
export MAGI_AGENT_SERVER_TOKEN="$(openssl rand -hex 24)"
magi-agent serve --port 8080
```

Use this token for local dashboard/API access. Do not reuse a model provider key
as a runtime token.

## Workspace

The workspace contains local files, knowledge, memory, artifacts, skills, and
harness rules. Path-sensitive tools stay inside the configured workspace root.

## Feature flags

Experimental surfaces should be explicit. Defaults should avoid surprising
external side effects. Prefer enabling one capability, verifying it, then
opening the next.

