# Troubleshooting

## Homebrew tries to build from source

Use the bottle path:

```bash
brew update
brew reinstall openmagi/tap/magi-agent --force-bottle
```

If Homebrew still attempts a source build, the tap may need refreshed bottle
metadata for your macOS/architecture combination.

## Command not found

Verify Homebrew installed the package and that Homebrew's bin directory is on
your `PATH`:

```bash
brew list openmagi/tap/magi-agent
which magi
which magi-agent
```

## Dashboard says unauthorized

Set a gateway token or use the local token shown by your runtime configuration:

```bash
export GATEWAY_TOKEN="$(openssl rand -hex 24)"
magi-agent serve --port 8080
```

Then provide the same token to the local dashboard if prompted.

## Port 8080 is already in use

Start on another port:

```bash
magi-agent serve --port 8090
open http://localhost:8090/dashboard
```

## Healthz is not ok

Check the health payload before debugging model behavior:

```bash
curl http://localhost:8080/healthz
```

Common causes:

- required environment variables are missing because `MAGI_AGENT_REQUIRE_ENV=1`
  is set;
- a provider key or model setting is invalid;
- an optional feature flag enables a surface without its dependency;
- the workspace path is not accessible.

## The agent says work is still running, then stops

That is not a valid completion. The runtime should either finish the requested
work, show a real background job/receipt, or state the concrete blocker.

## A tool is unavailable

Check:

- the feature flag or configuration that enables the tool;
- required credentials;
- workspace path permissions;
- approval policy;
- runtime health;
- whether the tool is intentionally read-only in the current mode.

## Composio is inactive

Run:

```bash
magi doctor
magi auth composio status
```

Then verify:

- `COMPOSIO_API_KEY` is set;
- `MAGI_COMPOSIO_ENABLED` is `auto` or `on`;
- `MAGI_COMPOSIO_TOOLKITS` includes the toolkit you intend to use.

## Stream-json output is too noisy

Use text output for normal terminal work:

```bash
magi --output text "Summarize this repository"
```

Use `stream-json` when an API client needs incremental events:

```bash
magi --output stream-json --include-partial-messages "Run a visible task"
```
