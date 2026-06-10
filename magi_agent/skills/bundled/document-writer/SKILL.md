---
name: document-writer
description: Create polished documents as Markdown, plain text, HTML, PDF, DOCX, or HWPX using Magi Agent first-party DocumentWrite and FileDeliver tools.
---

# Document Writer

Use this skill when the user asks you to create, draft, generate, export, or deliver a document file.

## Workflow

1. Draft the source in Markdown unless the user supplied structured blocks or a source file.
2. Use `DocumentWrite` for all document output formats: `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`.
3. Choose the requested format explicitly. If the user asks for several files, pass `outputs` when appropriate or call `DocumentWrite` once per format.
4. Use `renderer="canonical_markdown"` for polished HTML plus editable DOCX export from Markdown. PDF and fixed-layout DOCX require a configured renderer; if blocked, explain the blocked reason and offer DOCX/HTML.
5. After successful generation, use `FileDeliver` when the user needs the artifact delivered to chat or knowledge base.

## Format Guidance

- `md`: source-preserving Markdown.
- `txt`: plain text extraction from the document source.
- `html`: escaped, print-friendly HTML.
- `docx`: editable Word document.
- `pdf`: generated through DOCX-to-PDF conversion when a converter is available.
- `hwpx`: Korean HWPX document; prefer this for Hangul/Hancom workflows.

## Tool Shape

```json
{
  "format": "docx",
  "title": "Report title",
  "filename": "reports/report.docx",
  "source": {
    "kind": "markdown",
    "content": "# Report title\n\nBody..."
  }
}
```

Do not fall back to ad hoc shell scripts unless `DocumentWrite` is blocked and the user explicitly accepts the limitation.
