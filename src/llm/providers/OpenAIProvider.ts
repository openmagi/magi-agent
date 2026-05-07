/**
 * OpenAIProvider — streams completions via raw HTTP to the OpenAI Chat
 * Completions API (`/v1/chat/completions`).
 *
 * Zero external dependencies. Converts Anthropic-format messages/tools
 * into OpenAI format on the way in, and converts OpenAI streaming deltas
 * back into the canonical `LLMEvent` stream on the way out.
 *
 * Key conversions:
 *   - Anthropic `system` → OpenAI `messages[0].role = "system"`
 *   - Anthropic `LLMToolDef` → OpenAI `tools[].function`
 *   - Anthropic content blocks → OpenAI message content / tool_calls
 *   - OpenAI `choices[0].delta` → normalised `LLMEvent`
 */

import type { LLMProvider } from "../LLMProvider.js";
import type {
  LLMEvent,
  LLMStreamRequest,
  LLMMessage,
  LLMContentBlock,
  LLMToolDef,
  LLMUsage,
} from "../../transport/LLMClient.js";
import { getCapability } from "../modelCapabilities.js";
import { httpPost, consumeText, parseGenericSse } from "../sseUtils.js";

/** Configuration for the OpenAI provider. */
export interface OpenAIProviderOptions {
  /** OpenAI API key (sk-...). Optional for no-auth local model servers. */
  apiKey?: string;
  /** Override the base URL (e.g. for Azure OpenAI, Ollama, LM Studio, vLLM). Defaults to `https://api.openai.com`. */
  baseUrl?: string;
  /** Default model when `LLMStreamRequest.model` is omitted. */
  defaultModel?: string;
  /** Request timeout in milliseconds. Defaults to 600 000 (10 min). */
  timeoutMs?: number;
}

const DEFAULT_BASE_URL = "https://api.openai.com";
const DEFAULT_MODEL = "gpt-5.4";
const DEFAULT_TIMEOUT_MS = 600_000;

// ─── OpenAI wire types (minimal, inline) ───────────────────────────

interface OAIMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
  tool_calls?: OAIToolCall[];
  tool_call_id?: string;
}

interface OAIToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

interface OAITool {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: object;
  };
}

/**
 * OpenAI streaming provider. Sends requests to the OpenAI Chat
 * Completions API and normalises the streamed deltas into `LLMEvent`.
 */
export class OpenAIProvider implements LLMProvider {
  private readonly apiKey?: string;
  private readonly baseUrl: string;
  private readonly defaultModel: string;
  private readonly timeoutMs: number;

  constructor(opts: OpenAIProviderOptions) {
    this.apiKey = cleanOptional(opts.apiKey);
    this.baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.defaultModel = opts.defaultModel ?? DEFAULT_MODEL;
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  /**
   * Stream a completion from OpenAI's Chat Completions API.
   *
   * Converts the Anthropic-format request to OpenAI format, streams
   * the response, and converts each delta back to `LLMEvent`.
   */
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    const model = req.model ?? this.defaultModel;

    const oaiMessages = convertMessages(req.system, req.messages);
    const oaiTools = req.tools?.length ? convertTools(req.tools) : undefined;

    const body = JSON.stringify({
      model,
      messages: oaiMessages,
      ...(oaiTools ? { tools: oaiTools } : {}),
      max_tokens:
        req.max_tokens ?? (getCapability(model)?.maxOutputTokens ?? 8_192),
      temperature: req.temperature,
      stream: true,
      stream_options: { include_usage: true },
    });

    const res = await httpPost({
      url: `${this.baseUrl}/v1/chat/completions`,
      headers: {
        ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
        Accept: "text/event-stream",
      },
      body,
      timeoutMs: this.timeoutMs,
    });

    if (res.statusCode && res.statusCode >= 400) {
      const errBody = await consumeText(res);
      yield {
        kind: "error",
        code: `http_${res.statusCode}`,
        message: errBody.slice(0, 500) || `upstream ${res.statusCode}`,
      };
      return;
    }

    yield* this.parseOpenAISse(res);
  }

  /**
   * Parse OpenAI SSE deltas into normalised `LLMEvent`s.
   *
   * OpenAI streaming shape:
   *   choices[0].delta.content        → text
   *   choices[0].delta.tool_calls[i]  → tool use
   *   choices[0].finish_reason        → stop reason
   *   usage                           → token counts (final chunk)
   */
  private async *parseOpenAISse(
    res: import("node:http").IncomingMessage,
  ): AsyncGenerator<LLMEvent, void, void> {
    // Track active tool calls by index → blockIndex mapping
    const toolCallMap = new Map<number, { blockIndex: number; id: string; name: string }>();
    let nextBlockIndex = 0;
    let textBlockIndex = -1; // assigned on first text delta
    let usage: LLMUsage = { inputTokens: 0, outputTokens: 0 };
    let finishReason: string | null = null;

    for await (const payload of parseGenericSse(res)) {
      // Final chunk with usage (stream_options.include_usage)
      const usageObj = payload.usage as
        | { prompt_tokens?: number; completion_tokens?: number }
        | undefined;
      if (usageObj) {
        if (typeof usageObj.prompt_tokens === "number")
          usage.inputTokens = usageObj.prompt_tokens;
        if (typeof usageObj.completion_tokens === "number")
          usage.outputTokens = usageObj.completion_tokens;
      }

      const choices = payload.choices as
        | Array<{
            delta?: {
              content?: string | null;
              tool_calls?: Array<{
                index: number;
                id?: string;
                function?: { name?: string; arguments?: string };
              }>;
            };
            finish_reason?: string | null;
          }>
        | undefined;

      if (!choices?.length) continue;
      const choice = choices[0];

      // ── finish_reason ──
      if (choice?.finish_reason) {
        finishReason = choice.finish_reason;
      }

      const delta = choice?.delta;
      if (!delta) continue;

      // ── Text content ──
      if (delta.content) {
        if (textBlockIndex < 0) {
          textBlockIndex = nextBlockIndex++;
        }
        yield { kind: "text_delta", blockIndex: textBlockIndex, delta: delta.content };
      }

      // ── Tool calls ──
      if (delta.tool_calls) {
        for (const tc of delta.tool_calls) {
          const existing = toolCallMap.get(tc.index);

          if (!existing && tc.id && tc.function?.name) {
            // New tool call announced
            const blockIndex = nextBlockIndex++;
            toolCallMap.set(tc.index, {
              blockIndex,
              id: tc.id,
              name: tc.function.name,
            });
            yield {
              kind: "tool_use_start",
              blockIndex,
              id: tc.id,
              name: tc.function.name,
            };
            // First arguments chunk (if any)
            if (tc.function.arguments) {
              yield {
                kind: "tool_use_input_delta",
                blockIndex,
                partial: tc.function.arguments,
              };
            }
          } else if (existing && tc.function?.arguments) {
            // Streaming arguments delta
            yield {
              kind: "tool_use_input_delta",
              blockIndex: existing.blockIndex,
              partial: tc.function.arguments,
            };
          }
        }
      }
    }

    // Emit block_stop for all blocks
    if (textBlockIndex >= 0) {
      yield { kind: "block_stop", blockIndex: textBlockIndex };
    }
    for (const tc of toolCallMap.values()) {
      yield { kind: "block_stop", blockIndex: tc.blockIndex };
    }

    // Map OpenAI finish_reason → canonical stopReason
    const stopReason = mapFinishReason(finishReason);
    yield { kind: "message_end", stopReason, usage };
  }
}

function cleanOptional(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : undefined;
}

// ─── Format conversion helpers ─────────────────────────────────────

/**
 * Convert Anthropic-format system + messages into OpenAI messages array.
 */
function convertMessages(
  system: LLMStreamRequest["system"],
  messages: LLMMessage[],
): OAIMessage[] {
  const result: OAIMessage[] = [];

  // System message
  if (system) {
    const systemText =
      typeof system === "string"
        ? system
        : system.map((b) => b.text).join("\n");
    result.push({ role: "system", content: systemText });
  }

  for (const msg of messages) {
    if (typeof msg.content === "string") {
      result.push({ role: msg.role, content: msg.content });
      continue;
    }

    // Complex content blocks — need to split into OpenAI shapes
    const blocks = msg.content;

    if (msg.role === "user") {
      // User messages: concatenate text blocks, handle tool_result as tool messages
      const textParts: string[] = [];
      const toolResults: Array<{ tool_use_id: string; content: string }> = [];

      for (const block of blocks) {
        if (block.type === "text") {
          textParts.push(block.text);
        } else if (block.type === "tool_result") {
          const content =
            typeof block.content === "string"
              ? block.content
              : block.content.map((c) => c.text).join("\n");
          toolResults.push({ tool_use_id: block.tool_use_id, content });
        } else if (block.type === "image") {
          // OpenAI vision: embed as data URL in text (simplified)
          textParts.push(`[image: ${block.source.media_type}]`);
        }
      }

      // Emit tool result messages first (OpenAI requires role=tool)
      for (const tr of toolResults) {
        result.push({
          role: "tool",
          tool_call_id: tr.tool_use_id,
          content: tr.content,
        });
      }

      // Then the user text (if any)
      if (textParts.length > 0) {
        result.push({ role: "user", content: textParts.join("\n") });
      }
    } else {
      // Assistant messages: text + tool_use blocks
      const textParts: string[] = [];
      const toolCalls: OAIToolCall[] = [];

      for (const block of blocks) {
        if (block.type === "text") {
          textParts.push(block.text);
        } else if (block.type === "tool_use") {
          toolCalls.push({
            id: block.id,
            type: "function",
            function: {
              name: block.name,
              arguments:
                typeof block.input === "string"
                  ? block.input
                  : JSON.stringify(block.input),
            },
          });
        }
        // thinking blocks are Anthropic-specific; omit for OpenAI
      }

      const assistantMsg: OAIMessage = {
        role: "assistant",
        content: textParts.length > 0 ? textParts.join("") : null,
      };
      if (toolCalls.length > 0) {
        assistantMsg.tool_calls = toolCalls;
      }
      result.push(assistantMsg);
    }
  }

  return result;
}

/**
 * Convert Anthropic tool definitions to OpenAI function-calling format.
 */
function convertTools(tools: LLMToolDef[]): OAITool[] {
  return tools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.input_schema,
    },
  }));
}

/**
 * Map OpenAI finish_reason to our canonical stop reason.
 */
function mapFinishReason(
  reason: string | null,
): "end_turn" | "tool_use" | "max_tokens" | "stop_sequence" | "refusal" | "pause_turn" | null {
  switch (reason) {
    case "stop":
      return "end_turn";
    case "tool_calls":
      return "tool_use";
    case "length":
      return "max_tokens";
    case "content_filter":
      return "refusal";
    default:
      return reason as ReturnType<typeof mapFinishReason> ?? null;
  }
}
