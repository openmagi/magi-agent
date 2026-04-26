/**
 * LLMClient — thin Anthropic-streaming client pointed at api-proxy.
 * Design reference: §5.3, §9.2.
 *
 * Phase 1b: Anthropic `/v1/messages` streaming with tool_use content
 * blocks. Parses the SSE wire format into a normalised event stream
 * consumed by Turn.execute's agent loop.
 *
 * We do NOT use the official SDK — a zero-dep fetcher + SSE parser
 * stays honest about exactly what flows over the wire and what the
 * loop depends on.
 */

import http from "node:http";
import https from "node:https";
import { URL } from "node:url";
import { shouldEnableThinkingByDefault, getCapability } from "../llm/modelCapabilities.js";

export interface LLMClientOptions {
  apiProxyUrl: string; // e.g. http://api-proxy.clawy-system.svc.cluster.local:3001
  gatewayToken: string; // used as x-api-key to api-proxy
  defaultModel: string;
  anthropicVersion?: string; // default 2023-06-01
  timeoutMs?: number; // default 600_000
}

export type LLMRole = "user" | "assistant";

export type LLMContentBlock =
  | { type: "text"; text: string }
  | {
      type: "image";
      source: {
        type: "base64";
        media_type: "image/jpeg" | "image/png" | "image/gif" | "image/webp";
        data: string;
      };
    }
  | { type: "thinking"; thinking: string; signature: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | {
      type: "tool_result";
      tool_use_id: string;
      content: string | Array<{ type: "text"; text: string }>;
      is_error?: boolean;
    };

export interface LLMMessage {
  role: LLMRole;
  content: string | LLMContentBlock[];
}

export interface LLMToolDef {
  name: string;
  description: string;
  input_schema: object;
}

export interface LLMStreamRequest {
  model?: string;
  system?: string | Array<{ type: "text"; text: string }>;
  messages: LLMMessage[];
  tools?: LLMToolDef[];
  max_tokens?: number;
  temperature?: number;
  /** Adaptive thinking for opus-4-7 etc. Pass `{ type: "adaptive" }` to enable. */
  thinking?: { type: "adaptive" } | { type: "disabled" };
}

export interface LLMUsage {
  inputTokens: number;
  outputTokens: number;
}

const ANTHROPIC_TOOL_ID_RE = /^[a-zA-Z0-9_-]+$/;

function sanitizeToolUseId(raw: string): string {
  if (ANTHROPIC_TOOL_ID_RE.test(raw)) return raw;
  const sanitized = raw.replace(/[^a-zA-Z0-9_-]/g, "_");
  return sanitized.length > 0 ? sanitized : "toolu";
}

/**
 * Anthropic rejects tool_use ids that contain punctuation outside
 * `[a-zA-Z0-9_-]`. Some upstream providers and synthetic paths can emit
 * looser ids (for example `call.one` or `toolu:...`). Normalize every
 * tool_use / tool_result id pair just before the request leaves the
 * core-agent boundary so historical transcript quirks cannot 400 a new turn.
 */
export function normalizeToolUseIdsForRequest(messages: LLMMessage[]): LLMMessage[] {
  const idMap = new Map<string, string>();
  const used = new Set<string>();

  const canonicalId = (raw: string): string => {
    const existing = idMap.get(raw);
    if (existing) return existing;

    const base = sanitizeToolUseId(raw);
    let candidate = base;
    let suffix = 1;
    while (used.has(candidate)) {
      candidate = `${base}_${suffix}`;
      suffix += 1;
    }
    idMap.set(raw, candidate);
    used.add(candidate);
    return candidate;
  };

  return messages.map((msg) => {
    if (!Array.isArray(msg.content)) return msg;
    return {
      ...msg,
      content: msg.content.map((block) => {
        if (block.type === "tool_use") {
          return {
            ...block,
            id: canonicalId(block.id),
          };
        }
        if (block.type === "tool_result") {
          return {
            ...block,
            tool_use_id: canonicalId(block.tool_use_id),
          };
        }
        return block;
      }),
    };
  });
}

export type LLMEvent =
  /** Accumulating text block. */
  | { kind: "text_delta"; blockIndex: number; delta: string }
  /** Accumulating thinking block (Anthropic extended thinking). */
  | { kind: "thinking_delta"; blockIndex: number; delta: string }
  /** Thinking block signature (required for replay in subsequent API calls). */
  | { kind: "thinking_signature"; blockIndex: number; signature: string }
  /** Tool use block announced — id + name known, input being streamed in chunks. */
  | { kind: "tool_use_start"; blockIndex: number; id: string; name: string }
  /** input_json delta (partial JSON fragment). */
  | { kind: "tool_use_input_delta"; blockIndex: number; partial: string }
  /** Content block ended; final input is available for tool_use blocks. */
  | { kind: "block_stop"; blockIndex: number }
  /** Whole message ended. */
  | {
      kind: "message_end";
      stopReason:
        | "end_turn"
        | "tool_use"
        | "max_tokens"
        | "stop_sequence"
        | "refusal"
        | "pause_turn"
        | null;
      usage: LLMUsage;
    }
  | { kind: "error"; code: string; message: string };

type ProviderLike = { stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> };

export class LLMClient {
  private readonly opts: Required<Pick<LLMClientOptions, "anthropicVersion" | "timeoutMs">> &
    LLMClientOptions;
  private readonly providerOverride?: ProviderLike;

  constructor(options: LLMClientOptions, provider?: ProviderLike) {
    this.opts = {
      anthropicVersion: "2023-06-01",
      timeoutMs: 600_000,
      ...options,
    };
    this.providerOverride = provider;
  }

  /**
   * Create an LLMClient backed by an external LLMProvider (OSS multi-provider).
   * The provider's `stream()` replaces the built-in Anthropic api-proxy call.
   */
  static fromProvider(provider: ProviderLike, defaultModel: string): LLMClient {
    return new LLMClient(
      { apiProxyUrl: "unused://provider-mode", gatewayToken: "unused", defaultModel },
      provider,
    );
  }

  /**
   * Stream a single `/v1/messages` call. Yields normalised LLMEvents
   * until the upstream server closes the stream or errors.
   *
   * The caller is responsible for accumulating tool_use input fragments
   * (via `tool_use_input_delta`) and materialising the final structured
   * input when `block_stop` arrives for that block.
   */
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    if (this.providerOverride) {
      yield* this.providerOverride.stream(req);
      return;
    }
    const model = req.model ?? this.opts.defaultModel;
    // T4-17: gate thinking on model capability. If the caller passed
    // an explicit thinking directive (adaptive or disabled) respect
    // it; otherwise default to adaptive for models that support it
    // and omit the field entirely for models that don't (e.g. Haiku),
    // so we never send `thinking` to a model that may 400 on it.
    const thinking =
      req.thinking ??
      (shouldEnableThinkingByDefault(model)
        ? ({ type: "adaptive" } as const)
        : undefined);
    const normalizedMessages = normalizeToolUseIdsForRequest(req.messages);
    const body = JSON.stringify({
      model,
      system: req.system,
      messages: normalizedMessages,
      tools: req.tools,
      max_tokens: req.max_tokens ?? (thinking ? (getCapability(model)?.maxOutputTokens ?? 16_000) : 4096),
      temperature: req.temperature,
      ...(thinking ? { thinking } : {}),
      stream: true,
    });

    const url = new URL("/v1/messages", this.opts.apiProxyUrl);
    const lib = url.protocol === "https:" ? https : http;
    const reqOptions: http.RequestOptions = {
      method: "POST",
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || (url.protocol === "https:" ? 443 : 80),
      path: url.pathname + url.search,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        "x-api-key": this.opts.gatewayToken,
        "anthropic-version": this.opts.anthropicVersion,
        Accept: "text/event-stream",
      },
      timeout: this.opts.timeoutMs,
    };

    const res = await new Promise<http.IncomingMessage>((resolve, reject) => {
      const r = lib.request(reqOptions, resolve);
      r.on("error", reject);
      r.on("timeout", () => r.destroy(new Error("api-proxy timeout")));
      r.write(body);
      r.end();
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

    for await (const evt of parseAnthropicSse(res)) yield evt;
  }
}

/**
 * Consume the full body as text. Used on error paths.
 */
async function consumeText(res: http.IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of res) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Parse an Anthropic `/v1/messages` SSE stream into normalised LLMEvents.
 *
 * Anthropic SSE frame shape:
 *   event: message_start
 *   data: {...}
 *
 *   event: content_block_start
 *   data: { "index": 0, "content_block": {...} }
 *
 *   event: content_block_delta
 *   data: { "index": 0, "delta": { "type": "text_delta", "text": "..." } }
 *
 *   event: content_block_stop
 *   data: { "index": 0 }
 *
 *   event: message_delta
 *   data: { "delta": { "stop_reason": "end_turn" }, "usage": { "output_tokens": N } }
 *
 *   event: message_stop
 */
async function* parseAnthropicSse(
  res: http.IncomingMessage,
): AsyncGenerator<LLMEvent, void, void> {
  let buffer = "";
  let currentEvent = "";
  type StopReason =
    | "end_turn"
    | "tool_use"
    | "max_tokens"
    | "stop_sequence"
    | "refusal"
    | "pause_turn"
    | null;
  let stopReason: StopReason = null;
  let usage: LLMUsage = { inputTokens: 0, outputTokens: 0 };

  for await (const chunk of res) {
    buffer += (chunk as Buffer).toString("utf8");
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line === "") {
        currentEvent = "";
        continue;
      }
      if (line.startsWith(":")) continue; // SSE comment
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
        continue;
      }
      if (!line.startsWith("data:")) continue;
      const dataStr = line.slice(5).trim();
      if (!dataStr || dataStr === "[DONE]") continue;

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(dataStr);
      } catch {
        continue;
      }

      switch (currentEvent) {
        case "message_start": {
          const msg = (payload as { message?: { usage?: Partial<LLMUsage> } }).message;
          const u = msg?.usage;
          if (u) {
            usage = {
              inputTokens:
                (u as { input_tokens?: number }).input_tokens ?? usage.inputTokens,
              outputTokens:
                (u as { output_tokens?: number }).output_tokens ?? usage.outputTokens,
            };
          }
          break;
        }
        case "content_block_start": {
          const idx = (payload as { index?: number }).index ?? 0;
          const block = (payload as { content_block?: { type?: string; id?: string; name?: string } })
            .content_block;
          if (block?.type === "tool_use" && block.id && block.name) {
            yield {
              kind: "tool_use_start",
              blockIndex: idx,
              id: block.id,
              name: block.name,
            };
          }
          // text / thinking blocks emit their content via *_delta events
          break;
        }
        case "content_block_delta": {
          const idx = (payload as { index?: number }).index ?? 0;
          const delta = (payload as { delta?: Record<string, unknown> }).delta;
          if (!delta) break;
          const t = (delta as { type?: string }).type;
          if (t === "text_delta") {
            const text = (delta as { text?: string }).text ?? "";
            if (text) yield { kind: "text_delta", blockIndex: idx, delta: text };
          } else if (t === "thinking_delta") {
            const text = (delta as { thinking?: string }).thinking ?? "";
            if (text) yield { kind: "thinking_delta", blockIndex: idx, delta: text };
          } else if (t === "signature_delta") {
            // Extended-thinking signature — Anthropic delivers the full
            // signature as a single signature_delta at the end of the
            // thinking block. MUST be preserved byte-identical for replay
            // in subsequent assistant messages.
            const sig = (delta as { signature?: string }).signature ?? "";
            if (sig) yield { kind: "thinking_signature", blockIndex: idx, signature: sig };
          } else if (t === "input_json_delta") {
            const partial = (delta as { partial_json?: string }).partial_json ?? "";
            yield { kind: "tool_use_input_delta", blockIndex: idx, partial };
          }
          break;
        }
        case "content_block_stop": {
          const idx = (payload as { index?: number }).index ?? 0;
          yield { kind: "block_stop", blockIndex: idx };
          break;
        }
        case "message_delta": {
          const delta = (payload as { delta?: { stop_reason?: string } }).delta;
          if (delta?.stop_reason) {
            stopReason = delta.stop_reason as StopReason;
          }
          const u = (payload as { usage?: { output_tokens?: number; input_tokens?: number } })
            .usage;
          if (u) {
            if (typeof u.input_tokens === "number") usage.inputTokens = u.input_tokens;
            if (typeof u.output_tokens === "number") usage.outputTokens = u.output_tokens;
          }
          break;
        }
        case "message_stop": {
          yield { kind: "message_end", stopReason: stopReason ?? "end_turn", usage };
          return;
        }
        case "error": {
          const err = (payload as { error?: { type?: string; message?: string } }).error;
          yield {
            kind: "error",
            code: err?.type ?? "upstream_error",
            message: err?.message ?? "unknown",
          };
          return;
        }
        default:
          break;
      }
    }
  }

  // Fallthrough — stream closed without message_stop
  yield { kind: "message_end", stopReason: stopReason ?? null, usage };
}
