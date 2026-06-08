# Troubleshooting

Debug Magi Agent by locating the failed runtime boundary.

Most failures belong to a boundary: missing evidence, blocked approval, unsupported claim, stale context, tool denial, projection rejection, or install documentation drift.

## Common boundary failures

If output is blocked, identify the boundary that rejected it. A source-verified answer may be missing a source receipt. A Slack draft may lack approval. A memory write may contain unsupported claims. A tool call may be denied by policy.

Treat the failure as a runtime state problem first, not a prompt wording problem.

- Missing evidence: inspect source receipts and claim links.
- Blocked approval: confirm the action digest matches the approval receipt.
- Unsupported claim: repair, downgrade, abstain, or block.
- Stale context: rebuild model-visible context from committed state.
- Tool denial: inspect policy snapshot and ToolHost boundary logs.
- Projection rejection: remove private paths, raw output, secrets, and unsupported claims.

## Install docs look too simple

If docs claim a package-manager, Homebrew, shell-pipe, create-app, or one-command runtime path is currently available, verify it against package entrypoints and tests first. Today, the normal user path is Homebrew plus `magi` for CLI work and `magi-agent serve --port 8080` for the dashboard; a source checkout runs the same commands through `uv` (for example `uv run --extra cli magi doctor`), not npm.

Keep source checkout and local Homebrew instructions separate so users know
which environment they are operating.

## Local CLI will not run

The local `magi` CLI needs exactly ONE provider key — not BOT_ID, GATEWAY_TOKEN, or any service URL. Set one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), or `FIREWORKS_API_KEY`, or create a `~/.magi/config.toml`. Run `magi doctor` (or `uv run --extra cli magi doctor` from a source checkout) to diagnose configuration: it checks that a provider config is resolvable, that `litellm` is importable, that the config path is readable, and that the working directory is writable.

### No provider configured

If `magi` launches but answers with a model-free stub, no provider key was found. `magi doctor` reports the provider config as unresolved. Fix it by exporting one provider key, or by setting `[model].provider` + `[model].api_key` in `~/.magi/config.toml` (override the path with `MAGI_CONFIG`).

### litellm not installed

If a provider key is set but the turn returns an install hint instead of a model answer, the optional `litellm` dependency is missing (the CLI raises a provider-dependency error and stays usable on the stub runner). `magi doctor` reports `litellm` as not importable. Reinstall `magi-agent` so its default runtime dependencies are present, or from a source checkout install the CLI extra: `uv run --extra cli magi ...` (the `cli` extra pulls the provider runtime in).

### Recipe phase routing

Local CLI and dashboard runs consume the first-party recipe materializer's phase-routing plan by default. The route is local-only: it can select the active recipe phase, add route context to the runner state, and narrow already-available local tools for read/research phases, but it does not grant production writes or attach external integrations. Set `MAGI_RUNNER_POLICY_ROUTING_ENABLED=0` to opt out while debugging runner behavior.

## Agent gives wrong or unsupported answers

Verify that evidence contracts are active for your task type. Research tasks should have SourceInspection requirements; coding tasks should have TestRun and GitDiff requirements. Check the evidence ledger for missing or failed evidence records. If enforcement is set to audit, the agent logs issues but does not block — switch to block_final_answer for stricter enforcement.
