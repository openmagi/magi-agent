---
name: document-writer
description: "Use when the user wants to create or generate a document file — Markdown, plain text, HTML, PDF, DOCX (Word), or HWPX. Handles document creation with headings, paragraphs, tables, images, and styled output. For Excel files, use excel-processing skill instead."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Document Writer

## Overview

Primary path:

1. For substantial reports, memos, briefs, IC memos, research reports, audit drafts, and other document-style deliverables, write a complete Markdown source first unless the user supplied or requested a specific native document template.
2. Use `DocumentWrite` with `renderer` omitted or set to `"auto"` for PDF/DOCX/HTML exports; the runtime document-export classifier/harness routes to canonical Markdown when the user expects the exported file to match the Markdown render.
3. Use normal `DocumentWrite` for HWPX, edits to an existing file, user-supplied templates, company/institution forms, strict Word-native layouts, or any request where a specific 양식/서식 matters more than Markdown render parity.
4. Use `FileDeliver(target="chat")` for default user delivery.
5. Use `FileDeliver(target="kb" | "both")` only when the user explicitly asked for KB retention.

Canonical Markdown export policy:

- Users should never need to say `canonical_markdown`. Treat it as an internal tool-routing choice: natural-language requests such as "make the PDF match the Markdown preview", "same as rendered Markdown", "IC memo PDF/DOCX from this Markdown", or "HTML/PDF/DOCX versions should look the same" are classified once by the runtime and enforced by the `DocumentWrite` routing harness.
- PDF is rendered from canonical HTML/CSS through browser-worker. Do not hand-code PDFKit for ordinary reports.
- Canonical export is opt-in, not mandatory. Do not force it when the user asks to follow a specific format, template, previous document, branded form, government/corporate form, or Word/HWP-native layout.
- DOCX defaults to `docxMode="editable"`.
- Use `docxMode="fixed_layout"` only when the user prioritizes visual identity with the PDF over Word editability.
- Do not claim PDF/DOCX parity unless `DocumentWrite` metadata reports canonical Markdown QA success.

Do not install document packages during a live user turn. The runtime now bundles the supported document engines, including PDF generation.

For `docx` and `hwpx`, `DocumentWrite` runs a high-quality agentic authoring loop inside the runtime: it creates build files, executes allowlisted document commands, inspects failures, and retries until the output exists. HWPX output is independently validated with the bundled HWPX validator and a source-content coverage guard so template-only files are rejected; HWPX edits with a reference file must also pass page-drift guard checks. For `pdf`, the runtime first authors a rich DOCX through the same loop, then converts that DOCX to PDF deterministically. If those paths cannot complete, the runtime falls back to its fast native renderer so the user still gets a valid file.

This skill now focuses on document structure and format-specific guidance. Raw Node scripting is a fallback only when the native document tool is unavailable for the current runtime.

## Preferred Runtime Path

Use `DocumentWrite` for `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`.

1. Choose the right output format: `md`, `txt`, `html`, `pdf`, `docx`, or `hwpx`.
2. Use `DocumentWrite` with the requested title, source content, and edit/create mode. For ordinary prose, pass `source` as a single markdown string, including for HWPX. If the content already exists in the workspace, pass `source: { "type": "markdown", "path": "..." }`. Use structured `blocks` only when you need explicit heading/paragraph objects; use `blocksFile` when the blocks are already saved as JSON. For HWPX reports, prefer `template: "report"` unless the user requested `minutes`, `gonmun`, or a plain base document. If the user names a specific 양식, asks to preserve an existing file's layout, or provides a template/reference file, stay on normal `DocumentWrite` and let the native authoring path preserve that format.
3. If the file is user-facing, finish with `FileDeliver(target=chat|kb|both)`.

Do not stop at a workspace path. Delivery is part of the task.

```json
DocumentWrite({
  "mode": "create",
  "format": "docx",
  "title": "Board Memo",
  "filename": "exports/board-memo.docx",
  "source": "# Board Memo\n\nSummary of this week's decisions."
})
```

```json
DocumentWrite({
  "mode": "create",
  "format": "pdf",
  "renderer": "auto",
  "title": "Investment Committee Memorandum",
  "filename": "exports/ic-memo.pdf",
  "preset": "investment_committee",
  "locale": "ko-KR",
  "page": { "size": "A4", "margin": "18mm" },
  "source": { "type": "markdown", "path": "reports/ic-memo.md" }
})
```

Structured source is also accepted when needed:

```json
{
  "source": {
    "kind": "structured",
    "blocks": [
      { "type": "heading", "level": 1, "text": "Board Memo" },
      { "type": "paragraph", "text": "Summary of this week's decisions." }
    ]
  }
}
```

Existing workspace source files are accepted:

```json
{
  "source": { "type": "markdown", "path": "reports/full-report.md" }
}
```

Structured JSON block files are accepted:

```json
{
  "source": { "kind": "structured", "blocksFile": "scripts/blocks.json" }
}
```

## Sending Files to User

```json
FileDeliver({
  "artifactId": "<DocumentWrite 결과>",
  "target": "chat",
  "chat": { "channel": "general" }
})
```

Include the returned attachment marker in the final reply text. Legacy Node examples below are fallback reference material, not the primary path.

---

## Fallback Script Path

Only if `DocumentWrite` is not available in the current runtime, write a Node.js script and run it.

## DOCX (Word) Generation

Uses the `docx` library. Key concepts: `Document` → `Section` → `Paragraph`/`Table`.

### Korean / CJK DOCX Text

DOCX Korean text does not require an installed system font to create a valid Word file. Do not refuse a Korean DOCX request because local fonts are missing. Set the run font to `Noto Sans CJK KR` when Korean text is present; Word, Hancom, and compatible viewers can render or substitute the named font. Native PDF generation embeds the runtime CJK font when available.

### Basic Document

```javascript
const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } = require('docx');
const fs = require('fs');

const doc = new Document({
  sections: [{
    properties: {},
    children: [
      new Paragraph({
        text: 'Monthly Report',
        heading: HeadingLevel.HEADING_1,
        alignment: AlignmentType.CENTER,
      }),
      new Paragraph({
        children: [
          new TextRun({ text: 'Generated by ', size: 24 }),
          new TextRun({ text: 'Open Magi Bot', bold: true, size: 24 }),
          new TextRun({ text: ` on ${new Date().toLocaleDateString()}`, size: 24 }),
        ],
      }),
      new Paragraph({ text: '' }), // empty line
      new Paragraph({
        text: 'This is the first paragraph of the report body. The document supports full rich text formatting.',
        spacing: { after: 200 },
      }),
    ],
  }],
});

const buffer = await Packer.toBuffer(doc);
fs.writeFileSync('/tmp/report.docx', buffer);
console.log('Created /tmp/report.docx');
```

### Styled Text

```javascript
const { TextRun } = require('docx');

// Bold
new TextRun({ text: 'Bold text', bold: true })

// Italic
new TextRun({ text: 'Italic text', italics: true })

// Colored
new TextRun({ text: 'Red text', color: 'FF0000' })

// Font size (half-points: 24 = 12pt)
new TextRun({ text: 'Large text', size: 36 }) // 18pt

// Underline
new TextRun({ text: 'Underlined', underline: { type: 'single' } })

// Combined
new TextRun({ text: 'Bold Red', bold: true, color: 'FF0000', size: 28 })
```

### Tables

```javascript
const { Table, TableRow, TableCell, Paragraph, WidthType, BorderStyle } = require('docx');

const table = new Table({
  width: { size: 100, type: WidthType.PERCENTAGE },
  rows: [
    // Header row
    new TableRow({
      tableHeader: true,
      children: ['Name', 'Amount', 'Date'].map(text =>
        new TableCell({
          children: [new Paragraph({ text, alignment: AlignmentType.CENTER })],
          shading: { fill: '2F5496' },
        })
      ),
    }),
    // Data rows
    ...data.map(row =>
      new TableRow({
        children: row.map(text =>
          new TableCell({
            children: [new Paragraph({ text: String(text) })],
          })
        ),
      })
    ),
  ],
});
```

### Bullet / Numbered Lists

```javascript
const { Paragraph } = require('docx');

// Bullet list
new Paragraph({ text: 'First item', bullet: { level: 0 } })
new Paragraph({ text: 'Sub-item', bullet: { level: 1 } })

// Numbered list (requires numbering config)
const doc = new Document({
  numbering: {
    config: [{
      reference: 'numbered-list',
      levels: [{ level: 0, format: 'decimal', text: '%1.', alignment: AlignmentType.START }],
    }],
  },
  sections: [{
    children: [
      new Paragraph({
        text: 'Step one',
        numbering: { reference: 'numbered-list', level: 0 },
      }),
    ],
  }],
});
```

### Images

```javascript
const { ImageRun, Paragraph } = require('docx');
const fs = require('fs');

new Paragraph({
  children: [
    new ImageRun({
      data: fs.readFileSync('/workspace/uploads/logo.png'),
      transformation: { width: 200, height: 100 },
    }),
  ],
});
```

### Page Setup

```javascript
const doc = new Document({
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 }, // Letter (twips)
        margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 }, // 1 inch = 1440 twips
      },
    },
    children: [/* ... */],
  }],
});
```

---

## PDF Generation

Use `DocumentWrite(format="pdf")` first. The native runtime authors an intermediate rich DOCX, converts it to PDF deterministically, registers the PDF artifact, and then `FileDeliver` sends it to chat or KB. This preserves tables and Word-style layout better than direct PDF drawing.

The examples below are fallback reference material for low-level custom layouts only. Do not prefer raw scripts over native `DocumentWrite` for ordinary reports, memos, summaries, invoices, or briefs.

### Basic Document

```javascript
const PDFDocument = require('pdfkit');
const fs = require('fs');

const doc = new PDFDocument({ size: 'A4', margin: 50 });
doc.pipe(fs.createWriteStream('/tmp/report.pdf'));

// Title
doc.fontSize(24).font('Helvetica-Bold').text('Monthly Report', { align: 'center' });
doc.moveDown();

// Body text
doc.fontSize(12).font('Helvetica').text(
  'This is the report body. PDFKit supports rich text, images, and vector graphics.',
  { align: 'left', lineGap: 4 }
);

doc.end();
// Wait for stream to finish
await new Promise(resolve => doc.on('end', resolve));
console.log('Created /tmp/report.pdf');
```

### Text Styling

```javascript
// Bold
doc.font('Helvetica-Bold').text('Bold text');

// Italic
doc.font('Helvetica-Oblique').text('Italic text');

// Colored
doc.fillColor('red').text('Red text').fillColor('black');

// Font size
doc.fontSize(18).text('Large text').fontSize(12);

// Link
doc.fillColor('blue').text('Click here', { link: 'https://openmagi.ai', underline: true });

// Inline styling (continued text)
doc.font('Helvetica-Bold').text('Name: ', { continued: true })
   .font('Helvetica').text('John Doe');
```

### Built-in Fonts

PDFKit includes these fonts without any file:
- `Helvetica`, `Helvetica-Bold`, `Helvetica-Oblique`, `Helvetica-BoldOblique`
- `Courier`, `Courier-Bold`, `Courier-Oblique`, `Courier-BoldOblique`
- `Times-Roman`, `Times-Bold`, `Times-Italic`, `Times-BoldItalic`
- `Symbol`, `ZapfDingbats`

### Simple Table (Manual Drawing)

```javascript
function drawTable(doc, headers, rows, startX, startY, colWidths) {
  const rowHeight = 25;
  let y = startY;

  // Header
  doc.font('Helvetica-Bold').fontSize(10);
  headers.forEach((h, i) => {
    const x = startX + colWidths.slice(0, i).reduce((a, b) => a + b, 0);
    doc.rect(x, y, colWidths[i], rowHeight).fill('#2F5496').stroke();
    doc.fillColor('white').text(h, x + 5, y + 7, { width: colWidths[i] - 10 });
  });
  y += rowHeight;

  // Data rows
  doc.font('Helvetica').fillColor('black');
  rows.forEach(row => {
    row.forEach((cell, i) => {
      const x = startX + colWidths.slice(0, i).reduce((a, b) => a + b, 0);
      doc.rect(x, y, colWidths[i], rowHeight).stroke();
      doc.text(String(cell), x + 5, y + 7, { width: colWidths[i] - 10 });
    });
    y += rowHeight;
  });

  return y; // return cursor position after table
}

// Usage
drawTable(doc,
  ['Product', 'Qty', 'Price'],
  [['Widget A', '150', '$29.99'], ['Widget B', '89', '$49.99']],
  50, doc.y + 10, [200, 100, 100]
);
```

### Images

```javascript
// From file
doc.image('/workspace/uploads/logo.png', { width: 200 });

// Centered image
doc.image('/workspace/uploads/chart.png', {
  fit: [400, 300],
  align: 'center',
});
```

### Multi-Page with Headers/Footers

```javascript
const doc = new PDFDocument({ size: 'A4', margin: 50, bufferPages: true });

// ... add content ...

// Add page numbers after all content is done
const pages = doc.bufferedPageRange();
for (let i = 0; i < pages.count; i++) {
  doc.switchToPage(i);
  doc.fontSize(8).fillColor('gray')
     .text(`Page ${i + 1} of ${pages.count}`, 50, doc.page.height - 40, {
       align: 'center', width: doc.page.width - 100,
     });
}

doc.end();
```

---

## HTML Generation

No library needed — write HTML string directly.

### Basic HTML

```javascript
const fs = require('fs');

const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Report</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 40px; color: #333; }
    h1 { color: #2F5496; border-bottom: 2px solid #2F5496; padding-bottom: 8px; }
    table { width: 100%; border-collapse: collapse; margin: 20px 0; }
    th { background: #2F5496; color: white; padding: 10px; text-align: left; }
    td { padding: 8px 10px; border-bottom: 1px solid #ddd; }
    tr:hover { background: #f5f5f5; }
  </style>
</head>
<body>
  <h1>Monthly Report</h1>
  <p>Generated on ${new Date().toLocaleDateString()}</p>
  <table>
    <thead><tr><th>Product</th><th>Qty</th><th>Price</th></tr></thead>
    <tbody>
      <tr><td>Widget A</td><td>150</td><td>$29.99</td></tr>
      <tr><td>Widget B</td><td>89</td><td>$49.99</td></tr>
    </tbody>
  </table>
</body>
</html>`;

fs.writeFileSync('/tmp/report.html', html);
console.log('Created /tmp/report.html');
```

---

## Complete Example: Business Proposal (DOCX)

```javascript
const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType, ImageRun } = require('docx');
const fs = require('fs');

const doc = new Document({
  styles: {
    paragraphStyles: [{
      id: 'body',
      name: 'Body',
      run: { size: 24, font: 'Calibri' },
      paragraph: { spacing: { after: 200, line: 276 } },
    }],
  },
  sections: [{
    properties: {
      page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } },
    },
    children: [
      // Cover
      new Paragraph({ text: '' }),
      new Paragraph({ text: '' }),
      new Paragraph({
        children: [new TextRun({ text: 'Business Proposal', bold: true, size: 56, color: '2F5496' })],
        alignment: AlignmentType.CENTER,
      }),
      new Paragraph({
        children: [new TextRun({ text: 'Prepared for ACME Corp', size: 28, color: '666666' })],
        alignment: AlignmentType.CENTER,
        spacing: { after: 400 },
      }),
      new Paragraph({
        children: [new TextRun({ text: new Date().toLocaleDateString(), size: 24, color: '999999' })],
        alignment: AlignmentType.CENTER,
      }),

      // Section break would go here in real doc
      new Paragraph({ text: '' }),
      new Paragraph({ text: 'Executive Summary', heading: HeadingLevel.HEADING_1 }),
      new Paragraph({
        text: 'We propose a comprehensive solution that will reduce operational costs by 30% while improving customer satisfaction scores.',
        style: 'body',
      }),

      new Paragraph({ text: 'Pricing', heading: HeadingLevel.HEADING_1 }),
      new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          new TableRow({
            tableHeader: true,
            children: ['Service', 'Monthly Cost', 'Annual Cost'].map(text =>
              new TableCell({
                children: [new Paragraph({ text, alignment: AlignmentType.CENTER })],
                shading: { fill: '2F5496' },
              })
            ),
          }),
          ...[ ['Platform License', '$2,000', '$24,000'], ['Support', '$500', '$6,000'], ['Total', '$2,500', '$30,000'] ]
            .map(row => new TableRow({
              children: row.map(text => new TableCell({ children: [new Paragraph({ text })] })),
            })),
        ],
      }),
    ],
  }],
});

const buffer = await Packer.toBuffer(doc);
fs.writeFileSync('/tmp/proposal.docx', buffer);
console.log('Created /tmp/proposal.docx');
```

## When to Use Which Format

| Scenario | MD | TXT | HTML | PDF | DOCX | HWPX |
|----------|----|-----|------|-----|------|------|
| Fast readable notes | Best | Best | Good | Good | Good | Good |
| Editable document needed | Limited | Limited | Partial | No | Best | Best for Hancom |
| Print-ready layout | No | No | Print CSS needed | Best | Good | Good |
| Chat/download attachment | Yes | Yes | Yes | Yes | Yes | Yes |
| Rich formatting | Limited | No | Yes | Yes | Yes | Yes |
| Korean text (한글) | Yes | Yes | Yes | Yes | Yes | Yes |

## Limitations

- **PDF layout**: Native PDF supports headings, paragraphs, and page numbers. For complex editable tables/charts, create DOCX/HWPX as the primary file and PDF as a flattened companion when requested.
- **DOCX charts**: The `docx` library doesn't support chart insertion. Create data tables and suggest the user add charts in Word.
- **HTML rendering**: HTML files are delivered as-is. The user opens them in a browser — no server-side rendering.
- **For Excel (.xlsx)**: Use the `excel-processing` skill instead — it has full ExcelJS support.
- **For HWPX (한글)**: Use `DocumentWrite(format="hwpx")` for ordinary HWPX generation. Use the `hwpx` skill when low-level Hancom XML/template control is needed.
