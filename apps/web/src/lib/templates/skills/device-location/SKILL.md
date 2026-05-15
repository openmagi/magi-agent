---
name: device-location
description: Use when the user asks where they are, for location-aware context, commute detection, or context-aware reminders. Fetches device location via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Location Data Integration

## Overview

Access the user's current device location to provide context-aware responses, location-based reminders, and commute detection. Data is fetched through the chat-proxy integration layer from the user's connected mobile device.

## Commands

### Fetch Location Data

```bash
integration.sh device/location
```

**Response format:**

```json
{
  "lat": 37.5665,
  "lng": 126.978,
  "label": "Seoul City Hall",
  "timestamp": "2026-03-05T14:30:00Z"
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `lat` | number | Latitude coordinate |
| `lng` | number | Longitude coordinate |
| `label` | string or null | Human-readable location name (if resolved) |
| `timestamp` | string | ISO 8601 timestamp of the location reading |

## Use Cases

- **Context-aware reminders**: "Remind me to buy milk when I'm near a store" — use location to trigger contextual reminders
- **Commute detection**: Detect when the user is traveling between home and work to provide relevant information (traffic, agenda for the day)
- **Where am I**: "Where am I right now?" — provide the user's current location in a human-readable format
- **Local context**: When the user asks about nearby places, weather, or local information, use their location to provide relevant answers
- **Time zone inference**: Use location to infer the user's current time zone for scheduling-related queries

## Guidelines

- **Privacy is paramount**: Location data is extremely sensitive. Never store, log, or share location data beyond the immediate request. Never reveal precise coordinates to the user unless they specifically ask for lat/lng.
- **Use the label**: When a label is available, use it instead of coordinates. "You're at Seoul City Hall" is better than "You're at 37.5665, 126.978."
- **When NOT to bother the user**: Never proactively announce the user's location. Only use location data when it adds value to something the user asked about.
- **Stale data**: Check the `timestamp` field. If the location data is more than 30 minutes old, note that it may not reflect the user's current position.
- **No label**: If `label` is null, describe the location generally if possible, or let the user know you have coordinates but no place name.
- **Combine with other data**: Location pairs well with calendar (commute time to next meeting) and health (walking route context).
- **Errors**: If the integration returns an error, let the user know their device location may not be shared or the integration may not be connected.
