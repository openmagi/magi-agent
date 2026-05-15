---
name: slack-integration
description: Use when the user asks about their Slack messages, channel activity, or wants to know what's happening in their Slack workspace. Fetches Slack message data via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Slack Integration

## Overview

Access the user's Slack workspace to retrieve recent messages across channels, catch up on conversations, and identify messages that may need attention. Data is fetched through the chat-proxy integration layer using the user's connected Slack account.

## Commands

### Fetch Slack Messages

```bash
integration.sh slack/messages
```

**Response format:**

```json
{
  "messages": [
    {
      "channel": "#engineering",
      "text": "Deployed v2.3.1 to production. All checks passing.",
      "from": "alice",
      "timestamp": "2026-03-05T11:45:00Z"
    },
    {
      "channel": "#general",
      "text": "Team lunch moved to Thursday this week",
      "from": "bob",
      "timestamp": "2026-03-05T10:30:00Z"
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `channel` | string | Channel name where the message was posted |
| `text` | string | Message content |
| `from` | string | Username of the message sender |
| `timestamp` | string | ISO 8601 timestamp of the message |

## Use Cases

- **Catch up**: "What's happening in Slack?" — summarize recent messages across channels
- **Channel check**: "Anything new in #engineering?" — filter messages by channel
- **Mentions and replies**: "Did anyone message me?" — look for messages relevant to the user
- **Team pulse**: "What's the team talking about?" — provide a high-level summary of active conversations
- **Context before meetings**: Before a meeting, check relevant channels for context that might be discussed

## Guidelines

- **Group by channel**: When summarizing multiple messages, group them by channel for clarity.
- **Summarize, don't relay**: For active channels with many messages, provide a summary rather than listing every message.
- **When NOT to bother the user**: Do not proactively report Slack activity unless the user has asked for updates. Slack channels can be noisy.
- **Natural presentation**: Present conversationally:
  - "In #engineering, Alice mentioned the v2.3.1 deploy is live. In #general, Bob moved team lunch to Thursday."
  - "Things are quiet in Slack today — just a couple of messages in #general."
- **Privacy**: Slack messages may contain sensitive team discussions. Do not store or log message content beyond the immediate response.
- **Tone awareness**: When relaying messages, maintain the original meaning but present them naturally. Do not alter the intent of someone's message.
- **Errors**: If the integration returns an error, let the user know their Slack account may not be connected and suggest checking their integration settings.
