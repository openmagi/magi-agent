import fs from "node:fs/promises";
import path from "node:path";
import type { StructuredBlock } from "./docxDriver.js";

function ensureTrailingNewline(value: string): string {
  return value.endsWith("\n") ? value : `${value}\n`;
}

export function structuredBlocksToMarkdown(blocks: StructuredBlock[]): string {
  const parts = blocks.map((block) => {
    if (block.type === "heading") {
      const level = Math.max(1, Math.min(block.level ?? 1, 3));
      return `${"#".repeat(level)} ${block.text.trim()}`;
    }
    if (block.type === "bullet") {
      return `- ${block.text.trim()}`;
    }
    if (block.type === "table") {
      return block.rows
        .map((row, index) => [
          `| ${row.map((cell) => cell.trim()).join(" | ")} |`,
          ...(index === 0 ? [`| ${row.map(() => "---").join(" | ")} |`] : []),
        ].join("\n"))
        .join("\n");
    }
    if (block.type === "horizontal_rule") {
      return "---";
    }
    return block.text.trim();
  }).filter(Boolean);

  return ensureTrailingNewline(parts.join("\n\n"));
}

export function markdownToPlainText(markdown: string): string {
  const lines: string[] = [];

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      lines.push("");
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      lines.push((heading[2] ?? "").trim());
      continue;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      lines.push((bullet[1] ?? "").trim());
      continue;
    }

    if (/^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/.test(line)) {
      continue;
    }

    if (/^\|.+\|$/.test(line)) {
      lines.push(
        line
          .split("|")
          .map((cell) => cell.trim())
          .filter(Boolean)
          .join("\t"),
      );
      continue;
    }

    lines.push(line);
  }

  return ensureTrailingNewline(lines.join("\n").replace(/\n{3,}/g, "\n\n"));
}

export function structuredBlocksToPlainText(blocks: StructuredBlock[]): string {
  return ensureTrailingNewline(
    blocks
      .map((block) => {
        if (block.type === "table") {
          return block.rows.map((row) => row.join("\t")).join("\n");
        }
        if (block.type === "horizontal_rule") {
          return "";
        }
        return block.text.trim();
      })
      .filter(Boolean)
      .join("\n\n"),
  );
}

export async function writeTextFile(absPath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, ensureTrailingNewline(content), "utf8");
}
