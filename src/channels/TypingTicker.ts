/**
 * TypingTicker — keeps the upstream channel's "bot is typing…"
 * indicator alive for the duration of a single Turn.
 *
 * Motivation: under legacy gateway `node-host`, Telegram showed the typing
 * indicator while the bot was generating a response. Core-agent's
 * TelegramPoller previously never called `sendChatAction`, so users
 * saw no activity between sending a message and receiving the reply.
 * Same gap existed for Discord (`channel.sendTyping()` unused).
 *
 * Design:
 *   - Fire one `sendTyping(chatId)` immediately when the turn starts.
 *   - Re-fire every `intervalMs` (default 4000ms) until stopped.
 *     Telegram's indicator lingers ~5s per call and Discord's ~10s,
 *     so a 4s cadence covers both with overlap.
 *   - The returned `stop()` function clears the interval and aborts
 *     any in-flight tick. MUST be called in a `finally` block so a
 *     turn error / abort still tears the ticker down.
 *   - Fire-and-forget: adapter.sendTyping() is contracted to swallow
 *     its own errors. If it ever doesn't, we still catch here so a
 *     rogue indicator call cannot crash the runtime.
 *
 * Not a class on purpose — the ticker is stateful only inside the
 * closure, and callers only ever need (a) start and (b) the stop
 * function. A class would add ceremony without value.
 */

import type { ChannelAdapter } from "./ChannelAdapter.js";

export interface TypingTickerOptions {
  adapter: Pick<ChannelAdapter, "sendTyping">;
  chatId: string;
  /** Interval between re-fires in ms. Default 4000. */
  intervalMs?: number;
  /**
   * Injected for tests so we don't sit on a real `setInterval`.
   * Must match the `setInterval` / `clearInterval` contract of
   * Node's global timers.
   */
  setInterval?: (fn: () => void, ms: number) => NodeJS.Timeout | number;
  clearInterval?: (handle: NodeJS.Timeout | number) => void;
}

/**
 * Start a typing indicator ticker. Fires once synchronously (before
 * returning) and then every `intervalMs` until the returned `stop()`
 * is invoked.
 *
 * Returns a no-arg `stop()` function — safe to call multiple times.
 *
 * Guarantees:
 *   - Missing `chatId` → no-op. Logs a warning and returns a stop()
 *     that does nothing. Avoids accidental `chat_id: "undefined"`
 *     API calls.
 *   - `adapter.sendTyping` throwing is caught and logged — never
 *     propagates. Ticker keeps firing on subsequent intervals.
 *   - `stop()` is idempotent.
 */
export function startTypingTicker(opts: TypingTickerOptions): () => void {
  const { adapter, chatId } = opts;
  const intervalMs = opts.intervalMs ?? 4000;
  const setIntervalFn = opts.setInterval ?? globalThis.setInterval;
  const clearIntervalFn = opts.clearInterval ?? globalThis.clearInterval;

  if (!chatId || chatId.length === 0) {
    console.warn("[typing-ticker] missing chatId — ticker not started");
    return () => {
      /* no-op */
    };
  }

  let stopped = false;

  const fire = (): void => {
    if (stopped) return;
    // Fire-and-forget — we purposely don't await, because the caller
    // (ChannelDispatcher) must not block the real response stream on
    // Telegram/Discord API latency.
    Promise.resolve()
      .then(() => adapter.sendTyping(chatId))
      .catch((err) => {
        // Defensive — adapters already swallow their own errors, but
        // we catch here too so a contract violation can't crash the
        // turn.
        console.warn(
          `[typing-ticker] sendTyping rejected chat=${chatId}: ${(err as Error).message}`,
        );
      });
  };

  fire(); // immediate
  const handle = setIntervalFn(fire, intervalMs);

  return function stop(): void {
    if (stopped) return;
    stopped = true;
    clearIntervalFn(handle as NodeJS.Timeout);
  };
}
