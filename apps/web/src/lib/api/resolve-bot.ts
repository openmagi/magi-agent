import { getAuthUser } from "@/lib/privy/server-auth";
import { createAdminClient } from "@/lib/supabase/admin";
import { NextResponse } from "next/server";

interface ResolvedBot {
  auth: { userId: string };
  botId: string;
}

type ResolveResult =
  | { ok: true; data: ResolvedBot }
  | { ok: false; response: NextResponse };

/**
 * [codex gate3 P1] Fall back to the user's first (oldest) non-deleted bot
 * when the caller doesn't supply a botId. This preserves legacy/mobile
 * clients that still call /api/knowledge/* without the new botId query
 * parameter. Returns null if the user has no bots at all — caller should
 * treat this as a real 400.
 */
async function firstBotIdForUser(userId: string): Promise<string | null> {
  const supabase = createAdminClient();
  const { data } = await supabase
    .from("bots")
    .select("id")
    .eq("user_id", userId)
    .neq("status", "deleted")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  return data?.id ?? null;
}

/**
 * Resolves botId from query parameter and validates ownership.
 * Used by knowledge/quota APIs that aren't under /api/bots/[botId]/ routes.
 *
 * If `botId` is missing from the query, falls back to the user's first
 * bot (see firstBotIdForUser). This keeps already-deployed mobile clients
 * that don't pass botId working without a forced rebuild.
 */
export async function resolveBotFromQuery(request: Request): Promise<ResolveResult> {
  const auth = await getAuthUser();
  if (!auth) {
    return { ok: false, response: NextResponse.json({ error: "unauthorized" }, { status: 401 }) };
  }

  const url = new URL(request.url);
  let botId = url.searchParams.get("botId");
  if (!botId) {
    botId = await firstBotIdForUser(auth.userId);
    if (!botId) {
      return {
        ok: false,
        response: NextResponse.json({ error: "no bots for this user" }, { status: 400 }),
      };
    }
    return { ok: true, data: { auth, botId } };
  }

  const supabase = createAdminClient();
  const { data: bot } = await supabase
    .from("bots")
    .select("id")
    .eq("id", botId)
    .eq("user_id", auth.userId)
    .neq("status", "deleted")
    .single();

  if (!bot) {
    return { ok: false, response: NextResponse.json({ error: "bot not found" }, { status: 404 }) };
  }

  return { ok: true, data: { auth, botId: bot.id } };
}

/**
 * Resolves botId from JSON body and validates ownership.
 * Used by POST/DELETE APIs that send botId in the request body.
 *
 * Same fallback as resolveBotFromQuery: when botId is omitted, use the
 * user's first bot. See gate3 P1 note above.
 */
export async function resolveBotFromBody(
  auth: { userId: string },
  botId: string | undefined
): Promise<ResolveResult> {
  if (!botId) {
    const fallback = await firstBotIdForUser(auth.userId);
    if (!fallback) {
      return {
        ok: false,
        response: NextResponse.json({ error: "no bots for this user" }, { status: 400 }),
      };
    }
    return { ok: true, data: { auth, botId: fallback } };
  }

  const supabase = createAdminClient();
  const { data: bot } = await supabase
    .from("bots")
    .select("id")
    .eq("id", botId)
    .eq("user_id", auth.userId)
    .neq("status", "deleted")
    .single();

  if (!bot) {
    return { ok: false, response: NextResponse.json({ error: "bot not found" }, { status: 404 }) };
  }

  return { ok: true, data: { auth, botId: bot.id } };
}
