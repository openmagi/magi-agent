/**
 * Per-channel reset counter store. Ported from legacy gateway's `/reset`
 * mechanism: incrementing the counter changes the sessionKey namespace
 * on the NEXT inbound message, effectively starting a fresh session
 * (same chatId, new conversation).
 *
 * File layout:
 *   workspace/core-agent/sessions/reset-counters.json
 *
 * Shape: `{ [channelRefKey: string]: number }` where `channelRefKey`
 * is `"<type>:<channelId>"` (e.g. `"telegram:777"`, `"app:general"`).
 * Atomic tmp-rename on write so a crash mid-update never leaves a
 * truncated file.
 *
 * Counter applies to sessionKey as an appended bucket suffix:
 *   base:       `agent:main:telegram:777`
 *   post-reset: `agent:main:telegram:777:1`
 *                                      ^ counter value
 *
 * Zero is the implicit default for channels that never had `/reset`
 * run — those sessions use the bare base sessionKey for backward
 * compatibility with existing on-disk transcripts.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type { ChannelRef } from "../util/types.js";

export const RESET_COUNTERS_FILE = "reset-counters.json";

export function channelRefKey(ref: ChannelRef): string {
  return `${ref.type}:${ref.channelId}`;
}

export class ResetCounterStore {
  private loaded = false;
  private counters: Record<string, number> = {};

  constructor(private readonly sessionsDir: string) {}

  private filePath(): string {
    return path.join(this.sessionsDir, RESET_COUNTERS_FILE);
  }

  /**
   * Lazily load the on-disk JSON. Missing file is treated as empty
   * (fresh bot). Malformed JSON is logged + treated as empty so a
   * corrupted file never wedges the agent.
   */
  async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const txt = await fs.readFile(this.filePath(), "utf8");
      const parsed = JSON.parse(txt) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const out: Record<string, number> = {};
        for (const [k, v] of Object.entries(parsed)) {
          if (typeof v === "number" && Number.isFinite(v) && v >= 0) {
            out[k] = Math.floor(v);
          }
        }
        this.counters = out;
      }
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code === "ENOENT") return;
      // Logged, not thrown — slash commands must never wedge on a
      // corrupt sidecar file.
      console.warn(
        `[core-agent] reset-counters load failed: ${(err as Error).message}`,
      );
    }
  }

  /** Current counter for a channel. Zero when never reset. */
  async get(ref: ChannelRef): Promise<number> {
    await this.ensureLoaded();
    return this.counters[channelRefKey(ref)] ?? 0;
  }

  /**
   * Atomically increment the counter for a channel and return the
   * new value. The increment is persisted before returning so a
   * subsequent inbound message that hits {@link applyResetToSessionKey}
   * sees the new bucket.
   */
  async bump(ref: ChannelRef): Promise<number> {
    await this.ensureLoaded();
    const key = channelRefKey(ref);
    const next = (this.counters[key] ?? 0) + 1;
    this.counters[key] = next;
    await atomicWriteJson(this.filePath(), this.counters);
    return next;
  }
}

/**
 * Append the current reset-counter as a bucket suffix on a base
 * sessionKey. Counter == 0 → bare base (back-compat with pre-reset
 * bots). Counter > 0 → `<base>:<counter>`.
 *
 * Exported so ChannelDispatcher can compute the effective sessionKey
 * on every inbound before handing off to Session.
 */
export function applyResetToSessionKey(base: string, counter: number): string {
  if (counter <= 0) return base;
  return `${base}:${counter}`;
}
