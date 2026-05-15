const DISCORD_ENABLED_PLANS = new Set(["max", "flex"]);

export function isDiscordEnabled(plan: string): boolean {
  return DISCORD_ENABLED_PLANS.has(plan);
}
