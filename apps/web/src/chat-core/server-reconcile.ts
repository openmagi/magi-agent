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

/** Terminal/incompleteness signal carried by a durable server row. */
export interface ServerCopyMeta {
  incomplete?: boolean;
  terminal?: string | null;
}

function serverCopyIsIncomplete(meta?: ServerCopyMeta): boolean {
  if (!meta) return false;
  return meta.incomplete === true || meta.terminal === "error" || meta.terminal === "aborted";
}

export function shouldPatchAssistantTextFromServer(
  localContent: string,
  serverContent: string,
  serverMeta?: ServerCopyMeta,
): boolean {
  if (!serverContent || serverContent === localContent) return false;
  // A server row that ended on an error/aborted terminal (or is otherwise
  // flagged incomplete) must NEVER replace a locally-finalized streamed copy: it
  // is by definition a truncated/partial answer, and letting it win is exactly
  // how the finished streamed answer vanished and reappeared truncated.
  if (serverCopyIsIncomplete(serverMeta)) return false;
  const localVisible = normalizedVisibleAssistantText(localContent);
  const serverVisible = normalizedVisibleAssistantText(serverContent);
  if (serverVisible && serverVisible === localVisible) return false;
  // A longer complete server copy patches the truncated streamed copy. A shorter
  // (complete) server copy never wins here -- the only shorter-allowed path is the
  // replacement-char rescue below (corrupt local text -> clean server text), and
  // that too is already gated by the incomplete check above so a truncated error
  // row can never masquerade as the rescue.
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
  return shouldPatchAssistantTextFromServer(local.content, server.content, {
    incomplete: server.incomplete,
    terminal: server.terminal,
  });
}
