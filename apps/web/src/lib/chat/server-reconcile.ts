import type { ChatMessage, ServerMessage } from "./types";

const REPLACEMENT_CHAR = "\uFFFD";
const SERVER_PATCH_EXTRA_CHARS = 20;

export function shouldPatchAssistantTextFromServer(
  localContent: string,
  serverContent: string,
): boolean {
  if (!serverContent || serverContent === localContent) return false;
  if (serverContent.length > localContent.length + SERVER_PATCH_EXTRA_CHARS) return true;
  return localContent.includes(REPLACEMENT_CHAR) && !serverContent.includes(REPLACEMENT_CHAR);
}

export function findLatestAssistantServerMessage(messages: ServerMessage[]): ServerMessage | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role === "assistant" && message.content) return message;
  }
  return null;
}

export function shouldPreferServerAssistantMessage(
  local: ChatMessage,
  server: ChatMessage,
  proximityWindowMs: number,
): boolean {
  if (local.role !== "assistant" || server.role !== "assistant") return false;
  if (local.serverId) return false;
  if (!local.id.startsWith("assistant-")) return false;
  if (!Number.isFinite(local.timestamp) || !Number.isFinite(server.timestamp)) return false;
  if (Math.abs(server.timestamp - local.timestamp) > proximityWindowMs) return false;
  return shouldPatchAssistantTextFromServer(local.content, server.content);
}
