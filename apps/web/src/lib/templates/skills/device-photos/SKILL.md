---
name: device-photos
description: Use when the user asks about their photos, wants cleanup suggestions, duplicate detection, or a summary of recent photos. Fetches photo metadata from their device via integration.sh.
metadata:
  author: openmagi
  version: "1.0"
---

# Photo Metadata Integration

## Overview

Access photo metadata from the user's connected device to help with photo library management, duplicate detection, and storage cleanup suggestions. This integration provides metadata only (not actual photo files). Data is fetched through the chat-proxy integration layer.

## Commands

### Fetch Photo Metadata

```bash
integration.sh device/photos
```

**Response format:**

```json
{
  "photos": [
    {
      "id": "photo_abc123",
      "date": "2026-03-04T18:22:00Z",
      "size": 4521984,
      "similar_group": "group_sunset_001"
    },
    {
      "id": "photo_def456",
      "date": "2026-03-04T18:22:03Z",
      "size": 4398102,
      "similar_group": "group_sunset_001"
    },
    {
      "id": "photo_ghi789",
      "date": "2026-03-03T12:05:00Z",
      "size": 3201440,
      "similar_group": null
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique photo identifier |
| `date` | string | ISO 8601 timestamp when the photo was taken |
| `size` | number | File size in bytes |
| `similar_group` | string or null | Group ID for visually similar photos (null if unique) |

## Use Cases

- **Photo cleanup suggestions**: "Help me clean up my photos" — identify groups of similar photos and suggest keeping only the best one
- **Duplicate detection**: "Do I have duplicate photos?" — find photos sharing the same `similar_group` and report how many duplicates exist
- **Storage analysis**: "How much space are my photos taking?" — sum up file sizes and present total storage usage
- **Recent photos summary**: "What photos did I take recently?" — summarize recent photos by date and count
- **Batch organization**: Help the user understand photo groupings for bulk cleanup

## Guidelines

- **Size formatting**: Always convert bytes to human-readable format:
  - Under 1 MB: show as KB (e.g., "450 KB")
  - 1 MB and above: show as MB (e.g., "4.5 MB")
  - 1 GB and above: show as GB (e.g., "1.2 GB")
- **Similar groups**: When reporting duplicates, group them clearly:
  - "You have 3 groups of similar photos with 2-5 photos each. Cleaning up could save about 45 MB."
- **When NOT to bother the user**: Do not proactively suggest photo cleanup. Only provide this analysis when the user asks about photos or storage.
- **No image content**: This integration provides metadata only. You cannot see or describe the actual photo content. Be upfront about this limitation.
- **Natural presentation**: Frame suggestions helpfully:
  - "I found 12 similar photo groups. The largest group has 5 nearly identical shots from March 4th."
  - "Your recent photos from the last week total about 120 MB across 34 photos."
- **Errors**: If the integration returns an error, let the user know their photo access may not be enabled and suggest checking their integration settings.
