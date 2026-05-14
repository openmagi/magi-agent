import fs from "node:fs/promises";
import path from "node:path";
import {
  AlignmentType,
  BorderStyle,
  Document,
  HeadingLevel,
  ImageRun,
  Packer,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} from "docx";
import type {
  CanonicalInline,
  CanonicalMarkdownBlock,
  CanonicalMarkdownDocument,
  CanonicalMarkdownRenderOptions,
} from "./canonicalMarkdownTypes.js";

interface RunSpec {
  text: string;
  bold?: boolean;
  italics?: boolean;
  code?: boolean;
  link?: string;
}

type DocxBlock = Paragraph | Table;
type HeadingLevelValue = (typeof HeadingLevel)[keyof typeof HeadingLevel];

function inlineRunSpecs(
  inlines: CanonicalInline[],
  marks: Omit<RunSpec, "text"> = {},
): RunSpec[] {
  const specs: RunSpec[] = [];
  for (const inline of inlines) {
    if (inline.type === "text") {
      specs.push({ ...marks, text: inline.value });
    } else if (inline.type === "strong") {
      specs.push(...inlineRunSpecs(inline.children, { ...marks, bold: true }));
    } else if (inline.type === "emphasis") {
      specs.push(...inlineRunSpecs(inline.children, { ...marks, italics: true }));
    } else if (inline.type === "inline_code") {
      specs.push({ ...marks, code: true, text: inline.value });
    } else if (inline.type === "link") {
      const label = inlineRunSpecs(inline.children, marks)
        .map((spec) => spec.text)
        .join("");
      specs.push({ ...marks, link: inline.url, text: `${label} (${inline.url})` });
    } else if (inline.type === "image") {
      specs.push({ ...marks, italics: true, text: inline.alt || "[image]" });
    }
  }
  return specs;
}

function textRuns(inlines: CanonicalInline[]): TextRun[] {
  const runs = inlineRunSpecs(inlines).map(
    (spec) =>
      new TextRun({
        text: spec.text,
        bold: spec.bold,
        italics: spec.italics,
        font: spec.code ? "Consolas" : "Noto Sans CJK KR",
        color: spec.link ? "2563EB" : undefined,
        shading: spec.code ? { fill: "F5F7FA" } : undefined,
      }),
  );
  return runs.length > 0 ? runs : [new TextRun("")];
}

function inlineText(inline: CanonicalInline): string {
  if (inline.type === "text" || inline.type === "inline_code") return inline.value;
  if (inline.type === "image") return inline.alt;
  return inline.children.map(inlineText).join("");
}

function inlineBlockText(block: CanonicalMarkdownBlock): string {
  if (block.type === "heading" || block.type === "paragraph") {
    return block.children.map(inlineText).join("");
  }
  if (block.type === "code") return block.value;
  if (block.type === "blockquote") {
    return block.children.map(inlineBlockText).join(" ");
  }
  if (block.type === "list") {
    return block.items
      .flatMap((item) => item.map(inlineBlockText))
      .join(" ");
  }
  if (block.type === "table") {
    return block.rows
      .map((row) => row.map((cell) => cell.children.map(inlineText).join("")).join(" "))
      .join(" ");
  }
  return "";
}

function headingLevel(level: number): HeadingLevelValue {
  if (level === 1) return HeadingLevel.HEADING_1;
  if (level === 2) return HeadingLevel.HEADING_2;
  if (level === 3) return HeadingLevel.HEADING_3;
  return HeadingLevel.HEADING_4;
}

function blockToDocx(block: CanonicalMarkdownBlock): DocxBlock | DocxBlock[] {
  if (block.type === "heading") {
    return new Paragraph({
      heading: headingLevel(block.level),
      children: textRuns(block.children),
    });
  }
  if (block.type === "paragraph") {
    return new Paragraph({
      children: textRuns(block.children),
      spacing: { after: 160 },
    });
  }
  if (block.type === "blockquote") {
    return block.children.map(
      (child) =>
        new Paragraph({
          children:
            child.type === "paragraph"
              ? textRuns(child.children)
              : [new TextRun({ text: inlineBlockText(child), italics: true })],
          indent: { left: 360 },
          border: {
            left: { style: BorderStyle.SINGLE, color: "9AA7B5", size: 8 },
          },
          spacing: { after: 120 },
        }),
    );
  }
  if (block.type === "list") {
    return block.items.flatMap((item) =>
      item.map(
        (child) =>
          new Paragraph({
            children:
              child.type === "paragraph"
                ? textRuns(child.children)
                : [new TextRun(inlineBlockText(child))],
            bullet: block.ordered ? undefined : { level: 0 },
            numbering: block.ordered
              ? { reference: "canonical-numbering", level: 0 }
              : undefined,
          }),
      ),
    );
  }
  if (block.type === "code") {
    return new Paragraph({
      children: [new TextRun({ text: block.value, font: "Consolas" })],
      shading: { fill: "F5F7FA" },
      spacing: { after: 160 },
    });
  }
  if (block.type === "thematic_break") {
    return new Paragraph({
      border: {
        bottom: { color: "D1D5DB", size: 6, style: BorderStyle.SINGLE },
      },
    });
  }
  if (block.type === "table") {
    return new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: block.rows.map(
        (row) =>
          new TableRow({
            tableHeader: row.some((cell) => cell.header),
            children: row.map(
              (cell) =>
                new TableCell({
                  shading: cell.header ? { fill: "F1F4F8" } : undefined,
                  children: [
                    new Paragraph({
                      children: textRuns(cell.children),
                      alignment: cell.header ? AlignmentType.CENTER : AlignmentType.LEFT,
                    }),
                  ],
                }),
            ),
          }),
      ),
    });
  }
  return new Paragraph("");
}

export async function writeEditableDocxFromCanonicalMarkdown(
  absPath: string,
  document: CanonicalMarkdownDocument,
  _options: CanonicalMarkdownRenderOptions,
): Promise<void> {
  const children = document.blocks.flatMap((block) => blockToDocx(block));
  const doc = new Document({
    numbering: {
      config: [
        {
          reference: "canonical-numbering",
          levels: [
            {
              level: 0,
              format: "decimal",
              text: "%1.",
              alignment: AlignmentType.LEFT,
            },
          ],
        },
      ],
    },
    sections: [{ properties: {}, children }],
  });
  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, await Packer.toBuffer(doc));
}

export async function writeFixedLayoutDocxFromPngPages(
  absPath: string,
  pages: Array<{ page: number; pngBytes: Buffer; width: number; height: number }>,
  page: { size: "A4" | "Letter"; margin: string },
): Promise<void> {
  if (pages.length === 0) {
    throw new Error("fixed-layout DOCX requires at least one rendered page image");
  }
  const children = pages.map(
    (renderedPage) =>
      new Paragraph({
        children: [
          new ImageRun({
            data: renderedPage.pngBytes,
            type: "png",
            transformation: {
              width: page.size === "A4" ? 794 : 816,
              height: page.size === "A4" ? 1123 : 1056,
            },
          }),
        ],
        spacing: { after: 0 },
      }),
  );
  const doc = new Document({ sections: [{ properties: {}, children }] });
  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, await Packer.toBuffer(doc));
}
