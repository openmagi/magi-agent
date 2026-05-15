import { createHmac } from "crypto";
import { env } from "@/lib/config";

const TELEGRAM_API = "https://api.telegram.org/bot";

function telegramApiUrl(botToken: string, method: string): string {
  return `${TELEGRAM_API}${encodeURIComponent(botToken)}/${method}`;
}

/**
 * Deterministic HMAC-SHA256 secret for Telegram webhook validation.
 * No DB storage needed — derived from botId + ENCRYPTION_KEY.
 */
export function generateWebhookSecret(botId: string): string {
  return createHmac("sha256", env.ENCRYPTION_KEY)
    .update(botId)
    .digest("hex")
    .slice(0, 64);
}

/**
 * Set a Telegram webhook so the first /start sender becomes the bot owner.
 * Best-effort: logs errors but does not throw.
 */
export async function setOwnerWebhook(
  botToken: string,
  botId: string,
): Promise<void> {
  const secret = generateWebhookSecret(botId);
  const webhookUrl = `https://openmagi.ai/api/bots/${botId}/telegram-webhook`;

  try {
    const res = await fetch(telegramApiUrl(botToken, "setWebhook"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: webhookUrl,
        secret_token: secret,
        allowed_updates: ["message"],
        drop_pending_updates: true,
      }),
      signal: AbortSignal.timeout(10_000),
    });

    if (!res.ok) {
      const body = await res.text();
      console.error(`[telegram-webhook] setWebhook failed (${res.status}):`, body);
    }
  } catch (err) {
    console.error("[telegram-webhook] setWebhook error:", err);
  }
}

/**
 * Remove the Telegram webhook. Best-effort.
 */
export async function deleteOwnerWebhook(botToken: string): Promise<void> {
  try {
    const res = await fetch(telegramApiUrl(botToken, "deleteWebhook"), {
      method: "POST",
      signal: AbortSignal.timeout(10_000),
    });

    if (!res.ok) {
      const body = await res.text();
      console.error(`[telegram-webhook] deleteWebhook failed (${res.status}):`, body);
    }
  } catch (err) {
    console.error("[telegram-webhook] deleteWebhook error:", err);
  }
}

/**
 * Send a text message via the Telegram Bot API. Best-effort.
 */
export async function sendTelegramMessage(
  botToken: string,
  chatId: number,
  text: string,
): Promise<void> {
  try {
    const res = await fetch(telegramApiUrl(botToken, "sendMessage"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
      signal: AbortSignal.timeout(10_000),
    });

    if (!res.ok) {
      const body = await res.text();
      console.error(`[telegram-webhook] sendMessage failed (${res.status}):`, body);
    }
  } catch (err) {
    console.error("[telegram-webhook] sendMessage error:", err);
  }
}
