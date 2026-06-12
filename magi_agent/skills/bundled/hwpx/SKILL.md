---
name: hwpx
description: Create Korean HWPX documents with Magi Agent first-party DocumentWrite, using bundled HWPX templates and validation before FileDeliver.
---

# HWPX

Use this skill when the user asks for a Hangul/Hancom `.hwpx` file, Korean official document, meeting minutes, report, or HWPX-compatible deliverable.

## Workflow

1. Draft the source as Markdown with all required content included.
2. Call `DocumentWrite(format="hwpx")` with `title`, `filename`, `source`, and an optional `template`.
3. Use one of these templates: `base`, `gonmun`, `report`, or `minutes`.
4. Check the tool result for `hwpxValidation.status == "pass"` and `hwpxContentGuard.status == "pass"`.
5. Use `FileDeliver` after generation when the user needs the file sent or attached.

## Example

```json
{
  "format": "hwpx",
  "template": "minutes",
  "title": "회의록",
  "filename": "exports/minutes.hwpx",
  "source": {
    "kind": "markdown",
    "content": "# 회의록\n\n- 참석자: ...\n- 안건: ..."
  }
}
```

For reference-template edits, use `DocumentWrite(format="hwpx")` with the provided template reference. If the runtime reports that agentic HWPX reference authoring is not configured, explain that limitation and offer a generated HWPX using the closest bundled template.
