---
name: google-calendar
description: Use when the user asks about their schedule, upcoming meetings, calendar events, daily agenda, or schedule conflicts. Fetches Google Calendar data via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Google Calendar Integration

## Overview

Access the user's Google Calendar to retrieve upcoming events, check for schedule conflicts, and provide daily agenda summaries. Data is fetched through the chat-proxy integration layer using the user's connected Google account.

## Commands

### Fetch Calendar Events

```bash
integration.sh google/calendar
```

**Response format:**

```json
{
  "events": [
    {
      "title": "Weekly Standup",
      "start": "2026-03-05T09:00:00Z",
      "end": "2026-03-05T09:30:00Z",
      "location": "Zoom (https://zoom.us/j/123456)",
      "attendees": ["alice@example.com", "bob@example.com"]
    },
    {
      "title": "Lunch with Sarah",
      "start": "2026-03-05T12:00:00Z",
      "end": "2026-03-05T13:00:00Z",
      "location": "Cafe Blue",
      "attendees": ["sarah@example.com"]
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Event title/summary |
| `start` | string | ISO 8601 start time |
| `end` | string | ISO 8601 end time |
| `location` | string or null | Event location or video link |
| `attendees` | string[] | List of attendee email addresses |

## Use Cases

- **Daily agenda**: "What do I have today?" — fetch and summarize the day's events in chronological order
- **Upcoming meetings**: "When's my next meeting?" — find the nearest upcoming event
- **Schedule conflicts**: "Am I free at 3pm?" — check for overlapping events at the requested time
- **Meeting prep**: "Who's in my 2pm meeting?" — pull attendee lists for a specific event
- **Weekly overview**: "What does my week look like?" — summarize events across the coming days

## Guidelines

- **Proactive checks**: If the user mentions planning something or asks about availability, fetch the calendar without being asked explicitly.
- **Time zones**: Present times in the user's local time zone when known. If unknown, ask once and remember.
- **When NOT to bother the user**: Do not proactively announce calendar events unless the user has asked for reminders or agenda updates. Respect that calendar data is private.
- **Natural presentation**: Instead of dumping raw JSON, present events conversationally:
  - "You have 3 meetings today. Your first one is the Weekly Standup at 9am with Alice and Bob."
  - "You're free from 2pm to 4pm — that slot is open."
- **Empty calendar**: If no events are returned, say so clearly: "Your calendar is clear for today."
- **Errors**: If the integration returns an error, let the user know their Google Calendar may not be connected and suggest checking their integration settings.
