/**
 * DiscordClient — native discord.js adapter.
 *
 * Replaces legacy gateway node-host's discord integration. Uses discord.js v14
 * with the minimum gateway intents required for @mention + DM flow:
 *
 *   - Guilds           — channel cache + membership
 *   - GuildMessages    — messageCreate dispatch on guild channels
 *   - MessageContent   — read the message body (privileged intent — must
 *                        be enabled in the Developer Portal)
 *   - DirectMessages   — DM support
 *
 * Partials.Channel is required so DM channels (lazy-hydrated) still
 * fire messageCreate.
 *
 * Filter policy — we only hand messages to the Agent when:
 *   (a) the bot is @mentioned (`message.mentions.has(client.user)`), OR
 *   (b) the message is in a DM channel (`message.channel.isDMBased()`).
 *
 * Matches node-host's "no-noise" rule so the bot doesn't respond to
 * every message in a guild.
 *
 * Design reference: docs/plans/2026-04-19-core-agent-refactor-plan.md §2 C1.
 */

import {
  Client,
  GatewayIntentBits,
  Partials,
  Events,
  type Message,
} from "discord.js";
import type {
  ChannelAdapter,
  InboundHandler,
  InboundMessage,
  InboundReplyTo,
  OutboundMessage,
} from "./ChannelAdapter.js";

export interface DiscordClientOptions {
  botToken: string;
  workspaceRoot?: string;
  /** Injected for tests — defaults to a freshly-constructed Client. */
  clientFactory?: () => Client;
}

/**
 * Narrow structural typing — we only care about the Discord `Client`
 * methods we actually call. Keeps the test seam small + avoids
 * importing the whole Client surface in test doubles.
 */
export interface MinimalDiscordClient {
  login(token: string): Promise<string>;
  destroy(): Promise<void>;
  on(event: string, listener: (...args: unknown[]) => void): unknown;
  user: { id: string } | null;
  channels: {
    fetch(id: string): Promise<unknown>;
  };
}

export class DiscordClient implements ChannelAdapter {
  readonly kind = "discord" as const;

  private readonly botToken: string;
  private readonly client: Client;
  private handler: InboundHandler | null = null;
  private started = false;

  constructor(options: DiscordClientOptions) {
    this.botToken = options.botToken;
    this.client = options.clientFactory ? options.clientFactory() : buildDefaultClient();
  }

  onInboundMessage(handler: InboundHandler): void {
    this.handler = handler;
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.client.on(Events.MessageCreate, (message: Message) => {
      void this.dispatch(message);
    });
    await this.client.login(this.botToken);
  }

  async stop(): Promise<void> {
    if (!this.started) return;
    this.started = false;
    await this.client.destroy();
  }

  private async dispatch(message: Message): Promise<void> {
    if (!shouldDispatch(message, this.client.user?.id ?? null)) return;
    if (!this.handler) return;
    const replyTo = extractDiscordReplyTo(message, this.client.user?.id ?? null);
    const inbound: InboundMessage = {
      channel: "discord",
      chatId: message.channel.id,
      userId: message.author.id,
      text: message.content,
      messageId: message.id,
      ...(replyTo ? { replyTo } : {}),
      raw: message,
    };
    try {
      await this.handler(inbound);
    } catch (err) {
      console.warn(
        `[discord-client] inbound handler threw: ${(err as Error).message}`,
      );
    }
  }

  async send(msg: OutboundMessage): Promise<void> {
    const channel = await this.fetchSendableChannel(msg.chatId);
    await channel.send({ content: msg.text });
  }

  /**
   * Discord "is typing…" indicator via `channel.sendTyping()`. Shows
   * for ~10s per invocation — comfortably covers the 4s ticker cadence
   * shared with Telegram. Fire-and-forget: any error (channel not
   * sendable, permission revoked, gateway hiccup) is logged and
   * swallowed so the user's turn never blocks on the indicator.
   */
  async sendTyping(chatId: string): Promise<void> {
    try {
      const channel = await this.fetchSendableChannel(chatId);
      const typing = (channel as unknown as {
        sendTyping?: () => Promise<unknown>;
      }).sendTyping;
      if (typeof typing !== "function") return;
      await typing.call(channel);
    } catch (err) {
      console.warn(
        `[discord-client] sendTyping failed chat=${chatId}: ${(err as Error).message}`,
      );
    }
  }

  async sendDocument(
    chatId: string,
    filePath: string,
    caption?: string,
  ): Promise<void> {
    const channel = await this.fetchSendableChannel(chatId);
    await channel.send({
      files: [filePath],
      ...(caption ? { content: caption } : {}),
    });
  }

  async sendPhoto(
    chatId: string,
    filePath: string,
    caption?: string,
  ): Promise<void> {
    // Discord treats images as regular attachments; re-use sendDocument.
    await this.sendDocument(chatId, filePath, caption);
  }

  private async fetchSendableChannel(chatId: string): Promise<SendableChannel> {
    const raw = await this.client.channels.fetch(chatId);
    if (!raw || !isSendable(raw)) {
      throw new Error(`discord channel not sendable: ${chatId}`);
    }
    return raw;
  }
}

/**
 * Lift Discord's "reply" reference into the shared `InboundReplyTo`
 * shape. Exported so the filter logic can be unit-tested without a
 * real gateway connection.
 *
 * Discord exposes `message.reference.messageId` + caches the quoted
 * message on `message.channel.messages.cache` once it has been
 * observed; when the cache hit succeeds we stamp a role of
 * `assistant` if the quoted author's id matches the bot's own client
 * id, otherwise `user`. When the cache misses (message sent before
 * the bot booted) we still return a reference with an empty preview —
 * skipped by the caller so the LLM never sees `[Reply to user: ""]`.
 */
export function extractDiscordReplyTo(
  message: Message,
  botUserId: string | null,
): InboundReplyTo | undefined {
  // Narrow structural access — avoids importing discord.js MessageReference types
  // into a helper that tests want to stub.
  const ref = (message as unknown as { reference?: { messageId?: string } })
    .reference;
  const referencedId = ref?.messageId;
  if (typeof referencedId !== "string" || referencedId.length === 0) {
    return undefined;
  }
  const cache = (
    message as unknown as {
      channel: { messages?: { cache?: { get(id: string): unknown } } };
    }
  ).channel.messages?.cache;
  const cached = cache?.get(referencedId);
  let preview = "";
  let quotedAuthorId: string | undefined;
  if (cached && typeof cached === "object") {
    const obj = cached as {
      content?: unknown;
      author?: { id?: unknown };
    };
    if (typeof obj.content === "string") preview = obj.content;
    if (typeof obj.author?.id === "string") quotedAuthorId = obj.author.id;
  }
  if (preview.length === 0) return undefined;
  const role: "user" | "assistant" =
    botUserId !== null && quotedAuthorId === botUserId ? "assistant" : "user";
  return { messageId: referencedId, preview, role };
}

/**
 * Inbound filter — exported for unit testing without spinning up a
 * real gateway connection. Returns true iff we should forward the
 * message to the Agent.
 */
export function shouldDispatch(
  message: Message,
  botUserId: string | null,
): boolean {
  if (message.author.bot) return false;
  if (typeof message.content !== "string" || message.content.length === 0) {
    return false;
  }
  if (message.channel.isDMBased()) return true;
  if (botUserId && message.mentions.users.has(botUserId)) return true;
  return false;
}

function buildDefaultClient(): Client {
  return new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMessages,
      GatewayIntentBits.MessageContent,
      GatewayIntentBits.DirectMessages,
    ],
    partials: [Partials.Channel, Partials.Message],
  });
}

/** Minimal "anything with a `send(payload)` method" shape. Discord's
 * union of text-based channel types (TextChannel / DMChannel / etc)
 * all satisfy this structurally. */
interface SendableChannel {
  send(payload: unknown): Promise<unknown>;
}

function isSendable(ch: unknown): ch is SendableChannel {
  if (!ch || typeof ch !== "object") return false;
  const maybe = ch as { send?: unknown };
  return typeof maybe.send === "function";
}
