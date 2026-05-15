---
name: excel-processing
description: Use when the user wants to create, read, edit, or analyze Excel (.xlsx) spreadsheet files. Handles workbook creation, data manipulation, formulas, formatting, and file output via Telegram.
metadata:
  author: openmagi
  version: "1.0"
---

# Excel Processing

## Overview

Primary path:

1. `SpreadsheetWrite` for workbook create/edit flows
2. `FileDeliver(target="chat")` for default user delivery
3. `FileDeliver(target="kb" | "both")` only when the user explicitly asked for KB retention

Do not rely on globally installed ExcelJS during a live turn. The runtime now owns the spreadsheet engine.

This skill now focuses on workbook design, formulas, and sheet structure. Raw ExcelJS scripting is a fallback only when the native spreadsheet tool is unavailable for the current runtime.

## Preferred Runtime Path

1. Use `SpreadsheetWrite` for `xlsx`, `csv`, or sheet edits.
2. Keep formulas, formatting, and workbook structure in the request payload or intermediate data you prepare.
3. If the file is user-facing, finish with `FileDeliver(target=chat|kb|both)`.

Do not stop at a workspace path. Delivery is part of the task.

## Fallback Script Path

If `SpreadsheetWrite` is unavailable in the current runtime, fall back to ExcelJS scripting:

```json
SpreadsheetWrite({
  "mode": "create",
  "title": "Financial Statements",
  "filename": "exports/financial-statements.xlsx",
  "sheets": [
    {
      "name": "BS",
      "rows": [["Account", "Amount"], ["Cash", 1000]]
    }
  ]
})
```

Then call `FileDeliver` on the returned `artifactId`. Legacy Node/ExcelJS snippets below are fallback reference material.

## Common Operations

### Create a New Spreadsheet

```javascript
const ExcelJS = require('exceljs');
const wb = new ExcelJS.Workbook();
const ws = wb.addWorksheet('Sheet1');

// Define columns
ws.columns = [
  { header: 'Name', key: 'name', width: 20 },
  { header: 'Amount', key: 'amount', width: 15 },
  { header: 'Date', key: 'date', width: 15 },
];

// Add rows
ws.addRow({ name: 'Item A', amount: 1000, date: '2026-03-01' });
ws.addRow({ name: 'Item B', amount: 2500, date: '2026-03-02' });

// Header styling
ws.getRow(1).font = { bold: true };
ws.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF4472C4' } };
ws.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };

await wb.xlsx.writeFile('/tmp/report.xlsx');
console.log('Created /tmp/report.xlsx');
```

### Read an Existing Spreadsheet

```javascript
const ExcelJS = require('exceljs');
const wb = new ExcelJS.Workbook();
await wb.xlsx.readFile('/path/to/file.xlsx');

const ws = wb.getWorksheet(1); // or by name: wb.getWorksheet('Sheet1')
const data = [];
ws.eachRow((row, rowNumber) => {
  data.push({ row: rowNumber, values: row.values.slice(1) }); // values[0] is always undefined
});
console.log(JSON.stringify(data, null, 2));
```

### Add Formulas

```javascript
// SUM formula
ws.getCell('B10').value = { formula: 'SUM(B2:B9)' };

// AVERAGE
ws.getCell('B11').value = { formula: 'AVERAGE(B2:B9)' };

// IF formula
ws.getCell('C2').value = { formula: 'IF(B2>1000,"High","Low")' };

// VLOOKUP
ws.getCell('D2').value = { formula: 'VLOOKUP(A2,Sheet2!A:B,2,FALSE)' };
```

### Formatting

```javascript
// Number format (currency)
ws.getColumn('B').numFmt = '#,##0';
ws.getCell('B2').numFmt = '$#,##0.00';

// Percentage
ws.getCell('C2').numFmt = '0.0%';

// Date format
ws.getCell('D2').numFmt = 'YYYY-MM-DD';

// Borders
ws.getCell('A1').border = {
  top: { style: 'thin' },
  left: { style: 'thin' },
  bottom: { style: 'thin' },
  right: { style: 'thin' },
};

// Cell alignment
ws.getCell('A1').alignment = { vertical: 'middle', horizontal: 'center' };

// Merge cells
ws.mergeCells('A1:D1');

// Column auto-width (approximate)
ws.columns.forEach(col => {
  let maxLen = col.header ? col.header.length : 10;
  col.eachCell({ includeEmpty: false }, cell => {
    const len = cell.value ? String(cell.value).length : 0;
    if (len > maxLen) maxLen = len;
  });
  col.width = maxLen + 2;
});
```

### Conditional Formatting

```javascript
ws.addConditionalFormatting({
  ref: 'B2:B100',
  rules: [{
    type: 'cellIs',
    operator: 'greaterThan',
    formulae: [1000],
    style: { fill: { type: 'pattern', pattern: 'solid', bgColor: { argb: 'FF92D050' } } },
  }],
});
```

## Sending Files to User

After creating a file, use native `FileDeliver`. Avoid raw Telegram-only markers in the response body.

## Complete Example: Sales Report

```javascript
const ExcelJS = require('exceljs');
const wb = new ExcelJS.Workbook();
wb.creator = 'Open Magi Bot';
wb.created = new Date();

const ws = wb.addWorksheet('Sales Report');

// Title row
ws.mergeCells('A1:E1');
ws.getCell('A1').value = 'Monthly Sales Report - March 2026';
ws.getCell('A1').font = { size: 16, bold: true };
ws.getCell('A1').alignment = { horizontal: 'center' };

// Headers (row 3)
const headers = ['Product', 'Units Sold', 'Unit Price', 'Revenue', 'Margin %'];
ws.getRow(3).values = headers;
ws.getRow(3).font = { bold: true, color: { argb: 'FFFFFFFF' } };
ws.getRow(3).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF2F5496' } };

// Data
const data = [
  ['Widget A', 150, 29.99],
  ['Widget B', 89, 49.99],
  ['Widget C', 234, 14.99],
  ['Widget D', 67, 99.99],
];
data.forEach((row, i) => {
  const r = i + 4;
  ws.getRow(r).values = [...row];
  ws.getCell(`D${r}`).value = { formula: `B${r}*C${r}` };
  ws.getCell(`E${r}`).value = { formula: `D${r}/(B${r}*C${r})*0.3` };
});

// Totals row
const lastRow = data.length + 4;
ws.getCell(`A${lastRow}`).value = 'TOTAL';
ws.getCell(`A${lastRow}`).font = { bold: true };
ws.getCell(`B${lastRow}`).value = { formula: `SUM(B4:B${lastRow-1})` };
ws.getCell(`D${lastRow}`).value = { formula: `SUM(D4:D${lastRow-1})` };

// Format columns
ws.getColumn('C').numFmt = '$#,##0.00';
ws.getColumn('D').numFmt = '$#,##0.00';
ws.getColumn('E').numFmt = '0.0%';

// Column widths
ws.columns = [
  { width: 15 }, { width: 12 }, { width: 12 }, { width: 15 }, { width: 12 },
];

await wb.xlsx.writeFile('/tmp/sales-report.xlsx');
console.log('Created /tmp/sales-report.xlsx');
```

## Limitations

- **Charts**: ExcelJS has limited chart support. For complex charts, create the data in Excel and suggest the user add charts manually, or use Google Sheets integration instead.
- **Macros/VBA**: Not supported. Cannot read or write macro-enabled workbooks (.xlsm).
- **Large files**: For very large datasets (100k+ rows), write in batches and consider memory limits.

## When to Use This vs Google Sheets

| Scenario | Use Excel | Use Google Sheets |
|----------|-----------|-------------------|
| User wants a downloadable file | Yes | No |
| Offline use needed | Yes | No |
| Real-time collaboration | No | Yes |
| Complex formatting/formulas | Yes | Yes |
| Charts and visualizations | Limited | Yes |
| Sharing with others | Send file | Share link |
| Data already in Google Sheets | No | Yes |
