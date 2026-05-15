---
name: channel-delivery-safety
description: Use before sending files, messages, reports, or notifications to Telegram, Slack, Discord, email, cron announcements, or any external channel
---

# Channel Delivery Safety

Use this skill before any outbound or scheduled channel delivery.

## Confirm

1. Destination channel or recipient.
2. Exact content or artifact being sent.
3. Whether sensitive information is included.
4. Whether the user explicitly approved this delivery.
5. Whether the delivery should happen now or later.

## Safety Rules

- Do not send secrets or private workspace data to a channel unless the user explicitly requested it.
- Do not use a stale channel from memory when the current request names a different one.
- Do not schedule a channel notification without verifying the schedule was created.
