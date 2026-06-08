# Upgrading

Open Magi Agent is in early beta; interfaces may change between releases. Check the
[changelog](/docs/changelog) and [GitHub Releases](https://github.com/openmagi/magi-agent/releases)
before upgrading.

## Homebrew (normal user path)

```bash
brew update
brew upgrade openmagi/tap/magi-agent
```

If a build is attempted from source instead of the prebuilt bottle, reinstall the
bottle explicitly:

```bash
brew reinstall openmagi/tap/magi-agent --force-bottle
```

Verify the upgrade and your environment:

```bash
magi --version
magi doctor
```

## Source checkout (contributors)

```bash
git pull
uv sync --extra dev --extra cli
uv run --extra dev pytest -q
```

## Your configuration carries over

Provider configuration lives outside the install:

- Environment variables (e.g. `ANTHROPIC_API_KEY`), or
- `~/.magi/config.toml` (or the path in `MAGI_CONFIG`).

Upgrading the package does not modify these, so your provider/model selection is
preserved. After upgrading, run `magi doctor` to confirm the provider, the
`litellm` dependency, and the config file are all detected. See
[configuration](/docs/configuration) and the
[environment variable reference](/docs/env-reference) for details.

## If something breaks

See [troubleshooting](/docs/troubleshooting). Common post-upgrade issues are a
missing `litellm` dependency (reinstall the CLI extra) or a provider key that is
no longer in the environment (`magi doctor` reports both).
