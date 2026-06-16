/**
 * Shared constants for the streaming-aware message queue and the
 * ESC-to-cancel / "(interrupted)" annotation. Paired with
 * `apps/mobile/src/lib/chat-queue.ts` — keep values in sync.
 */

/** Max number of messages a user can queue while streaming. */
export const MAX_QUEUED_MESSAGES = 5;

/**
 * Appended to the last partial assistant message when a stream is
 * cancelled via ESC / stop / cancel button. Rendered as markdown italic.
 */
export const INTERRUPTED_SUFFIX = "\n\n_(interrupted)_";
