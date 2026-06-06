# Security

Security starts with runtime boundaries, default-off authority, and projection control.

Keep secrets out of model-visible context, make tools least-privilege, require approvals for side effects, and audit every governed transition.

## Secret hygiene

Secrets should live in environment variables or a deployment secret manager, not prompts, docs examples, model-visible context, memory writes, or output projections.

Governed output projection must exclude raw tool output, hidden reasoning, private paths, secrets, and unsupported claims.

## Least privilege and default-off authority

Expose only the tools and integration scopes required by the workflow. Use default-off settings for new live authority, especially model providers, tool execution, MCP, browser control, workspace mutation, and external delivery.

Runtime-enforced control should be auditable through receipts and append-only audit ledger entries.

## Security checklist

Before enabling any production authority, review this checklist.

- API keys are stored in .magi-agent/env.local or environment variables, never committed to git.
- Default-off boundaries remain disabled unless you have explicitly reviewed and enabled them.
- Dangerous tools (Bash, TestRun) require approval before execution.
- Authority flags in RuntimeConfig are all Literal[False] — production authority is a separate rollout step.
- Evidence enforcement is set to audit mode by default. Switch to block_final_answer only after testing.
- Memory writes are blocked. Read-only memory adapters are the only supported mode.
