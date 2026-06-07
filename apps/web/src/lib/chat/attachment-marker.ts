/** Regex to match [attachment:{uuid}:{filename}] markers in message content */
const MARKER_RE = /\[attachment:([0-9a-f-]{36}):([^\]]+)\]/g;

export interface AttachmentMarker {
  id: string;
  filename: string;
  fullMatch: string;
  index: number;
}

/** Parse all attachment markers from message content */
export function parseMarkers(content: string): AttachmentMarker[] {
  const markers: AttachmentMarker[] = [];
  let match: RegExpExecArray | null;
  const re = new RegExp(MARKER_RE.source, MARKER_RE.flags);
  while ((match = re.exec(content)) !== null) {
    markers.push({
      id: match[1],
      filename: match[2],
      fullMatch: match[0],
      index: match.index,
    });
  }
  return markers;
}

/** Create a marker string for embedding in message content */
export function createMarker(id: string, filename: string): string {
  return `[attachment:${id}:${filename}]`;
}

/** Check if a mimetype is an image type */
export function isImageMimetype(mimetype: string): boolean {
  return /^image\/(jpeg|png|gif|webp)$/.test(mimetype);
}

/** Format file size for display */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Build a short plain-text preview of a message body for use in reply banners.
 * Strips attachment markers, common markdown formatting, and collapses whitespace.
 */
export function buildReplyPreview(content: string, maxLen = 80): string {
  let text = content;
  // Strip attachment markers entirely
  text = text.replace(MARKER_RE, "").replace(/\[attachment:[0-9a-f-]{36}:[^\]]+\]/g, "");
  // Fenced code blocks: replace with language name or "code"
  text = text.replace(/```(\w*)[\s\S]*?```/g, (_m, lang) => (lang ? `[${lang} code]` : "[code]"));
  // Inline code
  text = text.replace(/`([^`]+)`/g, "$1");
  // Images: ![alt](url) -> alt
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
  // Links: [label](url) -> label
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  // Bold / italic markers
  text = text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1");
  text = text.replace(/\*(.+?)\*/g, "$1").replace(/_(.+?)_/g, "$1");
  // Strikethrough
  text = text.replace(/~~(.+?)~~/g, "$1");
  // Leading markdown tokens: headings, list markers, blockquote
  text = text.replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, "");
  // Collapse whitespace / newlines
  text = text.replace(/\s+/g, " ").trim();
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + "\u2026";
}
