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
  const systemDividers = [...existing, ...incoming].filter((message) => message.role === "system");
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

function isSessionResetDivider(message: ChatMessage): boolean {
  if (message.role !== "system") return false;
  const content = typeof message.content === "string" ? message.content.toLowerCase() : "";
  return message.id.startsWith("system-reset-") || content.includes("session ended");
}

function afterLatestResetDivider(messages: ChatMessage[]): ChatMessage[] {
  let resetIndex = -1;
  for (let index = 0; index < messages.length; index += 1) {
    if (isSessionResetDivider(messages[index])) {
      resetIndex = index;
    }
  }
  return resetIndex >= 0 ? messages.slice(resetIndex + 1) : messages;
}

function isOperationalCanaryPrompt(message: ChatMessage): boolean {
  if (message.role !== "user" || typeof message.content !== "string") return false;
  const content = message.content.trim();
  return (
    content.includes("SAFE CANARY ONLY") ||
    /^canary\b/i.test(content) ||
    /\bcanary\s+(smoke|route|kb|contextual|test|diagnostic)\b/i.test(content) ||
    /^\s*(?:smoke|diagnostic)\s+(?:test|check|route|canary)\b/i.test(content) ||
    /\binternal\s+diagnostic\b/i.test(content)
  );
}

function scrubOperationalCanaryExchanges(messages: ChatMessage[]): ChatMessage[] {
  const scrubbed: ChatMessage[] = [];
  let skippingCanaryReplies = false;

  for (const message of messages) {
    if (isSessionResetDivider(message)) {
      skippingCanaryReplies = false;
      scrubbed.push(message);
      continue;
    }

    if (message.role === "user") {
      if (isOperationalCanaryPrompt(message)) {
        skippingCanaryReplies = true;
        continue;
      }
      skippingCanaryReplies = false;
      scrubbed.push(message);
      continue;
    }

    if (message.role === "assistant" && skippingCanaryReplies) continue;
    scrubbed.push(message);
  }

  return scrubbed;
}

export function buildOutboundChatContext(
  localMessages: ChatMessage[],
  serverMessages: ChatMessage[],
  limit = 24,
): Pick<ChatMessage, "role" | "content">[] {
  const merged = scrubOperationalCanaryExchanges(
    afterLatestResetDivider(mergeChatHistoryPage(serverMessages, localMessages)),
  )
    .filter((message) => (
      (message.role === "user" || message.role === "assistant") &&
      typeof message.content === "string" &&
      message.content.trim().length > 0
    ));

  const boundedLimit = Math.max(1, Math.min(100, Math.floor(limit)));
  return merged.slice(-boundedLimit).map((message) => ({
    role: message.role,
    content: message.content,
  }));
}
