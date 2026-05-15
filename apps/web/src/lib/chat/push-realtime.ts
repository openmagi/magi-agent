/**
 * push-realtime.ts — stub for OSS magi-agent.
 *
 * The cloud product uses Supabase Realtime for push messages.
 * OSS does not have a Supabase dependency, so this is a no-op stub.
 */

"use client";

export interface PushMessageRow {
  id: string;
  bot_id: string;
  channel: string;
  user_id: string;
  role: "assistant" | "system";
  content: string;
  server_id: string;
  created_at: string;
}

export interface PushSubscriptionHandle {
  unsubscribe: () => void;
}

export async function subscribeToPushMessages(
  _options: Record<string, unknown>,
): Promise<PushSubscriptionHandle> {
  // No-op in OSS mode — push messages are delivered via SSE
  return { unsubscribe: () => {} };
}
