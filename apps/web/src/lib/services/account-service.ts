import { createAdminClient } from "@/lib/supabase/admin";
import { getStripe } from "@/lib/api/stripe";
import { deleteBotAndCleanup } from "@/lib/services/bot-service";
import { captureServerEvent } from "@/lib/posthog/server";

/**
 * Permanently delete a user account and all associated data.
 *
 * Deletion order:
 * 1. Hard-delete all user bots via deleteBotAndCleanup
 * 2. Cancel Stripe subscriptions + delete customer
 * 3. Delete Privy user
 * 4. Delete orphan tables with no FK cascade
 * 5. Delete profiles row (CASCADE handles linked tables)
 * 6. Capture analytics event
 */
export async function deleteAccount(userId: string): Promise<void> {
  const supabase = createAdminClient();

  // 1. Fetch and delete all user bots, including deleted tombstones that may
  // still carry cleanup state from a previous partial deletion.
  const { data: bots } = await supabase
    .from("bots")
    .select("id")
    .eq("user_id", userId);

  if (bots && bots.length > 0) {
    const failures: string[] = [];
    for (const bot of bots) {
      try {
        await deleteBotAndCleanup(bot.id);
      } catch (err) {
        console.error(`[account-service] Bot cleanup failed for ${bot.id}:`, err);
        failures.push(`${bot.id}: ${errorMessage(err)}`);
      }
    }
    if (failures.length > 0) {
      throw new Error(`Bot cleanup failed: ${failures.join("; ")}`);
    }
  }

  // 2. Cancel Stripe subscriptions and delete customer
  try {
    const { data: profile } = await supabase
      .from("profiles")
      .select("stripe_customer_id")
      .eq("id", userId)
      .single();

    const stripeCustomerId = (profile as unknown as Record<string, unknown>)
      ?.stripe_customer_id as string | undefined;

    if (stripeCustomerId) {
      const stripe = getStripe();

      // Cancel all active subscriptions first. Stripe list defaults to 10,
      // so page explicitly to avoid missing extra customer subscriptions.
      let startingAfter: string | undefined;
      do {
        const subscriptions = await stripe.subscriptions.list({
          customer: stripeCustomerId,
          status: "all",
          limit: 100,
          ...(startingAfter ? { starting_after: startingAfter } : {}),
        });
        for (const sub of subscriptions.data) {
          if (sub.status === "active" || sub.status === "trialing") {
            await stripe.subscriptions.cancel(sub.id);
          }
        }
        startingAfter = subscriptions.has_more
          ? subscriptions.data.at(-1)?.id
          : undefined;
      } while (startingAfter);

      await stripe.customers.del(stripeCustomerId);
    }
  } catch (err) {
    throw new Error(`Stripe cleanup failed for ${userId}: ${errorMessage(err)}`);
  }

  // 3. Delete Privy user before local account rows so failures keep a retryable
  // local handle.
  try {
    const { PrivyClient } = await import("@privy-io/server-auth");
    const { env } = await import("@/lib/config");
    const privy = new PrivyClient(env.NEXT_PUBLIC_PRIVY_APP_ID, env.PRIVY_APP_SECRET);
    await privy.deleteUser(userId);
  } catch (err) {
    throw new Error(`Privy user delete failed for ${userId}: ${errorMessage(err)}`);
  }

  // 4. Delete orphan tables (not in typed schema — use untyped client)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const untyped = supabase as any;
  const orphanDeleteResults = await Promise.allSettled([
    untyped.from("subscriptions").delete().eq("user_id", userId),
    untyped.from("credits").delete().eq("user_id", userId),
    untyped.from("email_quotas").delete().eq("user_id", userId),
    untyped.from("search_quotas").delete().eq("user_id", userId),
    untyped.from("credit_grants").delete().eq("user_id", userId),
    untyped.from("search_usage").delete().eq("user_id", userId),
    untyped.from("email_usage").delete().eq("user_id", userId),
    untyped.from("analytics_daily").delete().eq("user_id", userId),
    untyped.from("user_interactions").delete().eq("user_id", userId),
    untyped.from("skill_executions").delete().eq("user_id", userId),
  ]);
  const orphanFailures = orphanDeleteResults
    .map((result, index) => ({ result, table: ACCOUNT_ORPHAN_TABLES[index] }))
    .filter(
      ({ result }) =>
        result.status === "rejected" ||
        (result.status === "fulfilled" && result.value?.error),
    )
    .map(({ result, table }) =>
      result.status === "rejected"
        ? `${table}: ${errorMessage(result.reason)}`
        : `${table}: ${result.value.error.message}`,
    );

  if (orphanFailures.length > 0) {
    throw new Error(`Account table cleanup failed: ${orphanFailures.join("; ")}`);
  }

  // 5. Delete profiles row — CASCADE handles linked tables
  const { error } = await supabase
    .from("profiles")
    .delete()
    .eq("id", userId);

  if (error) {
    throw new Error(`Failed to delete profile: ${error.message}`);
  }

  // 6. Analytics event
  captureServerEvent(userId, "account_deleted");
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

const ACCOUNT_ORPHAN_TABLES = [
  "subscriptions",
  "credits",
  "email_quotas",
  "search_quotas",
  "credit_grants",
  "search_usage",
  "email_usage",
  "analytics_daily",
  "user_interactions",
  "skill_executions",
] as const;
