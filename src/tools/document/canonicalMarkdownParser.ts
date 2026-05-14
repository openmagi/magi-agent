import crypto from "node:crypto";
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import type {
  Blockquote,
  Code,
  Delete,
  Emphasis,
  Heading,
  Html,
  Image,
  InlineCode,
  Link,
  List,
  Paragraph,
  PhrasingContent,
  Root,
  RootContent,
  Strong,
  Table,
  Text,
} from "mdast";
import type {
  CanonicalInline,
  CanonicalMarkdownBlock,
  CanonicalMarkdownDocument,
  CanonicalTableCell,
} from "./canonicalMarkdownTypes.js";

function hashSource(markdown: string): string {
  return crypto.createHash("sha256").update(markdown, "utf8").digest("hex");
}

function assertNoRawHtml(node: unknown): void {
  if (!node || typeof node !== "object") return;
  const typed = node as { type?: unknown; value?: unknown; children?: unknown };
  if (typed.type === "html") {
    const html = typed as Html;
    throw new Error(
      `raw html is not supported in canonical Markdown export: ${html.value.slice(0, 40)}`,
    );
  }
  if (Array.isArray(typed.children)) {
    for (const child of typed.children) {
      assertNoRawHtml(child);
    }
  }
}

function textInline(value: string): CanonicalInline {
  return { type: "text", value };
}

function inlineChildren(children: PhrasingContent[]): CanonicalInline[] {
  const out: CanonicalInline[] = [];
  for (const child of children) {
    if (child.type === "text") {
      out.push(textInline((child as Text).value));
    } else if (child.type === "strong") {
      out.push({
        type: "strong",
        children: inlineChildren((child as Strong).children),
      });
    } else if (child.type === "emphasis") {
      out.push({
        type: "emphasis",
        children: inlineChildren((child as Emphasis).children),
      });
    } else if (child.type === "inlineCode") {
      out.push({ type: "inline_code", value: (child as InlineCode).value });
    } else if (child.type === "link") {
      const link = child as Link;
      out.push({
        type: "link",
        url: link.url,
        children: inlineChildren(link.children),
      });
    } else if (child.type === "image") {
      const image = child as Image;
      out.push({ type: "image", url: image.url, alt: image.alt ?? "" });
    } else if (child.type === "delete") {
      out.push(...inlineChildren((child as Delete).children));
    } else if (child.type === "break") {
      out.push(textInline("\n"));
    }
  }
  return out;
}

function blockChildren(children: RootContent[]): CanonicalMarkdownBlock[] {
  const out: CanonicalMarkdownBlock[] = [];
  for (const child of children) {
    const block = blockFromNode(child);
    if (block) out.push(block);
  }
  return out;
}

function tableCells(table: Table): CanonicalTableCell[][] {
  return table.children.map((row, rowIndex) =>
    row.children.map((cell): CanonicalTableCell => ({
      header: rowIndex === 0,
      children: inlineChildren(cell.children),
    })),
  );
}

function inlineRootBlock(node: PhrasingContent): CanonicalMarkdownBlock {
  return {
    type: "paragraph",
    children: inlineChildren([node]),
  };
}

function blockFromNode(node: RootContent): CanonicalMarkdownBlock | null {
  if (node.type === "heading") {
    const heading = node as Heading;
    return {
      type: "heading",
      level: Math.min(Math.max(heading.depth, 1), 6) as 1 | 2 | 3 | 4 | 5 | 6,
      children: inlineChildren(heading.children),
    };
  }
  if (node.type === "paragraph") {
    return {
      type: "paragraph",
      children: inlineChildren((node as Paragraph).children),
    };
  }
  if (node.type === "blockquote") {
    return {
      type: "blockquote",
      children: blockChildren((node as Blockquote).children),
    };
  }
  if (node.type === "list") {
    const list = node as List;
    return {
      type: "list",
      ordered: Boolean(list.ordered),
      items: list.children.map((item) => blockChildren(item.children)),
    };
  }
  if (node.type === "code") {
    const code = node as Code;
    return { type: "code", lang: code.lang ?? undefined, value: code.value };
  }
  if (node.type === "thematicBreak") {
    return { type: "thematic_break" };
  }
  if (node.type === "table") {
    const table = node as Table;
    return {
      type: "table",
      align: table.align ?? [],
      rows: tableCells(table),
    };
  }
  if (
    node.type === "text" ||
    node.type === "strong" ||
    node.type === "emphasis" ||
    node.type === "inlineCode" ||
    node.type === "link" ||
    node.type === "image" ||
    node.type === "delete" ||
    node.type === "break"
  ) {
    return inlineRootBlock(node);
  }
  return null;
}

export function parseCanonicalMarkdown(
  markdown: string,
): CanonicalMarkdownDocument {
  const root = fromMarkdown(markdown, {
    extensions: [gfm()],
    mdastExtensions: [gfmFromMarkdown()],
  }) as Root;
  assertNoRawHtml(root);
  return {
    sourceMarkdown: markdown,
    sourceHash: hashSource(markdown),
    blocks: blockChildren(root.children),
  };
}
