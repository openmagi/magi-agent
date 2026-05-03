/**
 * ChannelDispatcher — glue between ChannelAdapter inbound messages and
 * Session.runTurn. Owns the per-inbound "build sessionKey, accumulate
 * assistant text via a capture-SseWriter, send reply at turn_end"
 * orchestration.
 *
 * Kept out of Agent.ts so that the Agent's start()/stop() stays a
 * thin wiring layer and this module can be unit-tested in isolation.
 */

import type { Agent } from "../Agent.js";
import { SseWriter, type AgentEvent } from "../transport/SseWriter.js";
import type { ServerResponse } from "node:http";
import type {
  ChannelAdapter,
  InboundMessage,
  OutboundMessage,
} from "./ChannelAdapter.js";
import { startTypingTicker } from "./TypingTicker.js";
import { applyResetToSessionKey } from "../slash/resetCounters.js";
import { normalizeUserVisibleRouteMetaTags } from "../turn/visibleText.js";

/**
 * SseWriter that drops writes but captures text deltas so the
 * dispatcher can assemble the assistant's final reply. Subclassing
 * rather than re-implementing keeps us on the SseWriter ABI — if
 * Turn.ts grows new event types later, we inherit them for free.
 */
export class CaptureSseWriter extends SseWriter {
  private accumulated = "";
  private status: "pending" | "committed" | "aborted" = "pending";

  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }

  override start(): void {
    /* no-op */
  }

  override legacyDelta(_content: string): void {
    /* no-op — OpenAI-compat channel not needed for native adapters */
  }

  override legacyFinish(): void {
    /* no-op */
  }

  override end(): void {
    /* no-op */
  }

  override agent(event: AgentEvent): void {
    switch (event.type) {
      case "text_delta":
        this.accumulated += event.delta;
        break;
      case "turn_end":
        this.status = event.status;
        break;
      default:
        break;
    }
  }

  finalText(): string {
    return normalizeUserVisibleRouteMetaTags(this.accumulated);
  }

  turnStatus(): "pending" | "committed" | "aborted" {
    return this.status;
  }
}

/**
 * Dispatch a single InboundMessage through the Agent's session
 * registry and send the assistant's final reply back via the
 * originating adapter.
 *
 * Invariant A (source = delivery): the Session is created with
 * `channel: {type: inbound.channel, channelId: inbound.chatId}`, so
 * any downstream NotifyUser / cron fire inherits the right target
 * without the LLM picking a route.
 */
export async function dispatchInbound(
  agent: Agent,
  adapter: ChannelAdapter,
  inbound: InboundMessage,
): Promise<void> {
  const channelRef = {
    type: inbound.channel,
    channelId: inbound.chatId,
  } as const;
  // `/reset` bumps a per-channel counter which we append as a bucket
  // suffix to the sessionKey. Counter == 0 → unchanged base key (so
  // pre-reset transcripts keep flowing); counter > 0 → `<base>:<N>`
  // which produces a fresh Session entry in the registry. We read the
  // store on every inbound because the counter may have been bumped
  // by a `/reset` in the previous turn.
  const counter = await agent.resetCounters.get(channelRef);
  const sessionKey = applyResetToSessionKey(buildSessionKey(inbound), counter);
  const session = await agent.getOrCreateSession(sessionKey, channelRef);
  const capture = new CaptureSseWriter();
  // Kick the "bot is typing..." indicator immediately and refresh it
  // on a 4s cadence until the turn finishes (committed / aborted /
  // thrown). The ticker is fire-and-forget — sendTyping() on both
  // TelegramPoller and DiscordClient is contractually non-throwing,
  // and we guard the runTurn invocation with try/finally so an error
  // mid-stream still tears the indicator down.
  const stopTyping = startTypingTicker({
    adapter,
    chatId: inbound.chatId,
  });
  try {
    await session.runTurn(
      {
        text: inbound.text,
        ...(inbound.attachments && inbound.attachments.length > 0
          ? { attachments: inbound.attachments }
          : {}),
        receivedAt: Date.now(),
        metadata: {
          source: inbound.channel,
          chatId: inbound.chatId,
          userId: inbound.userId,
          upstreamMessageId: inbound.messageId,
          // Forward the upstream reply-to context (when the user tapped
          // Reply on a prior message). MessageBuilder turns this into
          // the `[Reply to {role}: "{preview}"]` preamble on the LLM
          // user message.
          ...(inbound.replyTo ? { replyTo: inbound.replyTo } : {}),
        },
      },
      capture,
    );
  } finally {
    stopTyping();
  }
  const finalText = capture.finalText();
  if (finalText.length === 0) return; // nothing to say
  const out: OutboundMessage = {
    chatId: inbound.chatId,
    text: finalText,
    replyToMessageId: inbound.messageId,
  };
  try {
    await adapter.send(out);
  } catch (err) {
    console.warn(
      `[channel-dispatcher] ${adapter.kind} send failed chat=${inbound.chatId}: ${(err as Error).message}`,
    );
  }
}

/**
 * SessionKey format — aligned with legacy convention:
 *   agent:<persona>:<channelType>:<chatId>
 * Persona defaults to "main"; multi-persona bots can override at the
 * Agent level (future). Bucket suffix (legacy `:<bucket>`) not
 * used — core-agent scopes by chatId alone.
 */
export function buildSessionKey(inbound: InboundMessage): string {
  return `agent:main:${inbound.channel}:${inbound.chatId}`;
}
