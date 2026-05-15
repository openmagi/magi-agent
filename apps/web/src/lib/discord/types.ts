export interface DiscordGuild {
  id: string;
  user_id: string;
  guild_id: string;
  guild_name: string | null;
  guild_icon: string | null;
  created_at: string;
}

export interface DiscordBotMapping {
  id: string;
  guild_id: string;
  bot_id: string;
  display_name: string;
  avatar_url: string | null;
  webhook_urls: Record<string, string>;
  is_active: boolean;
  created_at: string;
}
