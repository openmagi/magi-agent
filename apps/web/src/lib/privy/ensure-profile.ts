import { createAdminClient } from "@/lib/supabase/admin";

export async function ensureProfile(userId: string, displayName?: string) {
  const supabase = createAdminClient();
  const { data: existing } = await supabase
    .from("profiles")
    .select("id")
    .eq("id", userId)
    .single();

  if (!existing) {
    await supabase.from("profiles").insert({
      id: userId,
      display_name: displayName ?? null,
      onboarding_completed: false,
    });
  }

  return existing ?? { id: userId };
}
