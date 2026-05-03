/**
 * Shared value types used across core-agent.
 * Design reference: docs/plans/2026-04-19-clawy-core-agent-design.md §5.
 */

export type ChannelType = "app" | "telegram" | "discord" | "internal";

export interface ChannelRef {
  type: ChannelType;
  channelId: string;
}

/**
 * Quoted-reply context a channel or client attaches when the user is
 * replying to a specific earlier message. Consumed by MessageBuilder
 * to prepend a `[Reply to {role}: "{preview}"]` preamble so the LLM
 * knows which message the user is actually answering.
 *
 * - `messageId`: upstream id of the message being replied to. Typed as
 *   string to stay channel-agnostic (Telegram message_id is numeric
 *   but fits comfortably; Discord uses snowflake strings directly;
 *   web/app use server-side UUIDs).
 * - `preview`: human-readable excerpt of the quoted message. Truncated
 *   to 200 chars at injection time.
 * - `role`: who wrote the quoted message — `user` or `assistant`. For
 *   channel adapters where we don't yet track bot-own-message mapping
 *   (Telegram reply_to_message without a message id table), callers
 *   should default to `user`.
 */
export interface ReplyToRef {
  messageId: string;
  preview: string;
  role: "user" | "assistant";
}

/**
 * Structured per-message metadata slots that MessageBuilder +
 * ChannelDispatcher consume. Kept as a named subtype so the
 * `UserMessage.metadata` bag stays open (channels can still shove
 * ad-hoc keys like `chatId`/`userId`) while the reply-to shape is
 * type-checked everywhere it matters.
 */
export interface UserMessageMetadata {
  replyTo?: ReplyToRef;
  systemPromptAddendum?: string;
  /**
   * Mid-turn injection marker (#86). Present when this message was
   * queued via POST /v1/chat/inject while another turn was already
   * streaming — the midTurnInjector hook wraps it as a neutral
   * follow-up user message so the LLM knows this is not the original
   * request.
   */
  injection?: {
    id: string;
    source: "web" | "mobile" | "telegram" | "discord" | "api";
  };
  [key: string]: unknown;
}

/** Anthropic-native image content block for vision. */
export interface ImageContentBlock {
  type: "image";
  source: {
    type: "base64";
    media_type: "image/jpeg" | "image/png" | "image/gif" | "image/webp";
    data: string;
  };
}

/** Message a channel hands to the agent. */
export interface UserMessage {
  text: string;
  attachments?: MessageAttachment[];
  /**
   * Pre-encoded image content blocks (Anthropic vision format).
   * Set by extractLastUserMessage when chat-proxy injects base64
   * image_url blocks, or by MessageBuilder when reading Telegram
   * photo attachments from disk.
   */
  imageBlocks?: ImageContentBlock[];
  metadata?: UserMessageMetadata;
  /** ms since epoch — provided by channel, NOT trusted for ordering. */
  receivedAt: number;
}

export interface MessageAttachment {
  kind: "image" | "file" | "audio";
  url?: string;
  name?: string;
  mimeType?: string;
  sizeBytes?: number;
  /** Local filesystem path (set when file was downloaded by a channel adapter). */
  localPath?: string;
}

/** Anthropic-compatible content block (subset). */
export type ContentBlock =
  | { type: "text"; text: string }
  | {
      type: "image";
      source: {
        type: "base64";
        media_type: "image/jpeg" | "image/png" | "image/gif" | "image/webp";
        data: string;
      };
    }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface Message {
  role: "user" | "assistant" | "system";
  content: string | ContentBlock[];
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}
