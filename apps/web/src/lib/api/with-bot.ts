import { getAuthUserFromHeader } from "@/lib/privy/server-auth";
import { createAdminClient } from "@/lib/supabase/admin";
import { resolveViewAsUserId } from "@/lib/admin/view-as-api";
import { AppError } from "@/lib/errors";
import type { Tables } from "@/lib/supabase/database.types";

export interface BotContext {
  auth: { userId: string };
  bot: Tables<"bots">;
  params: Record<string, string>;
}

type BotHandler = (
  request: Request,
  ctx: BotContext
) => Promise<Response>;

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a handler to require auth + bot ownership.
 * Loads the bot by `params.botId` and verifies the authenticated user owns it.
 * For admin viewAs (GET only), allows accessing another user's bot.
 * Throws AppError(401) for missing auth, AppError(404) for missing/unowned bot.
 */
export function withBot(handler: BotHandler): RouteHandler {
  return async (request, context) => {
    const auth = await getAuthUserFromHeader(request);
    if (!auth) {
      throw new AppError("Unauthorized", 401);
    }

    const params = await context.params;
    const botId = params.botId;
    if (!botId) {
      throw new AppError("Missing botId parameter", 400);
    }

    const effectiveUserId = request.method === "GET"
      ? resolveViewAsUserId(request, auth.userId)
      : auth.userId;

    const supabase = createAdminClient();
    const { data: bot, error } = await supabase
      .from("bots")
      .select("*")
      .eq("id", botId)
      .eq("user_id", effectiveUserId)
      .single();

    if (error || !bot) {
      throw new AppError("Bot not found", 404);
    }

    return handler(request, { auth, bot, params });
  };
}
