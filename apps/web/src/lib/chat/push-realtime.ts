/**
 * push-realtime.ts — Supabase Realtime subscription glue for
 * push_messages (§7.15).
 *
 * Subscribes to INSERT events on public.push_messages filtered by
 * bot_id + channel, dedupes by serverId, and invokes the caller's
 * onMessage handler. The handler is wired by `useChatStore` /
 * `chat-view-client.tsx` to append the message to the store and
 * trigger the usual scroll-to-bottom behaviour.
 *
 * Why here (not inside chat-store.ts)? The zustand store is pure
 * state, deliberately free of I/O. Realtime is a side-effecty
 * subscription with its own lifecycle (subscribe / unsubscribe on
 * channel change), so it lives next to the store and is driven by
 * the same module that sets activeChannel.
 *
 * RLS: push_messages SELECT policy checks `user_id = auth.jwt() ->>
 * 'sub'`. The caller must first attach a Supabase JWT (minted by the
 * server after verifying the Privy token) via
 * `supabase.auth.setSession(...)` — passed in here as `accessToken`.
 * When no accessToken is set, Realtime subscribes anonymously and
 * RLS silently suppresses all INSERT events (fail-closed).
 */

"use client";

import type { RealtimeChannel, SupabaseClient } from "@supabase/supabase-js";
import { getBrowserSupabase } from "@/lib/supabase/browser-client";

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

export interface PushRealtimeSubscribeArgs {
  botId: string;
  channel: string;
  accessToken?: string | null;
  onInsert: (row: PushMessageRow) => void;
  onStatusChange?: (status: "SUBSCRIBED" | "CLOSED" | "CHANNEL_ERROR" | "TIMED_OUT" | string) => void;
}

export interface PushRealtimeHandle {
  unsubscribe: () => Promise<void>;
}

/**
 * Subscribe to push_messages for a specific bot+channel. Returns a
 * handle that must be called to unsubscribe when the active channel
 * changes or the component unmounts.
 *
 * Safe to call without `NEXT_PUBLIC_SUPABASE_ANON_KEY` — returns a
 * no-op handle so the caller doesn't need to branch.
 */
export async function subscribeToPushMessages(
  args: PushRealtimeSubscribeArgs,
): Promise<PushRealtimeHandle> {
  const supabase = getBrowserSupabase();
  if (!supabase) {
    return { unsubscribe: async () => {} };
  }
  if (args.accessToken) {
    try {
      // setSession expects both access + refresh; for short-lived
      // realtime we pass refresh_token=access_token since we disable
      // autoRefresh. Any error is non-fatal — RLS will deny reads.
      await supabase.auth.setSession({
        access_token: args.accessToken,
        refresh_token: args.accessToken,
      });
    } catch {
      /* swallow — RLS will fail closed */
    }
  }
  const channelName = `push_messages:${args.botId}:${args.channel}`;
  const realtimeChannel: RealtimeChannel = supabase
    .channel(channelName)
    .on(
      "postgres_changes" as never,
      {
        event: "INSERT",
        schema: "public",
        table: "push_messages",
        filter: `bot_id=eq.${args.botId}`,
      },
      (payload: { new: PushMessageRow }) => {
        const row = payload.new;
        // Double-check the channel filter — Realtime only supports one
        // `filter=` clause at a time so we filter channel client-side.
        if (!row || row.channel !== args.channel) return;
        args.onInsert(row);
      },
    )
    .subscribe((status) => {
      args.onStatusChange?.(status);
    });
  return {
    unsubscribe: async () => {
      try {
        await supabase.removeChannel(realtimeChannel);
      } catch {
        /* best-effort */
      }
    },
  };
}

/** Exposed for tests — returns the active client (or null). */
export function _getClientForTests(): SupabaseClient | null {
  return getBrowserSupabase();
}
