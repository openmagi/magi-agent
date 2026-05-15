---
name: document-reader
description: Use when the user sends a PDF, DOCX, or XLSX file, or asks you to read/analyze a document file. Converts binary document files to readable text via the document-worker service. MUST use this instead of the Read tool for .pdf, .docx, .xlsx files — Read tool shows raw binary for these formats.
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Document Reader

## Overview

Binary document files (PDF, DOCX, XLSX) cannot be read with the Read tool — it shows raw binary garbage. Use the document-worker service to convert them to readable text first.

## When to Use

- User sends a `.pdf`, `.docx`, or `.xlsx` file
- User asks to read, summarize, or analyze a document
- You encounter a binary file that Read tool cannot display

## How to Convert

Send the file to document-worker via `curl`:

```bash
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/pdf' -H 'X-Filename: document.pdf' --data-binary @/path/to/file.pdf"]
```

### MIME Types

| Extension | X-Mimetype |
|-----------|-----------|
| `.pdf` | `application/pdf` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.csv` | `text/csv` |
| `.txt` | `text/plain` |
| `.md` | `text/markdown` |
| `.json` | `application/json` |

### Examples

**Read a PDF:**
```bash
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/pdf' -H 'X-Filename: report.pdf' --data-binary @/workspace/uploads/report.pdf"]
```

**Read a DOCX:**
```bash
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/vnd.openxmlformats-officedocument.wordprocessingml.document' -H 'X-Filename: document.docx' --data-binary @/workspace/uploads/document.docx"]
```

**Read an XLSX:**
```bash
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' -H 'X-Filename: data.xlsx' --data-binary @/workspace/uploads/data.xlsx"]
```

## For Large Documents

If the output is very long, pipe through `head` to get the first N lines:

```bash
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/pdf' -H 'X-Filename: big.pdf' --data-binary @/workspace/uploads/big.pdf | head -500"]
```

## Important Rules

- **NEVER use Read tool on .pdf, .docx, .xlsx** — it shows binary garbage
- **Always detect file extension** and use the matching MIME type
- If document-worker is unreachable, tell the user the document conversion service is temporarily unavailable
- For `.txt`, `.csv`, `.md`, `.json` files, you can use Read tool directly (they are text-based)
