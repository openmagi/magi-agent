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

If docs claim a package-manager, Homebrew, shell-pipe, create-app, or one-command runtime path is currently available, verify it against package entrypoints and tests first. Today, the normal user path is Homebrew plus `magi-agent serve --port 8080`; the source checkout path remains source clone, npm install, npm run magi -- init, npm run magi -- doctor, and npm run magi -- start for development.

Keep source checkout, local Homebrew, and optional managed hosting language separate so users know which environment they are operating.

## Agent will not start

Check that all required environment variables are set: BOT_ID, USER_ID, GATEWAY_TOKEN, CORE_AGENT_MODEL, and the three service URLs. Run npm run magi -- doctor to diagnose missing configuration. If using the source checkout, ensure npm install completed without errors.

## Agent gives wrong or unsupported answers

Verify that evidence contracts are active for your task type. Research tasks should have SourceInspection requirements; coding tasks should have TestRun and GitDiff requirements. Check the evidence ledger for missing or failed evidence records. If enforcement is set to audit, the agent logs issues but does not block — switch to block_final_answer for stricter enforcement.
