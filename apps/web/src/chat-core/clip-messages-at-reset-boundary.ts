// Reset boundary clipping for the outbound message history.
//
// Reset rotates the session key but does NOT delete prior messages from the
// in-memory store — a divider is appended and history is preserved so the user
// can scroll up. The chat-view-client `sendMessage(allMessages)` call would
// otherwise replay that pre-reset history (incl. user messages + assistant
// replies from old turns) to the backend on every send after Reset, so the bot
// keeps acting on the old task even after a fresh session is started — exactly
// the dashboard symptom where a chat ends, the user resets, types a casual
// greeting, and the bot resumes the OLD conversation's tool plan.
//
// This helper takes the full client-side history and the per-channel reset
// boundary timestamp (from `getResetBoundaryTimestamp`) and returns only the
// messages on or after that boundary. Without a boundary it returns the input
// unchanged (no reset has happened yet).

import type { ChatMessage } from "./types";

export function clipMessagesAtResetBoundary(
  messages: readonly ChatMessage[] | null | undefined,
  boundaryAtMs: number | null | undefined,
): ChatMessage[] {
  const all = messages ?? [];
  if (!boundaryAtMs || !Number.isFinite(boundaryAtMs)) return [...all];
  return all.filter((m) => {
    const ts = typeof m.timestamp === "number" ? m.timestamp : 0;
    return ts >= boundaryAtMs;
  });
}
