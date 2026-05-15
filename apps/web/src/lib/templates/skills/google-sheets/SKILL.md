---
name: google-sheets
description: Use when the user wants to read, write, create, format, or share Google Sheets spreadsheets. Provides full spreadsheet control via the chat-proxy integration layer.
metadata:
  author: openmagi
  version: "1.0"
---

# Google Sheets Integration

## Overview

Full control over the user's Google Sheets: list, create, read, write, append rows, format cells, add charts, and manage sharing. Data flows through the chat-proxy integration layer using the user's connected Google account (or a custom API key).

## Commands

### List Spreadsheets (GET)

```bash
integration.sh google/sheets-list
```

**Response:**

```json
{
  "spreadsheets": [
    { "id": "1BxiM...", "name": "Budget 2026", "modifiedTime": "2026-03-05T10:00:00Z", "url": "https://docs.google.com/spreadsheets/d/1BxiM.../edit" }
  ]
}
```

### Get Spreadsheet Metadata (GET)

```bash
integration.sh "google/sheets-metadata?spreadsheetId=SPREADSHEET_ID"
```

**Response:** Sheet names, row/column counts, spreadsheet title.

### Read Cells (GET)

```bash
integration.sh "google/sheets-read?spreadsheetId=SPREADSHEET_ID&range=Sheet1!A1:D10"
```

**Response:**

```json
{
  "range": "Sheet1!A1:D10",
  "values": [
    ["Name", "Amount", "Date"],
    ["Item A", "1000", "2026-03-01"]
  ]
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `spreadsheetId` | Yes | Spreadsheet ID from URL or sheets-list |
| `range` | No | A1 notation (default: "Sheet1") |

### Write Cells (POST)

```bash
echo '{"spreadsheetId":"ID","range":"Sheet1!A1","values":[["Name","Score"],["Alice",95],["Bob",87]]}' | integration-write.sh google/sheets-write
```

**Response:** `{ "updatedRange": "Sheet1!A1:B3", "updatedRows": 3, "updatedColumns": 2 }`

| Field | Required | Description |
|-------|----------|-------------|
| `spreadsheetId` | Yes | Target spreadsheet |
| `range` | Yes | Start cell in A1 notation |
| `values` | Yes | 2D array of values |

### Append Rows (POST)

```bash
echo '{"spreadsheetId":"ID","range":"Sheet1!A:B","values":[["Charlie",92],["Diana",88]]}' | integration-write.sh google/sheets-append
```

Appends after the last row with data in the specified range.

### Create Spreadsheet (POST)

```bash
echo '{"title":"Q1 Report","sheets":["Revenue","Expenses","Summary"]}' | integration-write.sh google/sheets-create
```

**Response:** `{ "spreadsheetId": "1Bx...", "spreadsheetUrl": "https://docs.google.com/...", "title": "Q1 Report" }`

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Spreadsheet name |
| `sheets` | No | Array of sheet tab names |

### Format & Charts (POST)

```bash
echo '{"spreadsheetId":"ID","requests":[...]}' | integration-write.sh google/sheets-format
```

Uses the Sheets API `batchUpdate` — accepts an array of request objects.

#### Common Format Requests

**Bold header row:**
```json
{
  "repeatCell": {
    "range": { "sheetId": 0, "startRowIndex": 0, "endRowIndex": 1 },
    "cell": { "userEnteredFormat": { "textFormat": { "bold": true } } },
    "fields": "userEnteredFormat.textFormat.bold"
  }
}
```

**Background color:**
```json
{
  "repeatCell": {
    "range": { "sheetId": 0, "startRowIndex": 0, "endRowIndex": 1 },
    "cell": {
      "userEnteredFormat": {
        "backgroundColor": { "red": 0.18, "green": 0.33, "blue": 0.59 },
        "textFormat": { "foregroundColor": { "red": 1, "green": 1, "blue": 1 }, "bold": true }
      }
    },
    "fields": "userEnteredFormat(backgroundColor,textFormat)"
  }
}
```

**Number format (currency):**
```json
{
  "repeatCell": {
    "range": { "sheetId": 0, "startColumnIndex": 1, "endColumnIndex": 2, "startRowIndex": 1 },
    "cell": { "userEnteredFormat": { "numberFormat": { "type": "CURRENCY", "pattern": "$#,##0.00" } } },
    "fields": "userEnteredFormat.numberFormat"
  }
}
```

**Borders:**
```json
{
  "updateBorders": {
    "range": { "sheetId": 0, "startRowIndex": 0, "endRowIndex": 10, "startColumnIndex": 0, "endColumnIndex": 4 },
    "top": { "style": "SOLID", "color": { "red": 0, "green": 0, "blue": 0 } },
    "bottom": { "style": "SOLID", "color": { "red": 0, "green": 0, "blue": 0 } },
    "innerHorizontal": { "style": "SOLID", "color": { "red": 0.8, "green": 0.8, "blue": 0.8 } }
  }
}
```

**Merge cells:**
```json
{
  "mergeCells": {
    "range": { "sheetId": 0, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4 },
    "mergeType": "MERGE_ALL"
  }
}
```

**Auto-resize columns:**
```json
{
  "autoResizeDimensions": {
    "dimensions": { "sheetId": 0, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 5 }
  }
}
```

**Conditional formatting (highlight values > 1000):**
```json
{
  "addConditionalFormatRule": {
    "rule": {
      "ranges": [{ "sheetId": 0, "startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2 }],
      "booleanRule": {
        "condition": { "type": "NUMBER_GREATER", "values": [{ "userEnteredValue": "1000" }] },
        "format": { "backgroundColor": { "red": 0.57, "green": 0.82, "blue": 0.31 } }
      }
    },
    "index": 0
  }
}
```

**Add a chart:**
```json
{
  "addChart": {
    "chart": {
      "spec": {
        "title": "Revenue by Product",
        "basicChart": {
          "chartType": "COLUMN",
          "legendPosition": "BOTTOM_LEGEND",
          "domains": [{ "domain": { "sourceRange": { "sources": [{ "sheetId": 0, "startRowIndex": 0, "endRowIndex": 5, "startColumnIndex": 0, "endColumnIndex": 1 }] } } }],
          "series": [{ "series": { "sourceRange": { "sources": [{ "sheetId": 0, "startRowIndex": 0, "endRowIndex": 5, "startColumnIndex": 1, "endColumnIndex": 2 }] } } }]
        }
      },
      "position": { "overlayPosition": { "anchorCell": { "sheetId": 0, "rowIndex": 7, "columnIndex": 0 } } }
    }
  }
}
```

### Share Spreadsheet (POST)

```bash
echo '{"spreadsheetId":"ID","email":"alice@example.com","role":"writer"}' | integration-write.sh google/sheets-share
```

| Field | Required | Description |
|-------|----------|-------------|
| `spreadsheetId` | Yes | Target spreadsheet |
| `email` | Yes | Recipient email |
| `role` | No | `reader`, `writer`, or `commenter` (default: `writer`) |

## Use Cases

- **Create reports**: "Make a spreadsheet with this month's expenses" — create + write + format
- **Read data**: "What's in my Budget spreadsheet?" — list + read
- **Update records**: "Add these sales numbers to the tracker" — append rows
- **Format and style**: "Make the headers blue and bold" — batchUpdate formatting
- **Add charts**: "Add a bar chart for revenue" — batchUpdate addChart
- **Collaboration**: "Share the report with alice@company.com" — share

## Guidelines

- **Proactive suggestions**: If the user mentions tracking data, expenses, or lists, offer to use Google Sheets for collaborative access.
- **Natural presentation**: When reading data, present it as a clean summary or table, not raw JSON.
- **Batch operations**: Combine multiple format requests into a single `sheets-format` call for efficiency.
- **Sheet IDs**: When formatting, note that `sheetId` is 0 for the first sheet, 1 for second, etc. Use `sheets-metadata` to get exact IDs.
- **Range notation**: Use A1 notation (e.g., `Sheet1!A1:D10`). For entire columns: `Sheet1!A:D`. For entire sheet: `Sheet1`.
- **Errors**: If the integration returns `not_connected`, tell the user to connect their Google account in the Open Magi app settings.
- **When to use Excel instead**: If the user wants a downloadable file or offline access, use the `excel-processing` skill instead.
