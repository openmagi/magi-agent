---
name: notion-integration
description: Use when the user asks about their Notion pages, databases, notes, or documents, or wants to find/read something in their Notion workspace. Fetches Notion data via integration.sh.
metadata:
  author: openmagi
  version: "2.0"
---

# Notion Integration

## Overview

Access the user's Notion workspace to list pages, read page content, query databases, and search across their workspace. Data is fetched through the chat-proxy integration layer using the user's connected Notion account.

## Commands

### List Pages

```bash
integration.sh notion/pages
```

Returns all pages the user has shared with the integration. Supports pagination via query param:
```bash
integration.sh "notion/pages?start_cursor=<cursor>"
```

**Response:**
```json
{
  "pages": [
    {
      "id": "abc123",
      "title": "Project Roadmap Q1 2026",
      "url": "https://notion.so/...",
      "last_edited": "2026-03-05T10:15:00Z",
      "parent_type": "database_id",
      "parent_id": "def456"
    }
  ],
  "has_more": false,
  "next_cursor": null
}
```

### Read Page Content

```bash
integration.sh notion/page/<page_id>
```

Returns the page title and block-level content (text, headings, lists, bookmarks, embeds).

**Response:**
```json
{
  "id": "abc123",
  "title": "Project Roadmap Q1 2026",
  "url": "https://notion.so/...",
  "last_edited": "2026-03-05T10:15:00Z",
  "content": [
    { "type": "heading_2", "text": "Overview" },
    { "type": "paragraph", "text": "This is the roadmap for..." },
    { "type": "bookmark", "text": "", "url": "https://docs.google.com/spreadsheets/d/..." }
  ]
}
```

### List Databases

```bash
integration.sh notion/databases
```

Returns all databases the user has shared with the integration.

### Query a Database

```bash
integration.sh notion/database/<database_id>
```

Returns all rows (pages) in a database with their property values extracted.

**Response:**
```json
{
  "rows": [
    {
      "id": "row123",
      "url": "https://notion.so/...",
      "last_edited": "2026-03-05T10:15:00Z",
      "properties": {
        "Name": "ACME Corp",
        "Status": "Active",
        "Revenue": 5000000,
        "Last Contact": "2026-02-15"
      }
    }
  ],
  "has_more": false
}
```

### Search

```bash
integration.sh "notion/search?q=roadmap"
```

Searches across all pages and databases by keyword.

**Response:**
```json
{
  "results": [
    {
      "id": "abc123",
      "object": "page",
      "title": "Project Roadmap Q1 2026",
      "url": "https://notion.so/...",
      "last_edited": "2026-03-05T10:15:00Z"
    }
  ]
}
```

## Use Cases

- **Find a page**: "Where's my project roadmap?" — search by title and provide the link
- **Read content**: "What does the onboarding doc say?" — fetch page content and summarize
- **Database query**: "Show me all active deals" — query a database and filter by properties
- **Recent activity**: "What was I working on?" — list recently edited pages sorted by date
- **Follow links**: When a page contains Google Docs/Sheets links, use the Google integration to fetch those documents

## Guidelines

- **Provide links**: Always include the Notion URL when referencing a page
- **Read before answering**: When the user asks about page content, fetch the actual page with `notion/page/<id>` — don't guess from titles alone
- **Database awareness**: If the user's data is in a Notion database (table), use `notion/database/<id>` to query structured data
- **Google Drive links**: Pages often contain links to Google Docs/Sheets. Extract these URLs and use `integration.sh google/docs-read` or `google/sheets-read` to fetch the linked data
- **Natural presentation**: Present pages cleanly — "I found your Project Roadmap, last edited today. Here's the link: [url]"
- **Errors**: If the integration returns an error, let the user know their Notion account may not be connected
