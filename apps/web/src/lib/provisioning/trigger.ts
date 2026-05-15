import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Triggers provisioning for a bot.
 *
 * Sets status to "provisioning" in the database. The in-cluster
 * provisioning worker polls for this status and runs the K8s pipeline.
 *
 * In development (no cluster), immediately marks the bot as active
 * so the frontend flow isn't blocked.
 */
export async function triggerProvisioning(botId: string): Promise<void> {
  const supabase = createAdminClient();

  if (
    process.env.NODE_ENV === "development" &&
    !process.env.KUBECONFIG_CONTENT
  ) {
    // No K8s available — skip provisioning, mark as active
    await supabase
      .from("bots")
      .update({ status: "active", health_status: "healthy" })
      .eq("id", botId);
    return;
  }

  // Production: set status to provisioning, worker picks it up
  await supabase
    .from("bots")
    .update({
      status: "provisioning",
      error_message: null,
      updated_at: new Date().toISOString(),
    })
    .eq("id", botId);
}

/**
 * Reprovision a user's active bot (e.g. after plan switch).
 * Finds the user's active/provisioning bot and re-triggers provisioning.
 */
export async function reprovisionUserBot(userId: string): Promise<void> {
  const supabase = createAdminClient();

  const { data: bot } = await supabase
    .from("bots")
    .select("id")
    .eq("user_id", userId)
    .in("status", ["active", "provisioning"])
    .single();

  if (!bot) return;

  await supabase
    .from("bots")
    .update({
      status: "provisioning",
      error_message: null,
      updated_at: new Date().toISOString(),
    })
    .eq("id", bot.id);
}
