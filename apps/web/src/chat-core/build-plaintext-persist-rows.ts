// Pure helper: build the PLAINTEXT persist rows for a completed chat turn.
//
// Used by the new StreamChatContainer surface (PR3) to persist the just-finished
// user + assistant messages via POST /api/chat/messages with `{role, content,
// client_msg_id}` rows. The route wraps `content` with the plaintext sentinel
// server-side — this helper does NOT encrypt and does NOT add the sentinel.
//
// Parity note: by default the assistant row stores ONLY the visible text. If the
// caller passes thinking/usage and opts in, the assistant content is encoded via
// `encodeHistoryPlaintext` so the history loader can decode thinking/usage back.

import { encodeHistoryPlaintext } from "./history-envelope";
import type { ResearchEvidenceSnapshot, ResponseUsage, ToolActivity } from "./types";

/** A single plaintext row accepted by POST /api/chat/messages. */
export interface PlaintextPersistRow {
  role: "user" | "assistant";
  content: string;
  client_msg_id: string;
}

export interface CompletedAssistantMessage {
  /** Visible assistant text for this turn. */
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  researchEvidence?: ResearchEvidenceSnapshot;
  usage?: ResponseUsage;
  /** Tool/skill activities captured during the turn (persisted only when opted in). */
  activities?: ToolActivity[];
}

export interface BuildPlaintextPersistRowsOptions {
  /** The raw user message text that started the turn. */
  userText: string;
  /** The completed assistant message (visible text + optional metadata). */
  assistant: CompletedAssistantMessage;
  /** Stable client message id for the user row. */
  userClientMsgId: string;
  /** Stable client message id for the assistant row. */
  assistantClientMsgId: string;
  /**
   * When true, encode the assistant content via `encodeHistoryPlaintext` so
   * thinking/usage round-trip through the history loader. Default false →
   * assistant content is the visible text only (simplest, preserves the
   * visible message).
   */
  includeAssistantMetadata?: boolean;
  /**
   * When true (AND `includeAssistantMetadata`), the assistant's tool activities
   * are encoded into the `_v:4` envelope so the "Completed N actions" timeline
   * survives reload. Default false → activities are NOT persisted and the
   * envelope stays byte-identical to the v2/v3 form. Gated by the app-layer
   * flag `MAGI_PERSIST_TOOL_ACTIVITY` (default-OFF).
   */
  persistToolActivity?: boolean;
}

/**
 * Build the `[user, assistant]` plaintext rows for a completed turn.
 *
 * Returns `[]` when there is nothing worth persisting (both texts empty).
 * Rows are PLAINTEXT — never encrypted, never sentinel-prefixed (the API route
 * adds the sentinel). `client_msg_id`s are always present so the upsert can
 * dedupe on conflict.
 */
export function buildPlaintextPersistRows(
  opts: BuildPlaintextPersistRowsOptions,
): PlaintextPersistRow[] {
  const userText = opts.userText.trim();
  const assistantText = opts.assistant.content;

  if (!userText && !assistantText) return [];

  const rows: PlaintextPersistRow[] = [];

  if (userText) {
    rows.push({
      role: "user",
      content: userText,
      client_msg_id: opts.userClientMsgId,
    });
  }

  if (assistantText) {
    const content = opts.includeAssistantMetadata
      ? encodeHistoryPlaintext({
          role: "assistant",
          content: assistantText,
          ...(opts.assistant.thinkingContent !== undefined
            ? { thinkingContent: opts.assistant.thinkingContent }
            : {}),
          ...(opts.assistant.thinkingDuration !== undefined
            ? { thinkingDuration: opts.assistant.thinkingDuration }
            : {}),
          ...(opts.assistant.researchEvidence !== undefined
            ? { researchEvidence: opts.assistant.researchEvidence }
            : {}),
          ...(opts.assistant.usage !== undefined
            ? { usage: opts.assistant.usage }
            : {}),
          ...(opts.persistToolActivity && opts.assistant.activities?.length
            ? { activities: opts.assistant.activities }
            : {}),
        })
      : assistantText;

    rows.push({
      role: "assistant",
      content,
      client_msg_id: opts.assistantClientMsgId,
    });
  }

  return rows;
}
