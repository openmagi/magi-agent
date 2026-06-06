# Reference

## Commands

```bash
magi
magi --help
magi --version
magi --output text "Summarize this repository"
magi --mode plan "Inspect and propose"
magi --mode act "Apply the approved change"
magi-agent --help
magi-agent serve --port 8080
```

## Common environment variables

```bash
GATEWAY_TOKEN=...
MAGI_AGENT_REQUIRE_ENV=1
MAGI_AGENT_WORKSPACE=...
MAGI_STREAMING_CHAT=1
MAGI_FIRST_PARTY_TOOLS_ENABLED=1
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
CORE_AGENT_MODEL=...
COMPOSIO_API_KEY=...
MAGI_COMPOSIO_ENABLED=auto
```

## Package names

- Product/runtime name: `Magi Agent`
- Python package/import name: `magi_agent`
- CLI commands: `magi`, `magi-agent`
- Repository: `openmagi/magi-agent`

Avoid legacy or internal names in public docs and new code.

## Local URLs

```text
http://localhost:8080/dashboard
http://localhost:8080/health
http://localhost:8080/healthz
```

## Output Formats

```text
text
json
stream-json
```

`stream-json` is the CLI-friendly streaming format. HTTP clients should use the
SSE routes documented in [API](api.md) and [Streaming events](streaming-events.md).

## Runtime Modes

```text
plan
act
```

## Permission Modes

```text
default
acceptEdits
bypassPermissions
```

## First-party documentation

- [Recipes](recipes.md)
- [Harnesses](harnesses.md)
- [First-party packs](first-party-packs.md)
- [Streaming events](streaming-events.md)
