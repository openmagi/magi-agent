# Troubleshooting

## Homebrew tries to build from source

Use the bottle path:

```bash
brew update
brew reinstall openmagi/tap/magi-agent --force-bottle
```

If Homebrew still attempts a source build, the tap may need refreshed bottle
metadata for your macOS/architecture combination.

## Dashboard says unauthorized

Set a local server token or use the token shown by your local runtime
configuration:

```bash
export MAGI_AGENT_SERVER_TOKEN="$(openssl rand -hex 24)"
magi-agent serve --port 8080
```

Then provide the same token to the local dashboard if prompted.

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

