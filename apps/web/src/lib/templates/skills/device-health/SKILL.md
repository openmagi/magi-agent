---
name: device-health
description: Use when the user asks about their health data, step count, sleep quality, heart rate, or wants a daily health summary. Fetches health data from their device via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Health Data Integration

## Overview

Access health and fitness data from the user's connected device (Apple Health, Google Fit, etc.) to provide daily health summaries, track activity levels, and monitor sleep quality. Data is fetched through the chat-proxy integration layer.

## Commands

### Fetch Health Data

```bash
integration.sh device/health
```

**Response format:**

```json
{
  "steps": 8432,
  "sleep_hours": 7.2,
  "heart_rate_avg": 72,
  "date": "2026-03-05"
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `steps` | number | Total step count for the day |
| `sleep_hours` | number | Hours of sleep (decimal) |
| `heart_rate_avg` | number | Average resting heart rate in BPM |
| `date` | string | Date the data corresponds to (YYYY-MM-DD) |

## Use Cases

- **Daily health summary**: "How did I sleep last night?" — report sleep hours with qualitative assessment
- **Activity tracking**: "How many steps have I taken today?" — report current step count and progress toward common goals (e.g., 10,000 steps)
- **Sleep quality**: "Am I getting enough sleep?" — analyze sleep hours and provide gentle feedback
- **Heart rate check**: "What's my heart rate been like?" — report average resting heart rate
- **Wellness trends**: When the user asks about their overall health, combine all metrics into a holistic summary

## Guidelines

- **Proactive checks**: If the user mentions feeling tired or asks about productivity, health context (like low sleep) can be relevant. Offer insights when appropriate.
- **Qualitative framing**: Present numbers with context, not just raw data:
  - "You got 7.2 hours of sleep last night — that's solid."
  - "You're at 8,432 steps so far. About 1,500 more to hit 10k."
  - "Your average heart rate is 72 BPM — within a healthy resting range."
- **When NOT to bother the user**: Do not proactively report health data unless asked. Health is personal. Never make medical judgments or diagnoses.
- **Sensitivity**: Avoid negative framing. Instead of "You only slept 4 hours," try "You got about 4 hours of sleep — might be worth taking it easy today."
- **Not medical advice**: Always be clear that this is informational. Never diagnose conditions or recommend medical treatments based on the data.
- **Missing data**: If fields are null or zero, the device may not have synced. Let the user know rather than presenting zeros as real data.
- **Errors**: If the integration returns an error, let the user know their health device may not be connected and suggest checking their integration settings.
