/**
 * ChannelAdapter — abstract interface for inbound/outbound message
 * channels (Telegram long-polling, Discord gateway client, future
 * WhatsApp / Slack / etc).
 *
 * Design reference: docs/plans/2026-04-19-core-agent-refactor-plan.md
 * §2 "C1 — Native Telegram + Discord polling".
 *
 * Why this exists: legacy gateway's `node-host` sidecar owned channel I/O
 * for legacy bots. Core-agent replaces the gateway side of legacy gateway
 * but must also absorb node-host's Telegram poller + discord.js
 * client; otherwise bots migrated to core-agent go dark the moment
 * they reply through Telegram/Discord. See C1 spec for the
 * `Clawy_Bot` 2026-04-19 rollback postmortem that motivated this.
 *
 * Invariant A — source channel = delivery channel (cron-routing fix,
 * see CronScheduler.ts docstring). A poller owns both "inbound
 * handler fires" and "outbound send()"; Session.meta.channel and
 * Session.meta.chatId are set from the inbound message so every
 * downstream NotifyUser / cron fire inherits the right target
 * without the LLM picking a `--target` flag.
 */

/**
 * Quoted-reply descriptor lifted off an upstream reply-to field. Both
 * Telegram (`message.reply_to_message`) and Discord
 * (`message.reference` + resolved `channel.messages.cache`) expose the
 * message being replied to — when present, the adapter normalises it
 * into this shape so the Agent can inject a `[Reply to …]` preamble
 * on the LLM user message.
 *
 * `role` defaults to `user` because adapters don't yet maintain a
 * bot-own-message-id table; DiscordClient sets `assistant` when the
 * quoted message's author id equals the bot client's user id. For
 * Telegram we lack that mapping without tracking sent message ids,
 * so we conservatively stamp `user`.
 */
export interface InboundReplyTo {
  messageId: string;
  preview: string;
  role: "user" | "assistant";
}

/** Attachment downloaded by a channel adapter (e.g. Telegram photo/document). */
export interface InboundAttachment {
  kind: "image" | "file" | "audio";
  name: string;
  mimeType?: string;
  localPath: string;
  sizeBytes?: number;
}

/** Normalised inbound payload the Agent's router consumes. */
export interface InboundMessage {
  channel: "telegram" | "discord";
  /** Upstream thread id: Telegram `chat.id` as string, or Discord `channel.id`. */
  chatId: string;
  /** Upstream sender id: Telegram `from.id` as string, or Discord `author.id`. */
  userId: string;
  text: string;
  /** Upstream message id — captured so replies can thread. */
  messageId: string;
  /**
   * Populated iff the upstream message quoted a previous message. The
   * Agent forwards this into `UserMessage.metadata.replyTo` so
   * MessageBuilder renders the `[Reply to …]` preamble.
   */
  replyTo?: InboundReplyTo;
  /** Downloaded file attachments (populated by channel adapters). */
  attachments?: InboundAttachment[];
  /** Original payload, retained for debug logging / hooks. */
  raw: unknown;
}

/** Outbound text reply. */
export interface OutboundMessage {
  chatId: string;
  text: string;
  /** Optional reply-to threading. Telegram: reply_to_message_id;
   * Discord: message reference. Adapters may ignore if unsupported. */
  replyToMessageId?: string;
}

/** Handler registered by the Agent — fired once per inbound message. */
export type InboundHandler = (msg: InboundMessage) => Promise<void>;

/**
 * All channel adapters implement this. `start()` begins polling / opens
 * the gateway connection. `stop()` is cooperative — adapters must
 * abort in-flight long-polls so process shutdown is prompt.
 */
export interface ChannelAdapter {
  readonly kind: "telegram" | "discord";
  start(): Promise<void>;
  stop(): Promise<void>;
  onInboundMessage(handler: InboundHandler): void;
  send(msg: OutboundMessage): Promise<void>;
  sendDocument(chatId: string, filePath: string, caption?: string): Promise<void>;
  sendPhoto(chatId: string, filePath: string, caption?: string): Promise<void>;
  /**
   * Fire-and-forget "user sees bot is typing" indicator.
   *
   * Telegram: `sendChatAction` with `action=typing` — displays the
   * indicator for ~5s; caller should re-invoke every ~4s while the
   * turn is still streaming. Discord: `channel.sendTyping()` —
   * displays for ~10s per call.
   *
   * Implementations MUST NOT throw. A failure to show the indicator
   * must never kill the user's turn — log and return. Used by
   * {@link startTypingTicker} during `Turn` lifecycle.
   */
  sendTyping(chatId: string): Promise<void>;
}
