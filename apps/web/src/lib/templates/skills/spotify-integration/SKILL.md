---
name: spotify-integration
description: Use when the user asks about their recently played music, listening history, or wants to know what they've been listening to. Fetches Spotify listening data via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Spotify Integration

## Overview

Access the user's Spotify listening history to see recently played tracks, discover listening patterns, and provide music-related context. Data is fetched through the chat-proxy integration layer using the user's connected Spotify account.

## Commands

### Fetch Recent Tracks

```bash
integration.sh spotify/recent
```

**Response format:**

```json
{
  "tracks": [
    {
      "name": "Bohemian Rhapsody",
      "artist": "Queen",
      "album": "A Night at the Opera",
      "played_at": "2026-03-05T14:22:00Z"
    },
    {
      "name": "Blinding Lights",
      "artist": "The Weeknd",
      "album": "After Hours",
      "played_at": "2026-03-05T14:18:00Z"
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Track title |
| `artist` | string | Artist name |
| `album` | string | Album name |
| `played_at` | string | ISO 8601 timestamp when the track was played |

## Use Cases

- **Recent listening**: "What was I just listening to?" — show the most recently played tracks
- **Listening history**: "What have I been listening to today?" — summarize the day's listening activity
- **Music taste context**: Use listening data to personalize tone or make music-related small talk when appropriate
- **Discover patterns**: "What artists have I been into lately?" — analyze recent tracks for recurring artists or genres

## Guidelines

- **Keep it light**: Music is personal and fun. Present listening data in a casual, conversational tone.
  - "You were just listening to Bohemian Rhapsody by Queen — classic choice."
  - "Looks like you've been on a Weeknd kick today."
- **When NOT to bother the user**: Never proactively comment on the user's music unless they bring it up. Listening history is personal.
- **No judgments**: Never judge or critique the user's music taste. All music is valid.
- **Grouping**: When showing history, group by artist or time period rather than listing every track individually if there are many.
- **Context use**: If the user seems to be in a particular mood (based on conversation), you can subtly reference their music as a natural conversation element, but only if it feels organic.
- **Errors**: If the integration returns an error, let the user know their Spotify account may not be connected and suggest checking their integration settings.
