import { createAdminClient } from "@/lib/supabase/admin";
import { AppError } from "@/lib/errors";
import type { Tables } from "@/lib/supabase/database.types";
import { safeCompare } from "@/lib/auth/safe-compare";
import { hashToken } from "@/lib/auth/hash-token";

export interface BotGatewayContext {
  bot: Tables<"bots">;
  params: Record<string, string>;
}

type BotGatewayHandler = (
  request: Request,
  ctx: BotGatewayContext
) => Promise<Response>;

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a handler to require GATEWAY_TOKEN auth (bot-to-platform).
 * Validates the Bearer token against the gateway_tokens table,
 * verifies the token's bot_id matches the URL param, and loads the bot.
 *
 * Used for endpoints that bots call internally (e.g. x402 pay).
 */
export function withBotGateway(handler: BotGatewayHandler): RouteHandler {
  return async (request, context) => {
    const authHeader = request.headers.get("authorization");
    if (!authHeader?.startsWith("Bearer ")) {
      throw new AppError("Unauthorized", 401);
    }
    const token = authHeader.slice(7);
    if (!token) {
      throw new AppError("Unauthorized", 401);
    }

    const params = await context.params;
    const botId = params.botId;
    if (!botId) {
      throw new AppError("Missing botId parameter", 400);
    }

    const supabase = createAdminClient();

    // Phase 1 of hash migration (2026-04-18): look up by prefix, constant-time
    // compare the presented token against either (a) token_hash on the row,
    // or (b) legacy plaintext `token` column for rows that predate the backfill.
    const { prefix, hash } = hashToken(token);
    if (!prefix) throw new AppError("Unauthorized", 401);

    // token_prefix / token_hash columns were added in migration 085 but the
    // generated database.types may not yet reflect them.
    const { data: candidates, error: tokenError } = await supabase
      .from("gateway_tokens")
      .select("bot_id, token, token_hash" as "bot_id")
      .eq("token_prefix" as "token", prefix)
      .eq("is_active", true);

    if (tokenError || !candidates || candidates.length === 0) {
      throw new AppError("Unauthorized", 401);
    }

    type GatewayRow = { bot_id: string; token: string | null; token_hash: string | null };
    const rows = candidates as unknown as GatewayRow[];
    const matched = rows.find((r) =>
      r.token_hash
        ? safeCompare(r.token_hash, hash)
        : safeCompare(r.token ?? "", token),
    );

    if (!matched || matched.bot_id !== botId) {
      throw new AppError("Unauthorized", 401);
    }

    // Load the bot
    const { data: bot, error: botError } = await supabase
      .from("bots")
      .select("*")
      .eq("id", botId)
      .single();

    if (botError || !bot) {
      throw new AppError("Bot not found", 404);
    }

    return handler(request, { bot, params });
  };
}
