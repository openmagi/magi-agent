import { createAdminClient } from "@/lib/supabase/admin";
import { FALLBACK_MAX_SEATS } from "@/lib/constants";

/**
 * Read cluster bot capacity from the database.
 * The provisioning-worker (running in-cluster) periodically calculates this
 * from K8s node allocatable memory and stores it in platform_settings.
 * Falls back to FALLBACK_MAX_SEATS if no value is stored yet.
 */
export async function getClusterBotCapacity(): Promise<number> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const supabase: any = createAdminClient();
    const { data } = await supabase
      .from("platform_settings")
      .select("value")
      .eq("key", "cluster_capacity")
      .single();

    if (data?.value) {
      const capacity = parseInt(data.value, 10);
      if (!isNaN(capacity) && capacity > 0) return capacity;
    }
  } catch {
    // DB unreachable — use fallback
  }
  return FALLBACK_MAX_SEATS;
}
