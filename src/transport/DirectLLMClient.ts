import http from "node:http";
import https from "node:https";
import { StringDecoder } from "node:string_decoder";
import { URL } from "node:url";
import {
  LLMClient,
  parseAnthropicSse,
  type LLMEvent,
  type LLMMessage,
  type LLMStreamRequest,
} from "./LLMClient.js";

export interface DirectProviderConfig {
  kind: "anthropic" | "openai-compatible";
  baseUrl: string;
  apiKey?: string;
}

export interface DirectLLMClientOptions {
  providers: Record<string, DirectProviderConfig>;
  timeoutMs?: number;
}

export class DirectLLMClient extends LLMClient {
  private readonly providers: Record<string, DirectProviderConfig>;
  private readonly directTimeoutMs: number;

  constructor(options: DirectLLMClientOptions) {
    super({
      apiProxyUrl: "http://127.0.0.1",
      gatewayToken: "direct",
      defaultModel: "direct",
      timeoutMs: options.timeoutMs,
    });
    this.providers = options.providers;
    this.directTimeoutMs = options.timeoutMs ?? 600_000;
  }

  override async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    const model = req.model ?? "";
    const { key, provider } = this.resolveProvider(model);
    if (provider.kind === "anthropic") {
      yield* this.streamAnthropic(provider, req);
      return;
    }
    yield* this.streamOpenAICompatible(key, provider, req);
  }

  private resolveProvider(model: string): { key: string; provider: DirectProviderConfig } {
    const key = model.startsWith("claude-")
      ? "anthropic"
      : model.startsWith("kimi-") || model.startsWith("minimax-")
        ? "fireworks"
        : model.startsWith("gemini-")
          ? "google"
          : "openai";
    const provider = this.providers[key];
    if (!provider) {
      throw new Error(`Missing direct LLM provider config: ${key}`);
    }
    return { key, provider };
  }

  private async *streamAnthropic(
    provider: DirectProviderConfig,
    req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    const body = JSON.stringify({
      model: req.model,
      system: req.system,
      messages: req.messages,
      tools: req.tools,
      max_tokens: req.max_tokens ?? 4096,
      temperature: req.temperature,
      ...(req.thinking ? { thinking: req.thinking } : {}),
      stream: true,
    });
    const headers = {
      ...(provider.apiKey ? { "x-api-key": provider.apiKey } : {}),
      "anthropic-version": "2023-06-01",
      Accept: "text/event-stream",
      ...(req.thinking && req.thinking.type !== "disabled"
        ? { "anthropic-beta": "interleaved-thinking-2025-05-14" }
        : {}),
    };
    const res = await post(provider, "/v1/messages", body, headers, this.directTimeoutMs);

    if (res.statusCode && res.statusCode >= 400) {
      yield { kind: "error", code: `http_${res.statusCode}`, message: await consumeText(res) };
      return;
    }

    for await (const evt of parseAnthropicSse(res)) yield evt;
  }

  private async *streamOpenAICompatible(
    key: string,
    provider: DirectProviderConfig,
    req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    const body = JSON.stringify({
      model: normalizeOpenAICompatibleModel(key, req.model ?? ""),
      messages: toOpenAIMessages(req.system, req.messages),
      tools: req.tools?.map((tool) => ({
        type: "function",
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.input_schema,
        },
      })),
      max_tokens: req.max_tokens,
      temperature: req.temperature,
      stream: true,
      stream_options: { include_usage: true },
    });
    const res = await post(provider, openAICompatiblePath(key, provider.baseUrl), body, {
      ...(provider.apiKey ? { Authorization: `Bearer ${provider.apiKey}` } : {}),
      Accept: "text/event-stream",
    }, this.directTimeoutMs);

    if (res.statusCode && res.statusCode >= 400) {
      yield { kind: "error", code: `http_${res.statusCode}`, message: await consumeText(res) };
      return;
    }

    yield* parseOpenAISse(res);
  }
}

function normalizeOpenAICompatibleModel(providerKey: string, model: string): string {
  if (providerKey === "fireworks" && !model.startsWith("accounts/")) {
    return `accounts/fireworks/models/${model}`;
  }
  return model;
}

function openAICompatiblePath(providerKey: string, baseUrl: string): string {
  const basePath = new URL(baseUrl).pathname.replace(/\/$/, "");
  if (basePath.endsWith("/v1") || basePath.endsWith("/openai")) {
    return "/chat/completions";
  }
  if (providerKey === "google") {
    return "/chat/completions";
  }
  return "/v1/chat/completions";
}

function post(
  provider: DirectProviderConfig,
  pathname: string,
  body: string,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<http.IncomingMessage> {
  const url = endpointUrl(provider.baseUrl, pathname);
  const lib = url.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const r = lib.request({
      method: "POST",
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || (url.protocol === "https:" ? 443 : 80),
      path: url.pathname + url.search,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        ...headers,
      },
      timeout: timeoutMs,
    }, resolve);
    r.on("error", reject);
    r.on("timeout", () => r.destroy(new Error("direct llm timeout")));
    r.write(body);
    r.end();
  });
}

function endpointUrl(baseUrl: string, pathname: string): URL {
  const url = new URL(baseUrl);
  url.pathname = `${url.pathname.replace(/\/$/, "")}${pathname}`;
  return url;
}

function toOpenAIMessages(
  system: LLMStreamRequest["system"],
  messages: LLMMessage[],
): Array<Record<string, unknown>> {
  const out: Array<Record<string, unknown>> = [];
  const systemText = systemToText(system);
  if (systemText) out.push({ role: "system", content: systemText });

  for (const msg of messages) {
    if (typeof msg.content === "string") {
      out.push({ role: msg.role, content: msg.content });
      continue;
    }

    const text = textBlocksToString(msg.content);
    const toolUses = msg.content.filter((block) => block.type === "tool_use");
    if (msg.role === "assistant" && toolUses.length > 0) {
      out.push({
        role: "assistant",
        content: text || null,
        tool_calls: toolUses.map((block) => ({
          id: block.id,
          type: "function",
          function: {
            name: block.name,
            arguments: JSON.stringify(block.input ?? {}),
          },
        })),
      });
      continue;
    }

    const toolResults = msg.content.filter((block) => block.type === "tool_result");
    if (msg.role === "user" && toolResults.length > 0) {
      if (text) out.push({ role: "user", content: text });
      for (const block of toolResults) {
        out.push({
          role: "tool",
          tool_call_id: block.tool_use_id,
          content: toolResultToText(block.content),
        });
      }
      continue;
    }

    out.push({ role: msg.role, content: text });
  }

  return out;
}

function systemToText(system: LLMStreamRequest["system"]): string {
  if (!system) return "";
  if (typeof system === "string") return system;
  return system
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

function textBlocksToString(blocks: Exclude<LLMMessage["content"], string>): string {
  return blocks
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

function toolResultToText(
  content: string | Array<{ type: "text"; text: string }>,
): string {
  if (typeof content === "string") return content;
  return content.map((block) => block.text).join("\n");
}

async function* parseOpenAISse(res: http.IncomingMessage): AsyncGenerator<LLMEvent, void, void> {
  let buffer = "";
  let inputTokens = 0;
  let outputTokens = 0;
  let pendingStopReason: "end_turn" | "tool_use" | "max_tokens" | null = null;
  let ended = false;
  let nextBlockIndex = 1;
  const toolCalls = new Map<number, {
    blockIndex: number;
    id: string;
    name: string;
    started: boolean;
    pendingArgs: string;
  }>();
  const decoder = new StringDecoder("utf8");

  for await (const chunk of res) {
    buffer += decoder.write(chunk as Buffer);
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const data = line.slice(6).trim();
      if (!data) continue;
      if (data === "[DONE]") {
        if (pendingStopReason && !ended) {
          yield {
            kind: "message_end",
            stopReason: pendingStopReason,
            usage: { inputTokens, outputTokens },
          };
          ended = true;
        }
        continue;
      }

      let evt: OpenAIStreamEvent;
      try {
        evt = JSON.parse(data) as OpenAIStreamEvent;
      } catch {
        continue;
      }

      const delta = evt.choices?.[0]?.delta;
      if (typeof delta?.content === "string" && delta.content.length > 0) {
        yield { kind: "text_delta", blockIndex: 0, delta: delta.content };
      }
      for (const call of delta?.tool_calls ?? []) {
        const index = call.index ?? 0;
        let state = toolCalls.get(index);
        if (!state) {
          state = {
            blockIndex: nextBlockIndex,
            id: "",
            name: "",
            started: false,
            pendingArgs: "",
          };
          nextBlockIndex += 1;
          toolCalls.set(index, state);
        }
        if (call.id) state.id = call.id;
        if (call.function?.name) state.name = call.function.name;
        if (call.function?.arguments) state.pendingArgs += call.function.arguments;
        if (!state.started && state.name) {
          state.started = true;
          yield {
            kind: "tool_use_start",
            blockIndex: state.blockIndex,
            id: state.id || `call_${index}`,
            name: state.name,
          };
        }
        if (state.started && state.pendingArgs) {
          yield {
            kind: "tool_use_input_delta",
            blockIndex: state.blockIndex,
            partial: state.pendingArgs,
          };
          state.pendingArgs = "";
        }
      }
      if (evt.usage) {
        inputTokens = evt.usage.prompt_tokens ?? inputTokens;
        outputTokens = evt.usage.completion_tokens ?? outputTokens;
      }
      const finish = evt.choices?.[0]?.finish_reason;
      if (finish) {
        pendingStopReason = finishToStopReason(finish);
        if (pendingStopReason === "tool_use") {
          for (const state of toolCalls.values()) {
            if (state.started) {
              yield { kind: "block_stop", blockIndex: state.blockIndex };
            }
          }
        }
      }
    }
  }

  buffer += decoder.end();

  if (pendingStopReason && !ended) {
    yield {
      kind: "message_end",
      stopReason: pendingStopReason,
      usage: { inputTokens, outputTokens },
    };
  }
}

function finishToStopReason(
  finish: string,
): "end_turn" | "tool_use" | "max_tokens" {
  if (finish === "length") return "max_tokens";
  if (finish === "tool_calls" || finish === "function_call") return "tool_use";
  return "end_turn";
}

interface OpenAIStreamEvent {
  choices?: Array<{
    delta?: {
      content?: string;
      tool_calls?: Array<{
        index?: number;
        id?: string;
        type?: string;
        function?: { name?: string; arguments?: string };
      }>;
    };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
  };
}

async function consumeText(res: http.IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of res) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8").slice(0, 500);
}
