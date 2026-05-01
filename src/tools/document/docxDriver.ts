import fs from "node:fs/promises";
import path from "node:path";
import {
  BorderStyle,
  Document,
  HeadingLevel,
  Packer,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} from "docx";

export type StructuredBlock =
  | { type: "heading"; text: string; level?: 1 | 2 | 3 }
  | { type: "paragraph"; text: string }
  | { type: "bullet"; text: string }
  | { type: "table"; rows: string[][] }
  | { type: "horizontal_rule" };

type HeadingLevelValue = (typeof HeadingLevel)[keyof typeof HeadingLevel];
type MarkdownHeadingLevel = 1 | 2 | 3;

function headingLevelFor(level: MarkdownHeadingLevel | undefined): HeadingLevelValue {
  switch (level) {
    case 2:
      return HeadingLevel.HEADING_2;
    case 3:
      return HeadingLevel.HEADING_3;
    case 1:
    default:
      return HeadingLevel.HEADING_1;
  }
}

function flushParagraph(blocks: StructuredBlock[], lines: string[]): void {
  const text = lines.join(" ").trim();
  if (text) {
    blocks.push({ type: "paragraph", text: normalizeInlineMarkdown(text) });
  }
  lines.length = 0;
}

function splitTableCells(line: string): string[] {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => normalizeInlineMarkdown(cell.trim()));
}

function flushTable(blocks: StructuredBlock[], lines: string[]): void {
  if (lines.length === 0) return;
  const rows = lines
    .filter((line) => !/^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/.test(line.trim()))
    .map(splitTableCells)
    .filter((row) => row.length > 0 && row.some((cell) => cell.length > 0));
  if (rows.length > 0) {
    blocks.push({ type: "table", rows });
  }
  lines.length = 0;
}

function normalizeInlineMarkdown(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .trim();
}

function inlineRuns(text: string): TextRun[] {
  const runs: TextRun[] = [];
  const markdown = text
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/`([^`]+)`/g, "$1");
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g;
  let cursor = 0;
  for (const match of markdown.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      runs.push(new TextRun(markdown.slice(cursor, index)));
    }
    const raw = match[0];
    const bold = raw.startsWith("**") || raw.startsWith("__");
    const textValue = bold ? raw.slice(2, -2) : raw.slice(1, -1);
    runs.push(new TextRun({ text: textValue, bold, italics: !bold }));
    cursor = index + raw.length;
  }
  if (cursor < markdown.length) {
    runs.push(new TextRun(markdown.slice(cursor)));
  }
  return runs.length > 0 ? runs : [new TextRun("")];
}

function docxChildForBlock(block: StructuredBlock): Paragraph | Table {
  if (block.type === "heading") {
    return new Paragraph({
      children: inlineRuns(block.text),
      heading: headingLevelFor(block.level),
    });
  }
  if (block.type === "bullet") {
    return new Paragraph({
      children: inlineRuns(block.text),
      bullet: { level: 0 },
    });
  }
  if (block.type === "table") {
    return new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: block.rows.map((row) =>
        new TableRow({
          children: row.map((cell) =>
            new TableCell({
              children: [new Paragraph({ children: inlineRuns(cell) })],
            }),
          ),
        }),
      ),
    });
  }
  if (block.type === "horizontal_rule") {
    return new Paragraph({
      border: {
        bottom: { color: "D1D5DB", size: 6, style: BorderStyle.SINGLE },
      },
    });
  }
  return new Paragraph({ children: inlineRuns(block.text) });
}

export function markdownToStructuredBlocks(markdown: string): StructuredBlock[] {
  const blocks: StructuredBlock[] = [];
  const paragraphLines: string[] = [];
  const tableLines: string[] = [];

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      flushTable(blocks, tableLines);
      flushParagraph(blocks, paragraphLines);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushTable(blocks, tableLines);
      flushParagraph(blocks, paragraphLines);
      const marks = heading[1] ?? "#";
      const text = heading[2] ?? "";
      blocks.push({
        type: "heading",
        level: Math.min(marks.length, 3) as 1 | 2 | 3,
        text: normalizeInlineMarkdown(text),
      });
      continue;
    }

    if (/^\|.+\|$/.test(line)) {
      flushParagraph(blocks, paragraphLines);
      tableLines.push(line);
      continue;
    }

    flushTable(blocks, tableLines);

    if (/^[-*_]{3,}$/.test(line)) {
      flushParagraph(blocks, paragraphLines);
      blocks.push({ type: "horizontal_rule" });
      continue;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph(blocks, paragraphLines);
      blocks.push({ type: "bullet", text: normalizeInlineMarkdown(bullet[1] ?? "") });
      continue;
    }

    const blockquote = /^>\s+(.+)$/.exec(line);
    paragraphLines.push(normalizeInlineMarkdown(blockquote?.[1] ?? line));
  }

  flushTable(blocks, tableLines);
  flushParagraph(blocks, paragraphLines);
  return blocks.length > 0 ? blocks : [{ type: "paragraph", text: markdown }];
}

export async function writeDocxFromBlocks(
  absPath: string,
  blocks: StructuredBlock[],
): Promise<void> {
  const doc = new Document({
    sections: [
      {
        properties: {},
        children: blocks.map(docxChildForBlock),
      },
    ],
  });

  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, await Packer.toBuffer(doc));
}
