/**
 * createProvider — factory function for multi-provider LLM instantiation.
 *
 * Returns an `LLMProvider` for the requested backend so callers can
 * switch between hosted providers and OpenAI-compatible local/self-hosted
 * servers without importing concrete classes.
 *
 * ```ts
 * const provider = createProvider({
 *   provider: "openai",
 *   apiKey: process.env.OPENAI_API_KEY!,
 *   defaultModel: "gpt-5.4",
 * });
 * for await (const evt of provider.stream({ messages })) { ... }
 * ```
 */

import type { LLMProvider } from "./LLMProvider.js";
import { AnthropicProvider } from "./providers/AnthropicProvider.js";
import { OpenAIProvider } from "./providers/OpenAIProvider.js";
import { GoogleProvider } from "./providers/GoogleProvider.js";

export type ProviderName = "anthropic" | "openai" | "google" | "openai-compatible";

/** Provider configuration passed to {@link createProvider}. */
export interface ProviderConfig {
  /** Which LLM backend to use. */
  provider: ProviderName;
  /** API key for the chosen provider. Optional for no-auth local model servers. */
  apiKey?: string;
  /** Override the provider's default base URL (required for openai-compatible). */
  baseUrl?: string;
  /** Default model when `LLMStreamRequest.model` is omitted. */
  defaultModel?: string;
  /** Request timeout in milliseconds. Defaults to 600 000 (10 min). */
  timeoutMs?: number;
}

/**
 * Create an `LLMProvider` for the given configuration.
 *
 * @throws {Error} If the provider name is not recognised.
 */
export function createProvider(config: ProviderConfig): LLMProvider {
  switch (config.provider) {
    case "anthropic":
      return new AnthropicProvider({
        apiKey: requireApiKey(config),
        baseUrl: config.baseUrl,
        defaultModel: config.defaultModel,
        timeoutMs: config.timeoutMs,
      });

    case "openai":
      return new OpenAIProvider({
        apiKey: requireApiKey(config),
        baseUrl: config.baseUrl,
        defaultModel: config.defaultModel,
        timeoutMs: config.timeoutMs,
      });

    case "google":
      return new GoogleProvider({
        apiKey: requireApiKey(config),
        baseUrl: config.baseUrl,
        defaultModel: config.defaultModel,
        timeoutMs: config.timeoutMs,
      });

    case "openai-compatible":
      return new OpenAIProvider({
        apiKey: cleanOptional(config.apiKey),
        baseUrl: requireBaseUrl(config),
        defaultModel: config.defaultModel,
        timeoutMs: config.timeoutMs,
      });

    default:
      throw new Error(
        `Unknown LLM provider: "${(config as { provider: string }).provider}". ` +
          `Supported providers: anthropic, openai, google, openai-compatible.`,
      );
  }
}

function cleanOptional(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : undefined;
}

function requireApiKey(config: ProviderConfig): string {
  const apiKey = cleanOptional(config.apiKey);
  if (!apiKey) {
    throw new Error(`Missing apiKey for hosted LLM provider "${config.provider}".`);
  }
  return apiKey;
}

function requireBaseUrl(config: ProviderConfig): string {
  const baseUrl = cleanOptional(config.baseUrl);
  if (!baseUrl) {
    throw new Error("Missing baseUrl for openai-compatible LLM provider.");
  }
  return baseUrl;
}
