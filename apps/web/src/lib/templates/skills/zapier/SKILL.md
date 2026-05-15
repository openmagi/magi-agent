---
name: zapier
description: Access 8,000+ apps through Zapier MCP. Use when users ask to interact with external services like Gmail, Slack, Sheets, Salesforce, Shopify, HubSpot, Trello, Asana, or any app connected in their Zapier account.
---

# Zapier MCP — 8,000+ App Integrations

Access thousands of apps through the user's Zapier account.

## When to Use
- User asks to send an email, message, or notification via a third-party app
- User wants to create/update records in CRM, project management, or other SaaS
- User asks to interact with any app they've connected in Zapier
- Any automation or cross-app workflow request

## API Access

User-connected integration — requires Zapier MCP URL in Settings. All endpoints via `integration.sh`.

Response format: `{ "data": {...}, "synced_at": "..." }`

---

## 1. List Available Tools (discover what the user can do)

```
integration.sh "zapier/list"
```

Response: list of tools with `name`, `description`, and `inputSchema` (parameters).

**Always call this first** to discover what apps/actions the user has available. The tools depend on what they configured in their Zapier account.

---

## 2. Call a Tool (execute an action)

```
integration.sh "zapier/call" --post '{"tool": "tool_name_from_list", "args": {"param1": "value1", "param2": "value2"}}'
```

Parameters:
- `tool` (required) — exact tool name from the list response
- `args` (required) — object matching the tool's `inputSchema`

Response: tool execution result from Zapier

---

## Workflow Example

User: "Send a Slack message to #general saying the report is ready"

1. `integration.sh "zapier/list"` — check available tools
2. Find the Slack send-message tool in the list
3. `integration.sh "zapier/call" --post '{"tool": "slack_send_channel_message", "args": {"channel": "#general", "message": "The report is ready"}}'`
4. Confirm to user that the message was sent

---

## Important Notes

- **Always list tools first** — available tools vary per user
- **Confirm destructive actions** — ask before sending messages, creating records, or making payments
- **Each tool call costs 2 Zapier tasks** — the user's Zapier plan limits apply
- Tool names and schemas come from Zapier, not from this skill doc — always check `zapier/list`
