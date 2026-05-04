function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderInlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

export function renderMarkdownToHtml(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  const html: string[] = ["<!doctype html>", "<html>", "<body>"];
  let inList = false;

  const closeListIfNeeded = (): void => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  for (const line of lines) {
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = (heading[1] ?? "").length;
      const text = heading[2] ?? "";
      closeListIfNeeded();
      html.push(`<h${level}>${renderInlineMarkdown(text)}</h${level}>`);
      continue;
    }
    if (line.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${renderInlineMarkdown(line.slice(2))}</li>`);
      continue;
    }
    if (line.trim().length === 0) {
      closeListIfNeeded();
      continue;
    }
    closeListIfNeeded();
    html.push(`<p>${renderInlineMarkdown(line)}</p>`);
  }

  closeListIfNeeded();
  html.push("</body>", "</html>");
  return html.join("\n");
}
