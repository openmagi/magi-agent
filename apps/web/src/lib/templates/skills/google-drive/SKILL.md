---
name: google-drive
description: Use when the user asks about their files, documents, or wants to search, browse, upload, or manage files in Google Drive. Fetches Drive data via integration.sh.
metadata:
  author: openmagi
  version: "1.2"
---

# Google Drive Integration

## Overview

Access the user's Google Drive to list files, search for documents, browse folder contents, create folders, and upload files. Data is fetched through the chat-proxy integration layer using the user's connected Google account.

## Commands

### List Recent Files

```bash
integration.sh google/drive
```

### Search Files

```bash
echo '{"q":"quarterly report"}' | integration-write.sh google/drive
```

### Browse a Folder

```bash
echo '{"folderId":"FOLDER_ID"}' | integration-write.sh google/drive
```

### Create a Folder

```bash
echo '{"name":"My Folder"}' | integration-write.sh google/drive-mkdir
```

With a parent folder:

```bash
echo '{"name":"Sub Folder","parentId":"PARENT_FOLDER_ID"}' | integration-write.sh google/drive-mkdir
```

### Upload a File

Upload a workspace file to the user's Google Drive (max 10MB):

```bash
echo "{\"name\":\"report.pdf\",\"content\":\"$(base64 -w0 /path/to/file)\",\"mimeType\":\"application/pdf\"}" | integration-write.sh google/drive-upload
```

Upload into a specific folder:

```bash
echo "{\"name\":\"data.xlsx\",\"content\":\"$(base64 -w0 /path/to/file)\",\"mimeType\":\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\",\"folderId\":\"FOLDER_ID\"}" | integration-write.sh google/drive-upload
```

## Response Format

### drive (list/search/browse)

```json
{
  "files": [
    {
      "id": "1BxiM...",
      "name": "Q1 Report",
      "mimeType": "application/vnd.google-apps.document",
      "modifiedTime": "2026-03-05T10:00:00Z",
      "size": null,
      "webViewLink": "https://docs.google.com/document/d/1BxiM.../edit",
      "parents": ["0APn..."]
    }
  ]
}
```

### drive-mkdir

```json
{
  "id": "1New...",
  "name": "My Folder",
  "webViewLink": "https://drive.google.com/drive/folders/1New..."
}
```

### drive-upload

```json
{
  "id": "1Upl...",
  "name": "report.pdf",
  "mimeType": "application/pdf",
  "webViewLink": "https://drive.google.com/file/d/1Upl.../view"
}
```

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | File/folder ID |
| `name` | string | File/folder name |
| `mimeType` | string | MIME type (folder, doc, sheet, etc.) |
| `modifiedTime` | string | ISO 8601 last modified time |
| `size` | string/null | File size in bytes (null for Google Docs types) |
| `webViewLink` | string | URL to open in browser |
| `parents` | string[] | Parent folder IDs |

## Use Cases

- **Find files**: "Where's the quarterly report?" — search with `q` param
- **Recent files**: "What files did I work on today?" — list without params
- **Browse folders**: "What's in my project folder?" — pass `folderId`
- **Create folder structure**: "Set up a project folder" — use `drive-mkdir`
- **Upload files**: "Upload this report to Drive" — use `drive-upload` with base64 content
- **File info**: "How big is my backup folder?" — get metadata about files

## Guidelines

- **Natural presentation**: Present file lists as clean summaries, not raw JSON.
  - "You have 3 recently modified files. The most recent is 'Q1 Report' updated 2 hours ago."
- **Upload workflow**: When the user asks to upload a file to Drive:
  1. Create or locate the file in the workspace first.
  2. Use `base64 -w0 <filepath>` to encode the file content.
  3. Send via `integration-write.sh google/drive-upload` with `name`, `content`, and `mimeType`.
  4. Share the resulting `webViewLink` with the user.
- **MIME type mapping**: Translate Google MIME types to friendly names:
  - `application/vnd.google-apps.document` → "Google Doc"
  - `application/vnd.google-apps.spreadsheet` → "Google Sheet"
  - `application/vnd.google-apps.presentation` → "Google Slides"
  - `application/vnd.google-apps.folder` → "Folder"
- **Errors**: If the integration returns an error, let the user know their Google Drive may not be connected and suggest checking their integration settings.
