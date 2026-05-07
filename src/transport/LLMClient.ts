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
import { StringDecoder } from "node:string_decoder";
import { URL } from "node:url";
import type { ImageContentBlock } from "../util/types.js";
import { shouldEnableThinkingByDefault, getCapability } from "../llm/modelCapabilities.js";

export interface LLMClientOptions {
  apiProxyUrl: string; // e.g. http://api-proxy.magi-system.svc.cluster.local:3001
  gatewayToken: string; // used as x-api-key to api-proxy
  codexAccessToken?: string;
  codexRefreshToken?: string;
  defaultModel: string;
  anthropicVersion?: string; // default 2023-06-01
  timeoutMs?: number; // default 600_000
}

export type LLMRole = "user" | "assistant";

export type LLMContentBlock =
  | { type: "text"; text: string }
  | ImageContentBlock
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
  /** Cooperative cancellation for user interrupts / handoff. */
  signal?: AbortSignal;
  /** Non-authoritative routing hints for observability at the proxy boundary. */
  routing?: {
    profileId: string;
    tier: string;
    provider: string;
    confidence: string;
  };
}

export interface LLMUsage {
  inputTokens: number;
  outputTokens: number;
}

export interface ProviderHealthContext {
  provider: string;
  model: string;
  state: "ok" | "watch" | "degraded" | "outage" | "unknown";
  confidence: "low" | "medium" | "high";
  summary: string;
  routeReason: string;
}

const ANTHROPIC_TOOL_ID_RE = /^[a-zA-Z0-9_-]+$/;

function abortReason(signal: AbortSignal): Error {
  const reason = (signal as AbortSignal & { reason?: unknown }).reason;
  return reason instanceof Error ? reason : new Error("llm_stream_aborted");
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortReason(signal);
}

function sanitizeToolUseId(raw: string): string {
  if (ANTHROPIC_TOOL_ID_RE.test(raw)) return raw;
  const sanitized = raw.replace(/[^a-zA-Z0-9_-]/g, "_");
  return sanitized.length > 0 ? sanitized : "toolu";
}

export function normalizeToolUseIdsForRequest(messages: LLMMessage[]): LLMMessage[] {
  const pendingToolUseIds = new Map<string, string[]>();
  const used = new Set<string>();

  const nextUniqueId = (raw: string): string => {
    const base = sanitizeToolUseId(raw);
    let candidate = base;
    let suffix = 1;
    while (used.has(candidate)) {
      candidate = `${base}_${suffix}`;
      suffix += 1;
    }
    used.add(candidate);
    return candidate;
  };

  const recordToolUse = (raw: string): string => {
    const id = nextUniqueId(raw);
    const pending = pendingToolUseIds.get(raw) ?? [];
    pending.push(id);
    pendingToolUseIds.set(raw, pending);
    return id;
  };

  const consumeToolResult = (raw: string): string => {
    const pending = pendingToolUseIds.get(raw);
    if (pending && pending.length > 0) {
      const id = pending.shift()!;
      if (pending.length === 0) pendingToolUseIds.delete(raw);
      return id;
    }
    return nextUniqueId(raw);
  };

  return messages.map((msg) => {
    if (!Array.isArray(msg.content)) return msg;
    return {
      ...msg,
      content: msg.content.map((block) => {
        if (block.type === "tool_use") {
          return {
            ...block,
            id: recordToolUse(block.id),
          };
        }
        if (block.type === "tool_result") {
          return {
            ...block,
            tool_use_id: consumeToolResult(block.tool_use_id),
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
  private runtimeModelWarningEmitted = false;
  private lastProviderHealth: ProviderHealthContext | null = null;

  constructor(options: LLMClientOptions, provider?: ProviderLike) {
    this.opts = {
      anthropicVersion: "2023-06-01",
      timeoutMs: 600_000,
      ...options,
    };
    this.providerOverride = provider;
  }

  static fromProvider(provider: ProviderLike, defaultModel: string): LLMClient {
    return new LLMClient(
      { apiProxyUrl: "unused://provider-mode", gatewayToken: "unused", defaultModel },
      provider,
    );
  }

  async resolveRuntimeModel(fallbackModel = this.opts.defaultModel): Promise<string> {
    try {
      const url = new URL("/v1/bot-model", this.opts.apiProxyUrl);
      const lib = url.protocol === "https:" ? https : http;
      const reqOptions: http.RequestOptions = {
        method: "GET",
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        headers: {
          "x-api-key": this.opts.gatewayToken,
          Accept: "application/json",
        },
        timeout: Math.min(this.opts.timeoutMs, 5_000),
      };

      const body = await new Promise<string>((resolve, reject) => {
        const r = lib.request(reqOptions, (incoming) => {
          let data = "";
          incoming.setEncoding("utf8");
          incoming.on("data", (chunk) => {
            data += chunk;
          });
          incoming.on("end", () => {
            if ((incoming.statusCode ?? 500) < 200 || (incoming.statusCode ?? 500) >= 300) {
              reject(new Error(`bot model lookup returned ${incoming.statusCode}`));
              return;
            }
            resolve(data);
          });
        });
        r.on("timeout", () => {
          r.destroy(new Error("bot model lookup timeout"));
        });
        r.on("error", reject);
        r.end();
      });

      const parsed = JSON.parse(body) as { runtimeModel?: unknown };
      if (typeof parsed.runtimeModel === "string" && parsed.runtimeModel.trim().length > 0) {
        return parsed.runtimeModel.trim();
      }
    } catch (err) {
      if (!this.runtimeModelWarningEmitted) {
        const message = err instanceof Error ? err.message : String(err);
        console.warn(`[core-agent] dynamic model lookup failed; using provisioned model: ${message}`);
        this.runtimeModelWarningEmitted = true;
      }
    }
    return fallbackModel;
  }

  getLastProviderHealth(): ProviderHealthContext | null {
    return this.lastProviderHealth;
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
    throwIfAborted(req.signal);
    const model = req.model ?? this.opts.defaultModel;
    const usesCodexOAuth = model.startsWith("openai-codex/");
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
    const normalizedMessages = normalizeToolUseIdsForRequest(req.messages).map((msg) => {
      if (!Array.isArray(msg.content)) return msg;
      const filtered = (msg.content as Array<Record<string, unknown>>).filter(
        (block) => block.type !== "text" || (typeof block.text === "string" && block.text.length > 0),
      );
      return filtered.length > 0 ? { ...msg, content: filtered } : msg;
    });
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
    const routingHeaders = req.routing
      ? {
          "x-magi-router-profile": req.routing.profileId,
          "x-magi-router-tier": req.routing.tier,
          "x-magi-router-provider": req.routing.provider,
          "x-magi-router-confidence": req.routing.confidence,
        }
      : {};
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
        ...(usesCodexOAuth && this.opts.codexAccessToken
          ? { "x-openai-codex-access-token": this.opts.codexAccessToken }
          : {}),
        ...(usesCodexOAuth && this.opts.codexRefreshToken
          ? { "x-openai-codex-refresh-token": this.opts.codexRefreshToken }
          : {}),
        ...routingHeaders,
      },
      timeout: this.opts.timeoutMs,
    };

    const res = await new Promise<http.IncomingMessage>((resolve, reject) => {
      let onAbort: (() => void) | null = null;
      const r = lib.request(reqOptions, (incoming) => {
        if (req.signal && onAbort !== null) {
          req.signal.removeEventListener("abort", onAbort);
        }
        resolve(incoming);
      });
      onAbort = (): void => {
        const err = abortReason(req.signal!);
        r.destroy(err);
        reject(err);
      };
      if (req.signal) {
        if (req.signal.aborted) {
          onAbort();
          return;
        }
        req.signal.addEventListener("abort", onAbort, { once: true });
      }
      r.on("error", (err) => {
        if (req.signal && onAbort !== null) {
          req.signal.removeEventListener("abort", onAbort);
        }
        reject(err);
      });
      r.on("timeout", () => r.destroy(new Error("api-proxy timeout")));
      r.write(body);
      r.end();
    });

    this.lastProviderHealth = parseProviderHealthHeaders(res.headers);

    if (res.statusCode && res.statusCode >= 400) {
      const errBody = await consumeText(res, req.signal);
      yield {
        kind: "error",
        code: `http_${res.statusCode}`,
        message: errBody.slice(0, 500) || `upstream ${res.statusCode}`,
      };
      return;
    }

    const onResponseAbort = (): void => {
      res.destroy(abortReason(req.signal!));
    };
    if (req.signal) {
      if (req.signal.aborted) throw abortReason(req.signal);
      req.signal.addEventListener("abort", onResponseAbort, { once: true });
    }
    try {
      for await (const evt of parseAnthropicSse(res)) {
        throwIfAborted(req.signal);
        yield evt;
      }
    } finally {
      if (req.signal) {
        req.signal.removeEventListener("abort", onResponseAbort);
      }
    }
  }
}

function parseProviderHealthHeaders(headers: http.IncomingHttpHeaders): ProviderHealthContext | null {
  const provider = headerValue(headers, "x-magi-provider-health-provider");
  const state = headerValue(headers, "x-magi-provider-health-state");
  if (!provider || !state) return null;
  return {
    provider,
    model: headerValue(headers, "x-magi-provider-health-model"),
    state: normalizeProviderHealthState(state),
    confidence: normalizeProviderHealthConfidence(headerValue(headers, "x-magi-provider-health-confidence")),
    summary: headerValue(headers, "x-magi-provider-health-summary"),
    routeReason: headerValue(headers, "x-magi-provider-health-route") || "primary",
  };
}

function headerValue(headers: http.IncomingHttpHeaders, name: string): string {
  const raw = headers[name.toLowerCase()];
  const value = Array.isArray(raw) ? raw[0] : raw;
  return typeof value === "string" ? value : "";
}

function normalizeProviderHealthState(value: string): ProviderHealthContext["state"] {
  if (value === "ok" || value === "watch" || value === "degraded" || value === "outage" || value === "unknown") {
    return value;
  }
  return "unknown";
}

function normalizeProviderHealthConfidence(value: string): ProviderHealthContext["confidence"] {
  if (value === "low" || value === "medium" || value === "high") return value;
  return "low";
}

/**
 * Consume the full body as text. Used on error paths.
 */
async function consumeText(res: http.IncomingMessage, signal?: AbortSignal): Promise<string> {
  const onAbort = (): void => {
    res.destroy(abortReason(signal!));
  };
  if (signal) {
    throwIfAborted(signal);
    signal.addEventListener("abort", onAbort, { once: true });
  }
  const chunks: Buffer[] = [];
  try {
    for await (const chunk of res) {
      throwIfAborted(signal);
      chunks.push(chunk as Buffer);
    }
    throwIfAborted(signal);
    return Buffer.concat(chunks).toString("utf8");
  } finally {
    if (signal) {
      signal.removeEventListener("abort", onAbort);
    }
  }
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
export async function* parseAnthropicSse(
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
  const decoder = new StringDecoder("utf8");

  for await (const chunk of res) {
    buffer += decoder.write(chunk as Buffer);
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

  buffer += decoder.end();

  // Fallthrough — stream closed without message_stop
  yield { kind: "message_end", stopReason: stopReason ?? null, usage };
}
