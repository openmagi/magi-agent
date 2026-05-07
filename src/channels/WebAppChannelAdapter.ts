/**
 * WebAppChannelAdapter — outbound-only HTTP push to chat-proxy.
 *
 * Motivation: TelegramPoller + DiscordClient let the agent proactively
 * reply (e.g. when a cron fires). The web + mobile app channel had no
 * outbound path — chat-proxy is a request/response relay, so cron /
 * background output aimed at an app-channel user was dropped.
 *
 * This adapter closes the gap. On `send()` it POSTs to chat-proxy
 * `/v1/bot-push/message` with:
 *   - Authorization: Bearer <gateway token>      (per-bot auth)
 *   - X-Push-Signature: <HMAC-SHA256(hex)>       (shared HMAC defense)
 *
 * chat-proxy validates both, then inserts into `public.push_messages`.
 * Supabase Realtime fans the row out to the user's live web/mobile
 * clients (see §7.15 migration).
 *
 * Inbound is out of scope — web / mobile users continue to originate
 * messages via the existing `/v1/chat/:botId/completions` SSE path.
 * `onInboundMessage` retains the handler but is never invoked; `start`
 * and `stop` are no-ops so Agent.start() can treat this adapter
 * uniformly with Telegram / Discord.
 *
 * Design reference: 2026-04-20 push messaging gap fix.
 */

import crypto from "node:crypto";
import type {
  InboundHandler,
  OutboundMessage,
} from "./ChannelAdapter.js";

/**
 * Structurally compatible with ChannelAdapter's outbound surface, but
 * NOT declared as `implements ChannelAdapter` because the core
 * `ChannelAdapter.kind` union is intentionally kept to
 * `"telegram" | "discord"` — those are the only adapters that plug into
 * ChannelDispatcher's inbound routing. This adapter is outbound-only
 * and registered in a separate slot on Agent (see Agent.webAppAdapter).
 */

export interface WebAppChannelConfig {
  /** chat-proxy endpoint. e.g. https://chat.magi.local/v1/bot-push/message */
  pushEndpointUrl: string;
  /** Shared HMAC secret — must match chat-proxy's WEBAPP_PUSH_HMAC_KEY. */
  hmacKey: string;
  /** Per-bot gateway token (already provisioned for api-proxy auth). */
  gatewayToken: string;
  /** Bot id — echoed back to chat-proxy for idempotency + audit. */
  botId: string;
  /** Privy DID of the user who owns this bot. */
  userId: string;
  /** Injected for tests. Defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /** Injected for tests — controls serverId generation. */
  serverIdFactory?: () => string;
}

/**
 * `OutboundMessage.chatId` carries the app channel name (e.g.
 * "general") — unlike Telegram / Discord where chatId is the upstream
 * thread id. CronScheduler already stores the channelId in
 * `deliveryChannel.channelId` regardless of channel type, so the
 * existing plumbing flows through without change.
 */
export class WebAppChannelAdapter {
  readonly kind = "webapp" as const;

  private readonly cfg: WebAppChannelConfig;
  private readonly fetchImpl: typeof fetch;
  private readonly serverIdFactory: () => string;
  private handler: InboundHandler | null = null;
  private started = false;

  constructor(cfg: WebAppChannelConfig) {
    if (!cfg.pushEndpointUrl) {
      throw new Error("WebAppChannelAdapter: pushEndpointUrl required");
    }
    if (!cfg.hmacKey) {
      throw new Error("WebAppChannelAdapter: hmacKey required");
    }
    if (!cfg.gatewayToken) {
      throw new Error("WebAppChannelAdapter: gatewayToken required");
    }
    if (!cfg.botId) {
      throw new Error("WebAppChannelAdapter: botId required");
    }
    if (!cfg.userId) {
      throw new Error("WebAppChannelAdapter: userId required");
    }
    this.cfg = cfg;
    this.fetchImpl = cfg.fetchImpl ?? fetch;
    this.serverIdFactory =
      cfg.serverIdFactory ??
      (() => `web-${Date.now()}-${crypto.randomBytes(6).toString("hex")}`);
  }

  onInboundMessage(handler: InboundHandler): void {
    // Retained for interface conformance. Web/app inbound flows
    // through the chat-proxy SSE path, not through this adapter, so
    // the handler is never fired.
    this.handler = handler;
  }

  async start(): Promise<void> {
    // No socket / long-poll to open — outbound-only.
    this.started = true;
  }

  async stop(): Promise<void> {
    this.started = false;
  }

  async send(msg: OutboundMessage): Promise<void> {
    const channel = msg.chatId;
    const content = msg.text;
    const serverId = this.serverIdFactory();
    const signature = this.computeSignature({
      botId: this.cfg.botId,
      channelId: channel,
      userId: this.cfg.userId,
      serverId,
      content,
    });
    const body = JSON.stringify({
      channel,
      userId: this.cfg.userId,
      content,
      serverId,
    });
    const resp = await this.fetchImpl(this.cfg.pushEndpointUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.cfg.gatewayToken}`,
        "X-Push-Signature": signature,
      },
      body,
    });
    // 202 Accepted (new row) and the duplicate-serverId idempotent
    // path (also 202) are both fine; anything else is an error.
    if (resp.status !== 202 && resp.status !== 200) {
      let errText = "";
      try {
        errText = await resp.text();
      } catch {
        /* ignore */
      }
      throw new Error(
        `webapp push failed: HTTP ${resp.status} ${errText.slice(0, 200)}`,
      );
    }
  }

  async sendDocument(
    _chatId: string,
    _filePath: string,
    _caption?: string,
  ): Promise<void> {
    // File delivery to web/app goes through the knowledge / artifact
    // path (user downloads via signed URL from the chat UI). For
    // Phase 1 push, file delivery is explicitly out of scope — throw
    // so callers fall back to posting a link in the message body.
    throw new Error(
      "WebAppChannelAdapter.sendDocument: not supported — send a download URL in send() instead",
    );
  }

  async sendPhoto(
    _chatId: string,
    _filePath: string,
    _caption?: string,
  ): Promise<void> {
    throw new Error(
      "WebAppChannelAdapter.sendPhoto: not supported — send an image URL in send() instead",
    );
  }

  /** Exposed for tests + symmetry with chat-proxy's verifier. */
  computeSignature(input: {
    botId: string;
    channelId: string;
    userId: string;
    serverId: string;
    content: string;
  }): string {
    const canonical = `${input.botId}:${input.channelId}:${input.userId}:${input.serverId}:${input.content}`;
    return crypto
      .createHmac("sha256", this.cfg.hmacKey)
      .update(canonical)
      .digest("hex");
  }

  /** Test-only: inspect whether start() was called. */
  isStarted(): boolean {
    return this.started;
  }

  /** Test-only: confirm a handler was registered (never called). */
  hasInboundHandler(): boolean {
    return this.handler !== null;
  }
}
