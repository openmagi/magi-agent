function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
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
    if (line.startsWith("# ")) {
      closeListIfNeeded();
      html.push(`<h1>${escapeHtml(line.slice(2))}</h1>`);
      continue;
    }
    if (line.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${escapeHtml(line.slice(2))}</li>`);
      continue;
    }
    if (line.trim().length === 0) {
      closeListIfNeeded();
      continue;
    }
    closeListIfNeeded();
    html.push(`<p>${escapeHtml(line)}</p>`);
  }

  closeListIfNeeded();
  html.push("</body>", "</html>");
  return html.join("\n");
}
