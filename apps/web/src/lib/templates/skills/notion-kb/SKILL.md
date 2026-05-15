---
name: notion-kb
description: Use when the user wants to set up Notion as a knowledge base, sync their workspace, or asks questions that require searching through their company docs, databases, or linked Google Sheets. Requires notion-integration to be active. User must explicitly opt in.
metadata:
  author: openmagi
  version: "2.0"
  requires: notion-integration
---

# Notion Knowledge Base

## Overview

Syncs the user's Notion workspace metadata into a local searchable index. Enables fast querying of company knowledge without fetching every document on every question.

**Architecture: Hybrid (Sync metadata + On-demand content)**
- **Indexed locally**: Page titles, summaries, headings, database schemas, linked Google Sheet structures
- **Fetched on-demand**: Actual page content and cell data — always fresh from API

## Setup

When the user asks to set up Notion KB sync (e.g., "노션 knowledge base 연동해줘"):

1. Confirm scope: "전체 워크스페이스를 sync할까요?"
2. Run initial full sync:

```bash
notion-kb-sync.sh full
```

3. Add to TASK-QUEUE.md:
```
- [ ] 매일 03:00 — notion-kb-sync.sh incremental
```

4. Report results: how many pages/databases indexed, daily auto-sync active

## Sync Commands

```bash
# Full sync — re-indexes everything
notion-kb-sync.sh full

# Incremental sync — only changed since last sync
notion-kb-sync.sh incremental

# Check sync status
notion-kb-sync.sh status
```

**Response:**
```json
{
  "status": "ok",
  "pages_synced": 15,
  "pages_skipped": 30,
  "databases": 3,
  "gdrive_files": 2,
  "errors": 0,
  "total_pages": 45,
  "last_sync": "2026-03-10T03:00:00Z"
}
```

## Query Workflow

When the user asks a question about their knowledge base:

### Step 1: Search Local Index

The index is at `workspace/knowledge/notion-index/`. Search it:
- Read `_catalog.json` for the full page/database listing
- Search `page-*.md` files for relevant content by keyword
- Check `gdrive-*.md` for Google Sheet structures

### Step 2: Identify Sources

From the index, determine:
- Which Notion page(s) likely contain the answer
- Whether the answer is in a page or a linked Google Sheet
- The specific database/sheet to query

### Step 3: Fetch Fresh Data

```bash
# If answer is in a Notion page:
integration.sh notion/page/<page_id>

# If answer is in a Notion database:
integration.sh notion/database/<database_id>

# If answer is in a Google Sheet:
integration.sh "google/sheets-read?spreadsheetId=<id>&range=Sheet1!A1:F50"
```

### Step 4: Answer with Citations

Always cite the source page/database with its Notion URL.

## Manual Commands

- **"sync해줘"** / **"노션 sync"** — `notion-kb-sync.sh incremental`
- **"전체 sync"** / **"풀 sync"** — `notion-kb-sync.sh full`
- **"sync 상태"** — `notion-kb-sync.sh status`

## Index Structure

```
workspace/knowledge/notion-index/
├── _catalog.json       — Master index (all pages + databases)
├── page-{id}.md        — Per-page metadata (title, summary, sections, links)
└── gdrive-{id}.md      — Google Sheet structure (tabs, columns, row counts)
```

## Guidelines

- **Never store full page content** — only titles, summaries, and structure
- **Google Sheet structure only** — tab names, columns, row counts (no cell values)
- **Incremental by default** — full sync only on explicit request or initial setup
- **Don't auto-sync** — only run when user opts in or requests
- **Handle errors** — skip failed pages, don't fail entire sync
- **Rate limits** — Notion API ~3 req/s, script handles this automatically
