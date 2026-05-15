import { createClient } from "@supabase/supabase-js";
import type { Database } from "./database.types";
import { env } from "@/lib/config";

export function createAdminClient() {
  return createClient<Database>(
    env.NEXT_PUBLIC_SUPABASE_URL,
    env.SUPABASE_SERVICE_ROLE_KEY
  );
}
