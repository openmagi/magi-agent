import { parseMarkers } from "./attachment-marker";
import { parseKbContextMarker } from "./kb-context-marker";

interface BuildMessageCopyTextOptions {
  content: string;
  selection?: string;
}

export function buildMessageCopyText({
  content,
  selection = "",
}: BuildMessageCopyTextOptions): string {
  if (selection.trim().length > 0) return selection;

  const parsed = parseKbContextMarker(content);
  let text = parsed.text;

  for (const marker of parseMarkers(text)) {
    text = text.replace(marker.fullMatch, "");
  }

  return text.trim();
}
