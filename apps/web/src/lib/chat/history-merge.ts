import type { ChatMessage } from "./types";
import { compareChatMessages } from "./message-order";

function isSameMessage(a: ChatMessage, b: ChatMessage): boolean {
  return a.id === b.id || (!!a.serverId && !!b.serverId && a.serverId === b.serverId);
}

function insertSystemDividers(messages: ChatMessage[], dividers: ChatMessage[]): ChatMessage[] {
  const merged = [...messages];
  const sortedDividers = [...dividers].sort(compareChatMessages);
  for (const divider of sortedDividers) {
    if (merged.some((msg) => msg.id === divider.id)) continue;
    const ts = divider.timestamp || 0;
    let idx = merged.length;
    for (let i = merged.length - 1; i >= 0; i--) {
      if ((merged[i].timestamp || 0) <= ts) {
        idx = i + 1;
        break;
      }
    }
    merged.splice(idx, 0, divider);
  }
  return merged;
}

export function mergeChatHistoryPage(
  existing: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const systemDividers = existing.filter((message) => message.role === "system");
  const merged: ChatMessage[] = [];

  const addOrReplace = (message: ChatMessage) => {
    if (message.role === "system") return;
    const idx = merged.findIndex((candidate) => isSameMessage(candidate, message));
    if (idx >= 0) {
      merged[idx] = { ...merged[idx], ...message };
    } else {
      merged.push(message);
    }
  };

  existing.forEach(addOrReplace);
  incoming.forEach(addOrReplace);

  return insertSystemDividers(merged.sort(compareChatMessages), systemDividers);
}
