// NOTE: This module is consumed by the client chat surface. It transitively
// imports e2ee.ts which is "use client". Do NOT import this module from a
// Server Component.
import { isPlaintext, unwrapPlaintext } from "./plaintext-sentinel";
import { decryptMessage } from "./e2ee";
import { decodeHistoryPlaintext } from "./history-envelope";
import type { ChatMessage } from "./types";

/**
 * chat-proxy/core-agent-resume wraps Python-ADK user turns as a hidden
 * channel-history row whose content is a `<!-- openmagi:server-readable-
 * user-turn:v1:... -->` HTML comment (base64url-encoded user content). It
 * lets the server re-read the user's original input on resume without
 * exposing it to the chat UI. Loaders MUST filter these rows out so they
 * never render as visible bubbles.
 *
 * Same regex as chat-store (receivePushMessage) — see PR #1457 for context.
 */
const SERVER_READABLE_USER_TURN_MARKER_RE =
  /^\s*<!-- openmagi:server-readable-user-turn:v1:[A-Za-z0-9_-]+ -->\s*$/;

export interface E2EEApiMessage {
  id: string;
  channel_name: string;
  role: "user" | "assistant";
  encrypted_content: string;
  iv: string;
  created_at: string;
  client_msg_id: string | null;
}

export interface LoadChannelHistoryOptions {
  botId: string;
  channelName: string;
  keys: CryptoKey[];
  token: string;
  limit?: number;
  since?: string;
  before?: string;
  latest?: boolean;
  fetchImpl?: typeof fetch;
}

export interface LoadChannelHistoryResult {
  messages: ChatMessage[];
  deletions: { client_msg_id: string | null; deleted_at: string }[];
  hasMore: boolean;
  nextBefore: string | null;
  decryptFailures: number;
}

/**
 * Convert a single API row to a ChatMessage.
 *
 * Branches:
 * 1. Plaintext sentinel ("plaintext:v1:…") — no key needed; strip prefix, decode envelope.
 * 2. Legacy encrypted — try each key in order; first success wins.
 * 3. All keys fail (or no keys provided for encrypted row) — return null (caller counts).
 */
export async function rowToMessage(
  row: E2EEApiMessage,
  keys: CryptoKey[],
): Promise<ChatMessage | null> {
  let raw: string;

  if (isPlaintext(row.encrypted_content)) {
    raw = unwrapPlaintext(row.encrypted_content);
  } else {
    // Legacy encrypted path — try each key.
    let decrypted: string | null = null;
    for (const key of keys) {
      try {
        decrypted = await decryptMessage(key, row.encrypted_content, row.iv);
        break;
      } catch {
        // key didn't work — try the next one
      }
    }
    if (decrypted === null) return null;
    raw = decrypted;
  }

  const decoded = decodeHistoryPlaintext(row.role, raw);
  return {
    id: row.client_msg_id ?? row.id,
    role: row.role,
    content: decoded.content,
    timestamp: new Date(row.created_at).getTime(),
    serverId: row.id,
    ...(decoded.thinkingContent !== undefined ? { thinkingContent: decoded.thinkingContent } : {}),
    ...(decoded.thinkingDuration !== undefined ? { thinkingDuration: decoded.thinkingDuration } : {}),
    ...(decoded.researchEvidence !== undefined ? { researchEvidence: decoded.researchEvidence } : {}),
    ...(decoded.usage !== undefined ? { usage: decoded.usage } : {}),
    ...(decoded.activities !== undefined ? { activities: decoded.activities } : {}),
  };
}

/**
 * Fetch and decode the full message history for a channel.
 *
 * Pure function — no React, no Privy. Keys are passed in by the caller (PR3 hook layer).
 * Plaintext-sentinel rows decode without any key; legacy-encrypted rows use the key list.
 * Rows that fail all decryption attempts are dropped and counted in `decryptFailures`.
 * Returned messages are sorted by timestamp ascending.
 */
export async function loadChannelHistory(
  opts: LoadChannelHistoryOptions,
): Promise<LoadChannelHistoryResult> {
  const {
    botId,
    channelName,
    keys,
    token,
    limit,
    since,
    before,
    latest,
    fetchImpl = fetch,
  } = opts;

  const params = new URLSearchParams({ botId, channelName });
  if (limit !== undefined) params.set("limit", String(limit));
  if (since !== undefined) params.set("since", since);
  if (before !== undefined) params.set("before", before);
  if (latest !== undefined) params.set("latest", String(latest));

  const EMPTY: LoadChannelHistoryResult = { messages: [], deletions: [], hasMore: false, nextBefore: null, decryptFailures: 0 };

  let res: Response;
  let data: {
    messages?: unknown;
    deletions?: { client_msg_id: string | null; deleted_at: string }[];
    hasMore?: boolean;
    nextBefore?: string | null;
  };

  try {
    res = await fetchImpl(`/api/chat/messages?${params.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (!res.ok) {
      return EMPTY;
    }

    data = (await res.json()) as typeof data;
  } catch {
    return EMPTY;
  }

  const rows: E2EEApiMessage[] = Array.isArray(data.messages) ? (data.messages as E2EEApiMessage[]) : [];
  const deletions = Array.isArray(data.deletions) ? data.deletions : [];
  const hasMore = data.hasMore ?? false;
  const nextBefore = data.nextBefore ?? null;

  let decryptFailures = 0;
  const decoded = await Promise.all(rows.map((row) => rowToMessage(row, keys)));

  const messages = decoded
    .filter((m): m is ChatMessage => {
      if (m === null) {
        decryptFailures++;
        return false;
      }
      // Drop server-readable user-turn marker rows so they never render.
      if (SERVER_READABLE_USER_TURN_MARKER_RE.test(m.content)) return false;
      return true;
    })
    .sort((a, b) => a.timestamp - b.timestamp);

  return { messages, deletions, hasMore, nextBefore, decryptFailures };
}
