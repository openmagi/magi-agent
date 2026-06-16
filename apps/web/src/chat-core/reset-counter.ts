// Reset-counter helper for the new streaming chat surface (StreamChatContainer).
//
// Mirrors the legacy `chat-store.ts` reset-counter behavior WITHOUT importing the
// E2EE/Zustand store: a Reset starts a NEW runtime conversation by bumping a
// per-channel counter that is folded into the session key. The runtime keys its
// in-memory conversation context off the session key, so a higher counter ==
// fresh context. History is NEVER deleted (the divider message marks the break).
//
// localStorage shape is shared with the legacy store (`clawy:resetCounters:<botId>`)
// so both surfaces stay in sync for the same bot/channel:
//   { "<channel>": { "count": <count>, "updatedAt": <epoch-ms> }, ... }
// Legacy numeric values are still accepted.
//
// Pure-ish: the POST uses an injectable fetchImpl for unit-testing.

import type { ChatMessage } from "./types";

const RESET_COUNTERS_KEY = (botId: string): string =>
  `clawy:resetCounters:${botId}`;

interface ResetCounterEntry {
  count: number;
  updatedAt?: number;
}

type ResetCounterStorageValue = number | ResetCounterEntry;

function normalizedResetCounterEntry(value: unknown): ResetCounterEntry {
  if (typeof value === "number" && Number.isFinite(value)) {
    return { count: Math.max(0, Math.floor(value)) };
  }
  if (value && typeof value === "object") {
    const record = value as Partial<ResetCounterEntry>;
    const count = typeof record.count === "number" && Number.isFinite(record.count)
      ? Math.max(0, Math.floor(record.count))
      : 0;
    const updatedAt = typeof record.updatedAt === "number" && Number.isFinite(record.updatedAt) && record.updatedAt > 0
      ? Math.floor(record.updatedAt)
      : undefined;
    return updatedAt === undefined ? { count } : { count, updatedAt };
  }
  return { count: 0 };
}

function readLocalResetCounters(botId: string): Record<string, ResetCounterEntry> {
  try {
    const raw = localStorage.getItem(RESET_COUNTERS_KEY(botId));
    if (raw) {
      const counters = JSON.parse(raw) as Record<string, ResetCounterStorageValue>;
      return Object.fromEntries(
        Object.entries(counters).map(([name, value]) => [
          name,
          normalizedResetCounterEntry(value),
        ]),
      );
    }
  } catch {
    /* ignore — treat as empty */
  }
  return {};
}

function writeLocalResetCounters(
  botId: string,
  counters: Record<string, ResetCounterEntry>,
): void {
  try {
    localStorage.setItem(RESET_COUNTERS_KEY(botId), JSON.stringify(counters));
  } catch {
    /* ignore — best-effort persistence */
  }
}

/**
 * Read the current reset counter for a channel (0 when unset). Mirrors the
 * exported `getResetCounter` in chat-store.ts but local to this surface so the
 * streaming stack does not import the legacy store module.
 */
export function getResetCounter(botId: string, channel: string): number {
  return readLocalResetCounters(botId)[channel]?.count ?? 0;
}

export function getResetBoundaryTimestamp(botId: string, channel: string): number | null {
  return readLocalResetCounters(botId)[channel]?.updatedAt ?? null;
}

function setLocalResetCounter(
  botId: string,
  channel: string,
  value: number,
  updatedAt?: number | null,
): void {
  const counters = readLocalResetCounters(botId);
  const existing = counters[channel];
  const normalizedUpdatedAt =
    typeof updatedAt === "number" && Number.isFinite(updatedAt) && updatedAt > 0
      ? Math.floor(updatedAt)
      : existing?.updatedAt;
  counters[channel] = normalizedUpdatedAt === undefined
    ? { count: Math.max(0, Math.floor(value)) }
    : { count: Math.max(0, Math.floor(value)), updatedAt: normalizedUpdatedAt };
  writeLocalResetCounters(botId, counters);
}

function mergeResetCounterFromServer(
  local: Record<string, ResetCounterEntry>,
  channel: string,
  serverCount: number,
  serverUpdatedAt?: number,
): boolean {
  if (!Number.isFinite(serverCount)) return false;
  const localEntry = local[channel] ?? { count: 0 };
  const normalizedServerCount = Math.max(0, Math.floor(serverCount));
  const normalizedServerUpdatedAt =
    typeof serverUpdatedAt === "number" && Number.isFinite(serverUpdatedAt) && serverUpdatedAt > 0
      ? Math.floor(serverUpdatedAt)
      : undefined;
  const shouldAdopt =
    normalizedServerCount > localEntry.count ||
    (
      normalizedServerCount === localEntry.count &&
      normalizedServerUpdatedAt !== undefined &&
      normalizedServerUpdatedAt > (localEntry.updatedAt ?? 0)
    );
  if (!shouldAdopt) return false;
  local[channel] = normalizedServerUpdatedAt === undefined
    ? { count: normalizedServerCount, updatedAt: localEntry.updatedAt }
    : { count: normalizedServerCount, updatedAt: normalizedServerUpdatedAt };
  return true;
}

/**
 * Build the runtime session key WITH the reset counter folded in. Matches the
 * legacy `buildSessionKey` (chat-client.ts) shape exactly:
 *   rc === 0 → `agent:main:app:<channel>`
 *   rc  >  0 → `agent:main:app:<channel>:<rc>`
 * so both surfaces address the same runtime session before/after a reset.
 */
export function buildResetSessionKey(channel: string, resetCounter: number): string {
  return resetCounter > 0
    ? `agent:main:app:${channel}:${resetCounter}`
    : `agent:main:app:${channel}`;
}

/**
 * Build the system divider message inserted into the transcript on Reset. The
 * prior history is kept; this row simply marks where the new conversation began.
 * Pure — returns a fresh `ChatMessage` (id uses Date.now() so consecutive resets
 * don't collide).
 */
export function buildResetDivider(now: number = Date.now()): ChatMessage {
  return {
    id: `system-reset-${now}`,
    role: "system",
    content: "Session ended — new conversation started",
    timestamp: now,
  };
}

export interface IncrementResetCounterOptions {
  botId: string;
  channel: string;
  /** Privy bearer token (or null while hydrating). */
  token?: string | null;
  /**
   * Async token provider. When supplied, localStorage is still bumped before the
   * provider is awaited so the next render/request can use the fresh session key.
   */
  getToken?: () => Promise<string | null>;
  /** Injectable fetch implementation; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface SyncResetCountersOptions {
  botId: string;
  /** Privy bearer token provider. */
  getToken: () => Promise<string | null>;
  /** Injectable fetch implementation; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

/**
 * Sync reset counters from the server and merge with localStorage by taking the
 * maximum value per channel. Mirrors the legacy store behavior so a fresh
 * browser/device does not send with a stale lower session-key suffix.
 */
export async function syncResetCounters({
  botId,
  getToken,
  fetchImpl = fetch,
}: SyncResetCountersOptions): Promise<void> {
  try {
    const token = await getToken();
    if (!token) return;
    const res = await fetchImpl(`/api/chat/reset-counters?botId=${encodeURIComponent(botId)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const { counters: serverCounters, resetAt } = (await res.json()) as {
      counters?: Record<string, number>;
      resetAt?: Record<string, number>;
    };

    const local = readLocalResetCounters(botId);
    let changed = false;
    for (const [channelName, serverValue] of Object.entries(serverCounters ?? {})) {
      changed =
        mergeResetCounterFromServer(local, channelName, serverValue, resetAt?.[channelName]) ||
        changed;
    }
    if (changed) {
      writeLocalResetCounters(botId, local);
    }
  } catch {
    /* ignore — local counters remain authoritative */
  }
}

/**
 * Optimistically bump the local reset counter, then best-effort POST it to the
 * server so the counter syncs cross-device. Mirrors the legacy
 * `incrementResetCounter` in chat-store.ts. Returns the NEW local counter value
 * (so callers can recompute the session key immediately). Never throws — the
 * network POST is fire-and-forget and the local bump already took effect.
 */
export async function incrementResetCounter({
  botId,
  channel,
  token,
  getToken,
  fetchImpl = fetch,
}: IncrementResetCounterOptions): Promise<number> {
  const current = getResetCounter(botId, channel);
  const next = current + 1;
  const optimisticResetAt = Date.now();
  setLocalResetCounter(botId, channel, next, optimisticResetAt);

  try {
    const authToken = token !== undefined
      ? token
      : getToken
        ? await getToken()
        : null;
    if (!authToken) return next;
    const res = await fetchImpl("/api/chat/reset-counters", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify({ botId, channelName: channel }),
    });
    if (res.ok) {
      const { resetCount, resetAt } = (await res.json()) as {
        resetCount: number;
        resetAt?: number;
      };
      // Server is authoritative — adopt it when higher than our optimistic bump.
      const local = readLocalResetCounters(botId);
      if (mergeResetCounterFromServer(local, channel, resetCount, resetAt)) {
        writeLocalResetCounters(botId, local);
        return resetCount;
      }
    }
  } catch {
    /* ignore — local counter remains authoritative */
  }
  return next;
}
