import type { ChatMessage, ServerMessage } from "./types";
import { stripResearchEvidenceMarker } from "./research-evidence";
import { stripAssistantMetadataPreamble } from "./visible-content";

const REPLACEMENT_CHAR = "\uFFFD";
const SERVER_PATCH_EXTRA_CHARS = 20;

function normalizedVisibleAssistantText(content: string): string {
  return stripResearchEvidenceMarker(stripAssistantMetadataPreamble(content))
    .replace(/\s+/g, " ")
    .trim();
}

export function shouldPatchAssistantTextFromServer(
  localContent: string,
  serverContent: string,
): boolean {
  if (!serverContent || serverContent === localContent) return false;
  const localVisible = normalizedVisibleAssistantText(localContent);
  const serverVisible = normalizedVisibleAssistantText(serverContent);
  if (serverVisible && serverVisible === localVisible) return false;
  if (serverVisible.length > localVisible.length + SERVER_PATCH_EXTRA_CHARS) return true;
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
