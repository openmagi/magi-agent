/**
 * CronScheduler — T-cron (post-Phase-3 compat with legacy gateway).
 *
 * In-process scheduler that fires stored crons as synthetic turns.
 * A single setInterval tick-loop (30s cadence) scans for any cron
 * whose `nextFireAt` has passed and dispatches it.
 *
 * Channel routing — the critical fix vs legacy gateway:
 *   legacy gateway routed cron output via session-recency heuristic
 *   (active session = most-recently-updated). The LLM also chose
 *   a `--target` flag, routinely wrong (web-authored cron → Telegram
 *   delivery). In Clawy, the cron record persists the **delivery
 *   channel captured at creation time** (`deliveryChannel: ChannelRef`)
 *   so the runtime enforces the right target regardless of what the
 *   bot's prompt says. See CronRecord.deliveryChannel below.
 *
 * Storage: `workspace/core-agent/crons/index.json` atomic tmp-rename.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { monotonicFactory } from "ulid";
import { getNextFireAt } from "./cronParser.js";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type { ChannelRef } from "../util/types.js";

const ulid = monotonicFactory();

export interface CronRecord {
  cronId: string;
  botId: string;
  userId: string;
  expression: string;
  prompt: string;
  /** Channel the cron was created from — fire-and-deliver target. */
  deliveryChannel: ChannelRef;
  /** Optional human-facing label. */
  description?: string;
  enabled: boolean;
  createdAt: number;
  /** When it should fire next (ms since epoch). */
  nextFireAt: number;
  /** Most recent fire (ms). Undefined if never fired. */
  lastFiredAt?: number;
  /** Consecutive failure count — shuts the cron down at 5. */
  consecutiveFailures: number;
  /**
   * Durable crons survive session end and are persisted to
   * `workspace/core-agent/crons/index.json`. Non-durable crons are
   * session-scoped — their IDs are tracked on `Session.meta.crons` and
   * the scheduler drops them when the session closes. Legacy records
   * (pre-durable) hydrated from disk are treated as `durable: true`
   * since that's the only path that ever wrote to index.json.
   */
  durable: boolean;
  /**
   * SessionKey of the creating session. Only set for non-durable
   * (session-scoped) crons so the Session.close() path can ensure it
   * only deletes crons that actually belong to that session (defence
   * in depth — the Session.meta.crons array is authoritative but the
   * back-reference guards against index/array drift).
   */
  sessionKey?: string;
  /**
   * Internal system crons (e.g. hipocampus maintenance). Not visible
   * to the bot via CronList tool, not deletable by the bot, always
   * durable. Fired via dedicated handler instead of fireHandler.
   */
  internal?: boolean;
}

export type InternalCronHandler = () => Promise<void>;

export type CronFireHandler = (cron: CronRecord) => Promise<void>;

export interface CronSchedulerOptions {
  /** Tick cadence; defaults to 30 000 ms. Lowered in tests. */
  tickMs?: number;
  /** Override `Date.now()` — tests pass a mock clock. */
  now?: () => number;
  /** Max consecutive failures before auto-disable. */
  maxConsecutiveFailures?: number;
}

const MAX_CONSECUTIVE_FAILURES_DEFAULT = 5;

export class CronScheduler {
  private readonly crons = new Map<string, CronRecord>();
  private readonly internalHandlers = new Map<string, InternalCronHandler>();
  private readonly indexPath: string;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private readonly tickMs: number;
  readonly nowFn: () => number;
  private readonly maxConsecutiveFailures: number;
  private fireHandler: CronFireHandler | null = null;
  private tickInProgress = false;

  constructor(
    private readonly workspaceRoot: string,
    options: CronSchedulerOptions = {},
  ) {
    this.indexPath = path.join(workspaceRoot, "core-agent", "crons", "index.json");
    this.tickMs = options.tickMs ?? 30_000;
    this.nowFn = options.now ?? (() => Date.now());
    this.maxConsecutiveFailures =
      options.maxConsecutiveFailures ?? MAX_CONSECUTIVE_FAILURES_DEFAULT;
  }

  /**
   * Register the fire handler — called by Agent.start() before the
   * scheduler begins ticking. The handler receives a CronRecord and
   * is expected to synthesise a Turn carrying {prompt, channel:
   * deliveryChannel, metadata: {source: "cron", cronId}}.
   */
  setFireHandler(handler: CronFireHandler): void {
    this.fireHandler = handler;
  }

  async hydrate(): Promise<void> {
    this.crons.clear();
    try {
      const raw = await fs.readFile(this.indexPath, "utf8");
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      for (const entry of parsed) {
        if (isCronRecord(entry)) {
          // Backward compat: index.json only ever persisted durable
          // (before this change, every cron was unconditionally
          // written there). A missing `durable` field is therefore
          // `true` on load — anything else would silently re-classify
          // legacy crons as session-scoped and drop them on restart.
          const withDurable: CronRecord = {
            ...entry,
            durable: entry.durable === false ? false : true,
          };
          this.crons.set(withDurable.cronId, withDurable);
        }
      }
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
    }
  }

  start(): void {
    if (this.ticker) return;
    this.ticker = setInterval(() => {
      void this.tick();
    }, this.tickMs);
  }

  stop(): void {
    if (this.ticker) {
      clearInterval(this.ticker);
      this.ticker = null;
    }
  }

  private async persist(): Promise<void> {
    // Only durable non-internal crons are written to the on-disk index.
    // Session-scoped (non-durable) crons live in memory only. Internal
    // crons (e.g. hipocampus maintenance) are re-registered on every
    // Agent.start() and never persisted.
    const snapshot = [...this.crons.values()].filter((c) => c.durable && !c.internal);
    await atomicWriteJson(this.indexPath, snapshot);
  }

  /**
   * Register an internal system cron. Internal crons:
   * - Are always durable (survive pod restart)
   * - Cannot be deleted by the bot (excluded from CronDelete)
   * - Are excluded from CronList tool output
   * - Fire via dedicated handler instead of fireHandler
   * - Idempotent: silently returns if already registered
   */
  registerInternal(opts: {
    name: string;
    schedule: string;
    handler: InternalCronHandler;
  }): void {
    const cronId = `internal:${opts.name}`;
    if (this.crons.has(cronId)) return;
    const record: CronRecord = {
      cronId,
      botId: "",
      userId: "",
      expression: opts.schedule,
      prompt: "",
      deliveryChannel: { type: "internal" as ChannelRef["type"], channelId: "" },
      enabled: true,
      createdAt: this.nowFn(),
      nextFireAt: getNextFireAt(opts.schedule, new Date(this.nowFn())).getTime(),
      consecutiveFailures: 0,
      durable: true,
      internal: true,
    };
    this.crons.set(cronId, record);
    this.internalHandlers.set(cronId, opts.handler);
  }

  /**
   * Dispatch any crons whose nextFireAt has elapsed. Separate method
   * so tests can advance a mock clock + call tick() directly.
   */
  async tick(): Promise<void> {
    if (this.tickInProgress) return;
    this.tickInProgress = true;
    try {
      const currentTime = this.nowFn();
      const due: CronRecord[] = [];
      for (const c of this.crons.values()) {
        if (!c.enabled) continue;
        if (c.nextFireAt <= currentTime) due.push(c);
      }
      for (const cron of due) {
        try {
          if (cron.internal) {
            // Internal crons use their dedicated handler
            const handler = this.internalHandlers.get(cron.cronId);
            if (handler) await handler();
          } else if (this.fireHandler) {
            await this.fireHandler(cron);
          }
          cron.lastFiredAt = this.nowFn();
          cron.consecutiveFailures = 0;
        } catch (err) {
          cron.consecutiveFailures += 1;
          if (cron.consecutiveFailures >= this.maxConsecutiveFailures) {
            cron.enabled = false;
          }
          console.warn(
            `[cron] fire failed cronId=${cron.cronId} failures=${cron.consecutiveFailures}: ${(err as Error).message}`,
          );
        } finally {
          try {
            cron.nextFireAt = getNextFireAt(cron.expression, new Date(this.nowFn())).getTime();
          } catch {
            cron.enabled = false;
          }
        }
      }
      if (due.length > 0) await this.persist();
    } finally {
      this.tickInProgress = false;
    }
  }

  async create(opts: {
    botId: string;
    userId: string;
    expression: string;
    prompt: string;
    deliveryChannel: ChannelRef;
    description?: string;
    /** Defaults to `false` (session-scoped). See CronRecord.durable. */
    durable?: boolean;
    /** Required when durable=false so Session.close() can sweep. */
    sessionKey?: string;
  }): Promise<CronRecord> {
    // Throws if expression invalid — callers surface as tool error.
    const nextFireAt = getNextFireAt(opts.expression, new Date(this.nowFn())).getTime();
    const durable = opts.durable === true;
    const record: CronRecord = {
      cronId: ulid(),
      botId: opts.botId,
      userId: opts.userId,
      expression: opts.expression,
      prompt: opts.prompt,
      deliveryChannel: { ...opts.deliveryChannel },
      ...(opts.description ? { description: opts.description } : {}),
      enabled: true,
      createdAt: this.nowFn(),
      nextFireAt,
      consecutiveFailures: 0,
      durable,
      ...(!durable && opts.sessionKey ? { sessionKey: opts.sessionKey } : {}),
    };
    this.crons.set(record.cronId, record);
    // Only persist when durable — session-scoped crons never touch
    // the on-disk index. Still a no-op persist call is safe, but we
    // skip the filesystem round-trip entirely for throughput.
    if (durable) await this.persist();
    return record;
  }

  list(filter?: { enabled?: boolean; includeInternal?: boolean }): CronRecord[] {
    let all = [...this.crons.values()];
    // Internal crons are hidden from bot-facing tools by default
    if (!filter?.includeInternal) {
      all = all.filter((c) => !c.internal);
    }
    if (filter?.enabled !== undefined) {
      all = all.filter((c) => c.enabled === filter.enabled);
    }
    return all;
  }

  get(cronId: string): CronRecord | null {
    return this.crons.get(cronId) ?? null;
  }

  async update(
    cronId: string,
    patch: {
      expression?: string;
      prompt?: string;
      enabled?: boolean;
      description?: string;
    },
  ): Promise<CronRecord> {
    const c = this.crons.get(cronId);
    if (!c) throw new Error(`cron not found: ${cronId}`);
    if (patch.expression !== undefined) {
      // Recompute nextFireAt when schedule changes.
      const nextFireAt = getNextFireAt(patch.expression, new Date(this.nowFn())).getTime();
      c.expression = patch.expression;
      c.nextFireAt = nextFireAt;
    }
    if (patch.prompt !== undefined) c.prompt = patch.prompt;
    if (patch.enabled !== undefined) {
      c.enabled = patch.enabled;
      if (patch.enabled) c.consecutiveFailures = 0;
    }
    if (patch.description !== undefined) c.description = patch.description;
    await this.persist();
    return c;
  }

  async delete(cronId: string): Promise<boolean> {
    const record = this.crons.get(cronId);
    if (record?.internal) {
      throw new Error("internal crons cannot be deleted");
    }
    const existed = this.crons.delete(cronId);
    if (existed) await this.persist();
    return existed;
  }
}

function isCronRecord(x: unknown): x is CronRecord {
  if (!x || typeof x !== "object") return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r.cronId === "string" &&
    typeof r.botId === "string" &&
    typeof r.expression === "string" &&
    typeof r.prompt === "string" &&
    typeof r.enabled === "boolean" &&
    typeof r.nextFireAt === "number" &&
    !!r.deliveryChannel &&
    typeof r.deliveryChannel === "object"
  );
}
