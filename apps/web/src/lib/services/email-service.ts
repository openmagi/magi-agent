import { createAdminClient } from "@/lib/supabase/admin";
import { env } from "@/lib/config";
import { PLAN_EMAIL_QUOTA } from "@/lib/billing/plans";
import { AppError } from "@/lib/errors";

const AGENTMAIL_API_BASE = "https://api.agentmail.to/v0";
const EMAIL_DOMAIN = "agentmail.openmagi.ai";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function getSupabase(): any {
  return createAdminClient();
}

interface AgentMailInbox {
  inbox_id: string;
  display_name?: string;
}

async function agentmailRequest(
  method: string,
  path: string,
  body?: Record<string, unknown>
): Promise<{ status: number; data: unknown }> {
  const apiKey = env.AGENTMAIL_API_KEY;
  if (!apiKey) {
    throw new AppError("AgentMail API key not configured", 500);
  }

  const res = await fetch(`${AGENTMAIL_API_BASE}${path}`, {
    method,
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

  const data = await res.json().catch(() => null);
  return { status: res.status, data };
}

/** Create an AgentMail inbox for a bot. Idempotent via clientId=botId. */
export async function createEmailInbox(
  userId: string,
  botId: string,
  botName: string,
  username?: string
): Promise<{ email: string; inboxId: string }> {
  const supabase = getSupabase();

  // Check plan eligibility
  const { data: sub } = await supabase
    .from("subscriptions")
    .select("plan")
    .eq("user_id", userId)
    .single();

  const plan = sub?.plan ?? "byok";
  const emailLimit = PLAN_EMAIL_QUOTA[plan];
  if (emailLimit === undefined) {
    throw new AppError("Email integration requires Pro or Pro+ plan", 403);
  }

  // Check if inbox already exists for this bot
  const { data: existing } = await supabase
    .from("bot_email_inboxes")
    .select("inbox_id, email_address, enabled")
    .eq("bot_id", botId)
    .single();

  if (existing) {
    // Re-enable if disabled
    if (!existing.enabled) {
      await supabase
        .from("bot_email_inboxes")
        .update({ enabled: true, updated_at: new Date().toISOString() })
        .eq("bot_id", botId);
    }
    return { email: existing.email_address, inboxId: existing.inbox_id };
  }

  // Create inbox via AgentMail API (clientId for idempotency)
  // AgentMail display_name disallows parentheses and some special chars
  const displayName = botName.replace(/[^a-zA-Z0-9\s\-_.]/g, "").trim() || "Open Magi Bot";
  const inboxPayload: Record<string, unknown> = {
    domain: EMAIL_DOMAIN,
    display_name: displayName,
    client_id: botId,
  };
  if (username) {
    inboxPayload.username = username.toLowerCase().replace(/[^a-z0-9._-]/g, "");
  }
  const result = await agentmailRequest("POST", "/inboxes", inboxPayload);

  if (result.status !== 200 && result.status !== 201) {
    console.error("[email-service] AgentMail inbox creation failed:", result);
    throw new AppError("Failed to create email inbox", 502);
  }

  const inbox = result.data as AgentMailInbox;
  // AgentMail returns email address as inbox_id (e.g. "username@openmagi.ai")
  const emailAddress = inbox.inbox_id;

  // Store in database
  await supabase.from("bot_email_inboxes").insert({
    bot_id: botId,
    user_id: userId,
    inbox_id: emailAddress,
    email_address: emailAddress,
    display_name: displayName,
    enabled: true,
  });

  // Initialize email quota if not exists
  await supabase.rpc("reset_email_quota", {
    p_user_id: userId,
    p_monthly_limit: emailLimit,
  });

  return { email: emailAddress, inboxId: emailAddress };
}

/** Soft-disable email inbox for a bot. */
export async function disableEmailInbox(
  userId: string,
  botId: string
): Promise<void> {
  const supabase = getSupabase();

  await supabase
    .from("bot_email_inboxes")
    .update({ enabled: false, updated_at: new Date().toISOString() })
    .eq("bot_id", botId)
    .eq("user_id", userId);
}

/** Get email inbox status for a bot. */
export async function getEmailInbox(
  userId: string,
  botId: string
): Promise<{ enabled: boolean; email: string | null; inboxId: string | null }> {
  const supabase = getSupabase();

  const { data } = await supabase
    .from("bot_email_inboxes")
    .select("inbox_id, email_address, enabled")
    .eq("bot_id", botId)
    .eq("user_id", userId)
    .single();

  if (!data) {
    return { enabled: false, email: null, inboxId: null };
  }

  return {
    enabled: data.enabled,
    email: data.email_address,
    inboxId: data.inbox_id,
  };
}

/** Delete AgentMail inbox (best-effort). Used during bot cleanup. */
export async function deleteEmailInbox(botId: string): Promise<void> {
  const supabase = getSupabase();

  const { data } = await supabase
    .from("bot_email_inboxes")
    .select("inbox_id")
    .eq("bot_id", botId)
    .single();

  if (!data) return;

  // Best-effort AgentMail API deletion
  try {
    await agentmailRequest("DELETE", `/inboxes/${encodeURIComponent(data.inbox_id)}`);
  } catch (err) {
    console.error(`[email-service] Failed to delete AgentMail inbox for bot ${botId}:`, err);
  }

  // DB row will be cascade-deleted with the bot
}
