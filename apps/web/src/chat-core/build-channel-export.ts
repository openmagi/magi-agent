/**
 * buildChannelExport — pure helper that maps the streaming surface's ChatMessage[]
 * into the shape expected by the shared export lib, then returns
 * { filename, markdown } ready for download.
 *
 * Constraints:
 *  - Reuses export.ts (no markdown re-implementation).
 *  - No browser APIs — caller builds the Blob + anchor download.
 *  - Strict TS, no `any`.
 */
import {
  buildChatExportFilename,
  buildChatExportMarkdown,
  cleanChatExportContent,
  extractChatExportAttachments,
} from "./export";
import type { ChatExportMessage } from "./export";
import type { ChatMessage } from "./types";

export interface BuildChannelExportInput {
  botName: string;
  channelName: string;
  messages: ChatMessage[];
}

export interface BuildChannelExportResult {
  filename: string;
  markdown: string;
  /** Channel-scoped title used by the public-export API (`POST /api/chat/exports`). */
  title: string;
  /** Mapped export messages (user/assistant only) — payload for the public-export API. */
  messages: ChatExportMessage[];
}

/**
 * Map a `ChatMessage` (streaming surface) to `ChatExportMessage` (export lib).
 *
 * The export lib's `normalizeSelectedChatExportMessages` needs a `selectedIds`
 * set and runs dedup/sort logic — for a full-channel export we want ALL user +
 * assistant messages in order. We replicate the essential mapping inline:
 *   - Filter to role === "user" | "assistant" (system messages are excluded).
 *   - Sort by timestamp ascending.
 *   - Map to the ChatExportMessage shape (id, role, content, timestamp).
 *
 * We do NOT call `normalizeSelectedChatExportMessages` because it requires a
 * selectedIds set and is designed for partial selection; a full-channel export
 * is simpler to build directly (and reuses `buildChatExportMarkdown` for the
 * markdown layer, which is where the logic lives).
 */
function mapToExportMessages(messages: ChatMessage[]): ChatExportMessage[] {
  return messages
    .filter(
      (m): m is ChatMessage & { role: "user" | "assistant" } =>
        m.role === "user" || m.role === "assistant",
    )
    .sort((a, b) => a.timestamp - b.timestamp)
    .map((m): ChatExportMessage => {
      const attachments = extractChatExportAttachments(m.content);
      const content = cleanChatExportContent(m.content);
      return {
        id: m.serverId ?? m.id,
        role: m.role,
        content,
        timestamp: m.timestamp,
        ...(attachments.length > 0 ? { attachments } : {}),
      };
    })
    .filter((m) => m.content.trim().length > 0 || (m.attachments?.length ?? 0) > 0);
}

/**
 * Build a full-channel markdown export from the streaming surface's history.
 *
 * @param input.botName     - Display name of the bot (e.g. "My Bot")
 * @param input.channelName - Channel name/slug (e.g. "general")
 * @param input.messages    - Full committed history (`history` state from container)
 *
 * @returns `{ filename, markdown }` — the caller creates the Blob and download.
 */
export function buildChannelExport({
  botName,
  channelName,
  messages,
}: BuildChannelExportInput): BuildChannelExportResult {
  const exportedAt = new Date();
  const exportMessages = mapToExportMessages(messages);

  const filename = buildChatExportFilename({
    botName,
    channelName,
    exportedAt,
  });

  const markdown = buildChatExportMarkdown({
    botName,
    channelName,
    exportedAt,
    messages: exportMessages,
  });

  return { filename, markdown, title: `${botName} / ${channelName}`, messages: exportMessages };
}
