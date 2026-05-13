import type {
  CanonicalInline,
  CanonicalMarkdownBlock,
  CanonicalMarkdownDocument,
  CanonicalMarkdownHtmlOutput,
  CanonicalMarkdownRenderOptions,
} from "./canonicalMarkdownTypes.js";

const RENDERER_VERSION = "canonical-markdown-renderer/1";

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineHtml(inline: CanonicalInline): string {
  if (inline.type === "text") return escapeHtml(inline.value);
  if (inline.type === "strong") {
    return `<strong>${inline.children.map(inlineHtml).join("")}</strong>`;
  }
  if (inline.type === "emphasis") {
    return `<em>${inline.children.map(inlineHtml).join("")}</em>`;
  }
  if (inline.type === "inline_code") {
    return `<code>${escapeHtml(inline.value)}</code>`;
  }
  if (inline.type === "link") {
    const safeUrl =
      inline.url.startsWith("http://") || inline.url.startsWith("https://")
        ? inline.url
        : "#";
    return `<a href="${escapeHtml(safeUrl)}">${inline.children.map(inlineHtml).join("")}</a>`;
  }
  if (inline.type === "image") {
    return `<figure><img src="${escapeHtml(inline.url)}" alt="${escapeHtml(inline.alt)}"><figcaption>${escapeHtml(inline.alt)}</figcaption></figure>`;
  }
  return "";
}

function blockHtml(block: CanonicalMarkdownBlock): string {
  if (block.type === "heading") {
    return `<h${block.level}>${block.children.map(inlineHtml).join("")}</h${block.level}>`;
  }
  if (block.type === "paragraph") {
    return `<p>${block.children.map(inlineHtml).join("")}</p>`;
  }
  if (block.type === "blockquote") {
    return `<blockquote>${block.children.map(blockHtml).join("\n")}</blockquote>`;
  }
  if (block.type === "list") {
    const tag = block.ordered ? "ol" : "ul";
    return `<${tag}>${block.items
      .map((item) => `<li>${item.map(blockHtml).join("\n")}</li>`)
      .join("")}</${tag}>`;
  }
  if (block.type === "code") {
    const languageClass = block.lang
      ? ` class="language-${escapeHtml(block.lang)}"`
      : "";
    return `<pre><code${languageClass}>${escapeHtml(block.value)}</code></pre>`;
  }
  if (block.type === "thematic_break") {
    return "<hr>";
  }
  if (block.type === "table") {
    const rows = block.rows.map((row) => {
      const tag = row.some((cell) => cell.header) ? "th" : "td";
      return `<tr>${row
        .map((cell) => `<${tag}>${cell.children.map(inlineHtml).join("")}</${tag}>`)
        .join("")}</tr>`;
    });
    return `<table>${rows.join("")}</table>`;
  }
  return "";
}

function cssFor(options: CanonicalMarkdownRenderOptions): string {
  return [
    `@page { size: ${options.page.size}; margin: ${options.page.margin}; }`,
    ":root { color-scheme: light; }",
    'body { font-family: "Inter", "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans CJK SC", Arial, sans-serif; color: #1f2933; font-size: 11.5pt; line-height: 1.55; }',
    "main { max-width: 760px; margin: 0 auto; }",
    "h1 { font-size: 24pt; line-height: 1.18; margin: 0 0 16pt; border-bottom: 1px solid #d8dde6; padding-bottom: 10pt; }",
    "h2 { font-size: 16pt; margin: 22pt 0 8pt; border-bottom: 1px solid #e4e7ec; padding-bottom: 4pt; }",
    "h3 { font-size: 13pt; margin: 16pt 0 6pt; }",
    "p { margin: 0 0 9pt; }",
    "table { border-collapse: collapse; width: 100%; margin: 10pt 0 16pt; page-break-inside: avoid; }",
    "th, td { border: 1px solid #cfd6df; padding: 7pt 8pt; vertical-align: top; }",
    "th { background: #f1f4f8; font-weight: 700; }",
    "blockquote { border-left: 3px solid #9aa7b5; margin: 10pt 0; padding: 4pt 0 4pt 10pt; color: #4b5563; }",
    'code { font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.92em; background: #f5f7fa; padding: 1px 3px; }',
    "pre { background: #f5f7fa; padding: 9pt; white-space: pre-wrap; border: 1px solid #e1e6ee; }",
    "hr { border: 0; border-top: 2px solid #d8dde6; margin: 18pt 0; }",
    "img { max-width: 100%; height: auto; }",
  ].join("\n");
}

export function renderCanonicalMarkdownHtml(
  document: CanonicalMarkdownDocument,
  options: CanonicalMarkdownRenderOptions,
): CanonicalMarkdownHtmlOutput {
  const css = cssFor(options);
  const body = document.blocks.map(blockHtml).join("\n");
  const html = [
    "<!doctype html>",
    `<html lang="${escapeHtml(options.locale)}">`,
    "<head>",
    '<meta charset="utf-8">',
    `<title>${escapeHtml(options.title)}</title>`,
    `<style>${css}</style>`,
    "</head>",
    "<body>",
    "<main>",
    body,
    "</main>",
    "</body>",
    "</html>",
  ].join("\n");
  return { html, css, rendererVersion: RENDERER_VERSION };
}
