/**
 * Browser-safe Supabase client — anonymous, used for push_messages
 * Realtime subscription (§7.15).
 *
 * RLS on push_messages restricts SELECT to rows matching
 * `auth.jwt() ->> 'sub'`. For Phase 1 we rely on a Privy-derived JWT
 * being set on the client via `supabase.auth.setSession({access_token,
 * refresh_token})` — the caller provides the token. See
 * `src/lib/chat/push-realtime.ts` for the subscription glue.
 *
 * Singleton pattern — one instance per browser tab so that multiple
 * subscriptions share the same WebSocket connection (Supabase Realtime
 * multiplexes channels over a single socket).
 */

"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

/**
 * Get (or lazily create) the shared browser Supabase client. Returns
 * null when the anon key isn't configured so callers can gracefully
 * degrade to the polling path.
 */
export function getBrowserSupabase(): SupabaseClient | null {
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return null;
  _client = createClient(url, anonKey, {
    auth: {
      // Disable auth persistence — Privy is the source of truth; we
      // only ever setSession() transiently to attach a JWT for RLS.
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    realtime: {
      // Silence default timeout warnings; 10s is plenty for a push.
      params: { eventsPerSecond: 10 },
    },
  });
  return _client;
}
