import fs from "node:fs/promises";
import path from "node:path";
import { Document, HeadingLevel, Packer, Paragraph } from "docx";

export interface StructuredBlock {
  type: "heading" | "paragraph";
  text: string;
  level?: 1 | 2 | 3;
}

type HeadingLevelValue = (typeof HeadingLevel)[keyof typeof HeadingLevel];

function headingLevelFor(level: StructuredBlock["level"]): HeadingLevelValue {
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
    blocks.push({ type: "paragraph", text });
  }
  lines.length = 0;
}

export function markdownToStructuredBlocks(markdown: string): StructuredBlock[] {
  const blocks: StructuredBlock[] = [];
  const paragraphLines: string[] = [];

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph(blocks, paragraphLines);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph(blocks, paragraphLines);
      const marks = heading[1] ?? "#";
      const text = heading[2] ?? "";
      blocks.push({
        type: "heading",
        level: Math.min(marks.length, 3) as 1 | 2 | 3,
        text: text.trim(),
      });
      continue;
    }

    if (/^\|.+\|$/.test(line)) {
      flushParagraph(blocks, paragraphLines);
      const cells = line
        .split("|")
        .map((cell) => cell.trim())
        .filter(Boolean);
      if (!cells.every((cell) => /^:?-{3,}:?$/.test(cell))) {
        blocks.push({ type: "paragraph", text: cells.join(" | ") });
      }
      continue;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(line);
    const bulletText = bullet?.[1];
    paragraphLines.push(bulletText ? `• ${bulletText.trim()}` : line);
  }

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
        children: blocks.map((block) =>
          block.type === "heading"
            ? new Paragraph({
                text: block.text,
                heading: headingLevelFor(block.level),
              })
            : new Paragraph({ text: block.text }),
        ),
      },
    ],
  });

  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, await Packer.toBuffer(doc));
}
