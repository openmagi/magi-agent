---
name: google-gmail
description: Use when the user asks about their email inbox, unread messages, important emails, or wants a summary of recent messages. Fetches Gmail data via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Gmail Integration

## Overview

Access the user's Gmail inbox to check for unread messages, summarize recent emails, and identify important or time-sensitive correspondence. Data is fetched through the chat-proxy integration layer using the user's connected Google account.

## Commands

### Fetch Gmail Messages

```bash
integration.sh google/gmail
```

**Response format:**

```json
{
  "messages": [
    {
      "from": "alice@example.com",
      "subject": "Q1 Report Draft",
      "snippet": "Hey, attached is the draft for the Q1 report. Let me know if...",
      "date": "2026-03-05T08:32:00Z",
      "unread": true
    },
    {
      "from": "notifications@github.com",
      "subject": "[openmagi/core] PR #142 merged",
      "snippet": "Your pull request has been merged into main...",
      "date": "2026-03-05T07:15:00Z",
      "unread": false
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | Sender email address |
| `subject` | string | Email subject line |
| `snippet` | string | Preview of the email body |
| `date` | string | ISO 8601 timestamp when received |
| `unread` | boolean | Whether the message is unread |

## Use Cases

- **Unread count**: "Do I have any new emails?" — count and summarize unread messages
- **Important emails**: "Anything important in my inbox?" — filter for messages from known contacts or with urgent subjects
- **Follow-up reminders**: "Did Alice reply to my report?" — search for messages from a specific sender or about a specific topic
- **Daily digest**: "Give me an email summary" — provide a concise overview of recent messages grouped by importance
- **Notification triage**: Help the user distinguish between actionable emails and automated notifications

## Guidelines

- **Proactive checks**: If the user mentions waiting for a reply or expecting an email, offer to check their inbox.
- **Privacy first**: Email content is highly sensitive. Never share email details with third parties or include them in logs. Only surface what the user asks for.
- **When NOT to bother the user**: Do not proactively announce new emails unless the user has explicitly asked for inbox monitoring. Automated notifications (GitHub, marketing) are low priority unless asked about.
- **Natural presentation**: Summarize rather than dump:
  - "You have 4 unread emails. The most notable one is from Alice about the Q1 Report Draft."
  - "No new emails since you last checked."
- **Snippet handling**: Use snippets to give context but don't present them as full email content. Indicate when a snippet is truncated.
- **Errors**: If the integration returns an error, let the user know their Gmail may not be connected and suggest checking their integration settings.
