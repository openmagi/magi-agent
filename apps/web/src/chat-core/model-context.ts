import { compareChatMessages } from "./message-order";
import type { ChatMessage } from "./types";

// Generous client history cap (design budget: 31 -> 64). The wider window
// hardens multi-turn continuity when a stream tail drops, matching the pod-side
// sanitized-history bump.
const DEFAULT_MAX_MODEL_CONTEXT_MESSAGES = 64;
const DUPLICATE_WINDOW_MS = 5 * 60_000;

/** Client-side session reset dividers carry this id prefix (see chat-store resetSession). */
const RESET_DIVIDER_ID_PREFIX = "system-reset-";

function isSessionResetDivider(message: ChatMessage): boolean {
  return message.role === "system" && message.id.startsWith(RESET_DIVIDER_ID_PREFIX);
}

/**
 * Timestamp of the most recent session reset. The runtime is stateless and
 * replays whatever history we send, so a "Reset" must drop everything authored
 * before the divider — otherwise the prior conversation bleeds into the new
 * session. Returns 0 when no reset divider is present.
 */
function latestResetBoundaryTimestamp(messages: readonly ChatMessage[]): number {
  let boundary = 0;
  for (const message of messages) {
    if (isSessionResetDivider(message)) {
      boundary = Math.max(boundary, message.timestamp ?? 0);
    }
  }
  return boundary;
}

function normalizeResetBoundaryTimestamp(value?: number | null): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : 0;
}

function normalizedContent(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function isDuplicateMessage(a: ChatMessage, b: ChatMessage): boolean {
  if (a.id && b.id && a.id === b.id) return true;
  if (a.serverId && b.serverId && a.serverId === b.serverId) return true;
  if (a.serverId && b.id && a.serverId === b.id) return true;
  if (b.serverId && a.id && b.serverId === a.id) return true;
  if (a.role !== b.role) return false;

  const aContent = normalizedContent(a.content);
  const bContent = normalizedContent(b.content);
  if (!aContent || aContent !== bContent) return false;

  return Math.abs((a.timestamp ?? 0) - (b.timestamp ?? 0)) <= DUPLICATE_WINDOW_MS;
}

function sanitizeForModelContext(message: ChatMessage): ChatMessage | null {
  if (message.role === "system") return null;
  const content = message.content.trim();
  if (!content) return null;
  return {
    ...message,
    content,
  };
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

function dedupeVisibleMessages(messages: ChatMessage[]): ChatMessage[] {
  const deduped: ChatMessage[] = [];
  for (const message of messages) {
    const existingIndex = deduped.findIndex((candidate) =>
      isDuplicateMessage(candidate, message)
    );
    if (existingIndex >= 0) {
      const existing = deduped[existingIndex]!;
      deduped[existingIndex] = message.serverId && !existing.serverId
        ? message
        : existing;
      continue;
    }
    deduped.push(message);
  }
  return deduped.sort(compareChatMessages);
}

export function buildVisibleModelContextMessages(
  localMessages: readonly ChatMessage[],
  serverMessages: readonly ChatMessage[],
  maxMessages = DEFAULT_MAX_MODEL_CONTEXT_MESSAGES,
  resetBoundaryTimestamp?: number | null,
): ChatMessage[] {
  const resetBoundaryTs = Math.max(
    latestResetBoundaryTimestamp(localMessages),
    normalizeResetBoundaryTimestamp(resetBoundaryTimestamp),
  );
  const postResetLocalCandidates = localMessages
    .filter((message) => resetBoundaryTs === 0 || (message.timestamp ?? 0) > resetBoundaryTs);
  const postResetLocalMessages = postResetLocalCandidates
    .map(sanitizeForModelContext)
    .filter((message): message is ChatMessage => message !== null);
  // After reset, server-only rows are not session-scoped. Keep only server
  // copies that match a local message already known to be in this reset epoch.
  const trustedServerCandidates = serverMessages.filter((message) => {
    if (resetBoundaryTs === 0) return true;
    if ((message.timestamp ?? 0) <= resetBoundaryTs) return false;
    return postResetLocalMessages.some((localMessage) =>
      isDuplicateMessage(localMessage, message)
    );
  });
  const merged = dedupeVisibleMessages(
    scrubOperationalCanaryExchanges(
      [...postResetLocalCandidates, ...trustedServerCandidates].sort(compareChatMessages),
    )
      .map(sanitizeForModelContext)
      .filter((message): message is ChatMessage => message !== null),
  );
  const boundedMax = Math.max(1, Math.floor(maxMessages));
  if (merged.length <= boundedMax) return merged;

  const latest = merged.slice(-boundedMax);
  const latestUser = [...merged].reverse().find((message) => message.role === "user");
  if (!latestUser || latest.some((message) => isDuplicateMessage(message, latestUser))) {
    return latest;
  }

  return [...latest.slice(1), latestUser].sort(compareChatMessages);
}
