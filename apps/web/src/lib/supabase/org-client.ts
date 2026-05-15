import type { SupabaseClient } from "@supabase/supabase-js";
import { createAdminClient } from "./admin";

/**
 * Returns an untyped Supabase admin client for querying org tables
 * (organizations, org_members, org_invites, org_summaries)
 * which are not yet in the auto-generated database.types.ts.
 *
 * Remove this cast once `npx supabase gen types` is re-run.
 */
export function createOrgClient(): SupabaseClient {
  return createAdminClient() as unknown as SupabaseClient;
}
