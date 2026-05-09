const KB_CONTEXT_RE = /^\[KB_CONTEXT:\s*(.+?)\]\n?/;

export interface KbContextRef {
  id: string;
  filename: string;
}

export function buildKbContextMarker(refs: KbContextRef[]): string {
  return `[KB_CONTEXT: ${refs.map((ref) => `${ref.id}=${ref.filename}`).join(", ")}]`;
}

export function parseKbContextMarker(content: string): {
  refs: KbContextRef[];
  text: string;
} {
  const match = content.match(KB_CONTEXT_RE);
  if (!match) {
    return { refs: [], text: content };
  }

  const refs = match[1]
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const separatorIndex = part.indexOf("=");
      return {
        id: part.slice(0, separatorIndex).trim(),
        filename: part.slice(separatorIndex + 1).trim(),
      };
    });

  return {
    refs,
    text: content.slice(match[0].length),
  };
}
