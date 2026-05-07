/**
 * src/llm/ — Multi-provider LLM abstraction layer.
 *
 * Re-exports all public types and factories so consumers can import
 * from a single entry point:
 *
 * ```ts
 * import { createProvider, type LLMProvider } from "./llm/index.js";
 * ```
 *
 * The canonical event/message/tool types live in
 * `src/transport/LLMClient.ts` and are NOT re-exported here to avoid
 * circular dependencies — import them directly from the transport layer.
 */

// ── Interface ──
export type { LLMProvider } from "./LLMProvider.js";

// ── Factory ──
export { createProvider } from "./createProvider.js";
export type { ProviderConfig } from "./createProvider.js";

// ── Concrete providers (for advanced use / direct instantiation) ──
export { AnthropicProvider } from "./providers/AnthropicProvider.js";
export type { AnthropicProviderOptions } from "./providers/AnthropicProvider.js";

export { OpenAIProvider } from "./providers/OpenAIProvider.js";
export type { OpenAIProviderOptions } from "./providers/OpenAIProvider.js";

export { GoogleProvider } from "./providers/GoogleProvider.js";
export type { GoogleProviderOptions } from "./providers/GoogleProvider.js";

// ── Shared utilities (for custom provider implementations) ──
export {
  parseAnthropicSse,
  parseGenericSse,
  httpPost,
  consumeText,
} from "./sseUtils.js";
export type { HttpPostOptions } from "./sseUtils.js";

// ── Model capabilities ──
export {
  getCapability,
  getRegisteredCapability,
  computeUsd,
  shouldEnableThinkingByDefault,
  getContextWindowOrDefault,
  MODEL_CAPABILITIES,
  registerModelCapability,
  resetCustomModelCapabilitiesForTests,
  DEFAULT_CONTEXT_WINDOW_TOKENS,
} from "./modelCapabilities.js";
export type { ModelCapability } from "./modelCapabilities.js";
