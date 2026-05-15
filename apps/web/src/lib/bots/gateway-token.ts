import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Fetch the active GATEWAY_TOKEN for a bot.
 * Returns null if the bot has no active token.
 *
 * Caller is responsible for verifying user ownership of the bot before calling.
 */
export async function fetchBotGatewayToken(botId: string): Promise<string | null> {
  const supabase = createAdminClient();
  const { data } = await supabase
    .from("gateway_tokens")
    .select("token")
    .eq("bot_id", botId)
    .eq("is_active", true)
    .single();
  return data?.token ?? null;
}

export const CHAT_PROXY_URL = process.env.CHAT_PROXY_URL || "https://chat.openmagi.ai";
