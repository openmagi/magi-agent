import { env } from "@/lib/config";
import { AppError } from "@/lib/errors";

interface AnthropicMessage {
  role: "user" | "assistant";
  content: string;
}

interface AnthropicResponse {
  content: Array<{ type: string; text?: string }>;
}

/**
 * Call the Anthropic Messages API directly via fetch.
 * Used for lightweight LLM tasks (NL conversion, classification).
 * No SDK dependency — uses raw HTTP.
 */
export async function callAnthropic(opts: {
  system: string;
  messages: AnthropicMessage[];
  model?: string;
  maxTokens?: number;
}): Promise<string> {
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new AppError("ANTHROPIC_API_KEY not configured", 500);
  }

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: opts.model ?? "claude-haiku-4-5-20251001",
      max_tokens: opts.maxTokens ?? 500,
      system: opts.system,
      messages: opts.messages,
    }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "unknown");
    throw new AppError(`Anthropic API error: ${res.status} ${body}`, 502);
  }

  const data = (await res.json()) as AnthropicResponse;
  const text = data.content
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text)
    .join("");

  if (!text) {
    throw new AppError("Anthropic returned empty response", 502);
  }

  return text;
}
