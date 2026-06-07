import { buildKbContextMarker } from "./kb-context-marker";
import type { KbDocReference } from "./types";

export function mergeKbDocReferences(
  ...groups: Array<KbDocReference[] | undefined>
): KbDocReference[] {
  const seen = new Set<string>();
  const merged: KbDocReference[] = [];

  for (const group of groups) {
    if (!group) continue;
    for (const ref of group) {
      if (seen.has(ref.id)) continue;
      seen.add(ref.id);
      merged.push(ref);
    }
  }

  return merged;
}

export function buildMessageContentWithKbContext(
  text: string,
  kbDocs: KbDocReference[],
): string {
  const trimmed = text.trim();
  if (kbDocs.length === 0) return trimmed;

  const marker = buildKbContextMarker(
    kbDocs.map((doc) => ({ id: doc.id, filename: doc.filename })),
  );

  return trimmed ? `${marker}\n${trimmed}` : marker;
}
