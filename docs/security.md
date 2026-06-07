# Security

Status: ✅ Active — boundaries, default-off authority, and projection control are the security model.

Security starts with runtime boundaries, default-off authority, and projection control.

Keep secrets out of model-visible context, make tools least-privilege, require approvals for side effects, and audit every governed transition.

## Threat model

Treat these as the primary adversarial surfaces when running the agent:

- **Prompt injection.** Untrusted content (web pages, files, tool output) can try
  to steer the model into unintended tool calls or data exfiltration. Mitigations:
  permission prompts on tool use, least-privilege tool scope, and governed
  projection that withholds secrets/private paths from output.
- **Tool side effects.** First-party tools include destructive capabilities
  (`Bash`, file writes/patches). Run in `default` permission mode (approve each
  tool) for untrusted tasks; reserve `acceptEdits`/`bypassPermissions` for trusted
  contexts.
- **Hooks that execute.** Hook manifests can declare `command` and `http` handler
  types that run external processes or make network calls. Only install hooks/
  plugins you trust; review their manifests. See [plugin manifest](/docs/plugin-manifest).
- **Secret exfiltration.** Channel adapters and the evidence ledger redact common
  secret patterns and private paths, but do not place secrets in prompts, memory,
  or doc examples in the first place.

## Secret hygiene

Secrets should live in environment variables or a deployment secret manager, not prompts, docs examples, model-visible context, memory writes, or output projections.

Governed output projection must exclude raw tool output, hidden reasoning, private paths, secrets, and unsupported claims.

## Least privilege and default-off authority

Expose only the tools and integration scopes required by the workflow. Use default-off settings for new live authority, especially model providers, tool execution, MCP, browser control, workspace mutation, and external delivery.

Runtime-enforced control should be auditable through receipts and append-only audit ledger entries.

## Security checklist

Before enabling additional external authority, review this checklist.

- API keys are stored in environment variables or `~/.magi/config.toml` (kept out of source control), never committed to git.
- Default-off boundaries remain disabled unless you have explicitly reviewed and enabled them.
- Dangerous tools (Bash, TestRun) require approval before execution.
- Authority flags and integration scopes stay least-privilege.
- Evidence enforcement is set to audit mode by default. Switch to block_final_answer only after testing.
- Memory writes are blocked. Read-only memory adapters are the only supported mode.
