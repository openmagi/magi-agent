---
name: google-docs
description: Use when the user asks to read, create, edit, or reference Google Docs documents. Fetches and modifies Docs data via integration.sh.
metadata:
  author: openmagi
  version: "2.0"
---

# Google Docs Integration

## Overview

Access the user's Google Docs to read, create, and edit documents. Data is fetched through the chat-proxy integration layer using the user's connected Google account.

## Commands

### List Documents

```bash
integration.sh google/docs-list
```

Lists recently modified Google Docs documents (up to 20).

**Response format:**

```json
{
  "docs": [
    {
      "id": "1abc...",
      "title": "Project Proposal",
      "lastModified": "2026-03-05T10:00:00Z",
      "url": "https://docs.google.com/document/d/1abc.../edit"
    }
  ],
  "synced_at": "2026-03-08T12:00:00Z"
}
```

### Read Document Content

```bash
integration.sh "google/docs-read" '{"documentId":"DOCUMENT_ID"}'
```

**Response format:**

```json
{
  "title": "Project Proposal",
  "content": "This document outlines...",
  "wordCount": 1250,
  "synced_at": "2026-03-08T12:00:00Z"
}
```

### Create Document

```bash
integration.sh "google/docs-create" '{"title":"New Document Title"}'
```

**Response format:**

```json
{
  "documentId": "1abc...",
  "title": "New Document Title",
  "synced_at": "2026-03-08T12:00:00Z"
}
```

### Write to Document

```bash
integration.sh "google/docs-write" '{"documentId":"DOCUMENT_ID","text":"Hello world","index":1}'
```

Inserts text at the given position (index defaults to 1 = beginning of doc).

**Response format:**

```json
{
  "replies": [],
  "synced_at": "2026-03-08T12:00:00Z"
}
```

## Use Cases

- **Read a document**: "Read my project proposal" — list docs first, then fetch by ID
- **Summarize**: "Summarize the meeting notes from yesterday" — fetch and provide a concise summary
- **Reference**: "What does the design doc say about authentication?" — search and read document content
- **Recent docs**: "What documents have I been working on?" — list recently modified docs
- **Create**: "Create a new doc called Meeting Notes" — create a blank document
- **Write**: "Add a summary section to my project doc" — append text to an existing document

## Guidelines

- **Privacy**: Document content is sensitive. Only share what the user explicitly asks for.
- **Long documents**: For lengthy documents, provide a summary first and offer to show specific sections.
- **Natural presentation**: Present content with appropriate formatting, not as raw data.
- **Document IDs**: Users won't know document IDs. Use `docs-list` to find documents first, then read/write by ID.
- **Errors**: If the integration returns an error, let the user know their Google account may not be connected and suggest checking their integration settings.
