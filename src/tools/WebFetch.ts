import { createHash } from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import {
  defaultResolvePublicFetchHost,
  validatePublicFetchUrl,
  type PublicFetchHostAddress,
  type PublicFetchHostResolver,
} from "../util/publicFetchUrl.js";

export type WebFetchFormat = "markdown" | "text" | "html";

export interface WebFetchInput {
  url?: string;
  format?: WebFetchFormat;
  timeoutMs?: number;
}

export interface WebFetchRunInput {
  url: string;
  format: WebFetchFormat;
}

export interface WebFetchRunResult {
  statusCode: number;
  url: string;
  finalUrl?: string;
  contentType?: string;
  body: string;
  truncated: boolean;
}

export interface WebFetchOutput {
  url: string;
  finalUrl: string;
  statusCode: number;
  contentType?: string;
  format: WebFetchFormat;
  title?: string;
  content: string;
  truncated: boolean;
  sourceId?: string;
}

export type WebFetchRunner = (
  input: WebFetchRunInput,
  ctx: ToolContext,
  timeoutMs: number,
) => Promise<WebFetchRunResult>;

export type WebFetchHostAddress = PublicFetchHostAddress;

export type WebFetchHostResolver = PublicFetchHostResolver;

interface WebFetchToolOptions {
  runner?: WebFetchRunner;
  resolveHost?: WebFetchHostResolver;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    url: {
      type: "string",
      description: "Public HTTP(S) URL to fetch for source-sensitive research.",
    },
    format: {
      type: "string",
      enum: ["markdown", "text", "html"],
      description: "Returned content format. Defaults to markdown.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      maximum: 120000,
      description: "Timeout in ms. Defaults to 30000.",
    },
  },
  required: ["url"],
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_BODY_BYTES = 1024 * 1024;

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function normalizeTimeout(timeoutMs: unknown): number {
  if (typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs)) return DEFAULT_TIMEOUT_MS;
  return Math.max(100, Math.min(MAX_TIMEOUT_MS, Math.trunc(timeoutMs)));
}

export function normalizeWebFetchFormat(format: unknown): WebFetchFormat {
  return format === "text" || format === "html" || format === "markdown" ? format : "markdown";
}

export function validateWebFetchInput(input: WebFetchInput): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "`input` must be an object";
  }
  if (!stringValue(input.url)) return "`url` is required for web fetch";
  if (input.format !== undefined && !["markdown", "text", "html"].includes(String(input.format))) {
    return "`format` must be markdown, text, or html";
  }
  return null;
}

function contentHash(content: string): string {
  return `sha256:${createHash("sha256").update(content).digest("hex")}`;
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, "\"")
    .replace(/&#39;/g, "'");
}

function extractTitle(html: string): string | undefined {
  const title = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1];
  const normalized = title ? decodeHtmlEntities(stripHtml(title)).trim() : "";
  return normalized.length > 0 ? normalized : undefined;
}

function stripHtml(html: string): string {
  return decodeHtmlEntities(
    html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|section|article|header|footer|h[1-6]|li)>/gi, "\n")
      .replace(/<[^>]+>/g, " ")
      .replace(/[ \t\r\f\v]+/g, " ")
      .replace(/\n\s+/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim(),
  );
}

function isHtmlContent(contentType: string | undefined, body: string): boolean {
  return Boolean(
    contentType?.toLowerCase().includes("text/html") ||
      /^\s*<!doctype html/i.test(body) ||
      /^\s*<html[\s>]/i.test(body),
  );
}

export function renderWebFetchContent(
  body: string,
  contentType: string | undefined,
  format: WebFetchFormat,
): { content: string; title?: string } {
  const html = isHtmlContent(contentType, body);
  if (!html) return { content: body };
  const title = extractTitle(body);
  if (format === "html") return { content: body, title };
  return { content: stripHtml(body), title };
}

async function defaultRunner(
  input: WebFetchRunInput,
  ctx: ToolContext,
  timeoutMs: number,
): Promise<WebFetchRunResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  ctx.abortSignal.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const response = await fetch(input.url, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        Accept: "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
        "User-Agent": "MagiResearchAgent/1.0",
      },
    });
    const buffer = Buffer.from(await response.arrayBuffer());
    const truncated = buffer.byteLength > MAX_BODY_BYTES;
    const body = buffer.subarray(0, MAX_BODY_BYTES).toString("utf8");
    return {
      statusCode: response.status,
      url: input.url,
      finalUrl: response.url,
      contentType: response.headers.get("content-type") ?? undefined,
      body,
      truncated,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function errorResult(
  code: string,
  message: string,
  start: number,
): ToolResult<WebFetchOutput> {
  return {
    status: "error",
    errorCode: code,
    errorMessage: message,
    durationMs: Date.now() - start,
  };
}

export function makeWebFetchTool(opts: WebFetchToolOptions = {}): Tool<WebFetchInput, WebFetchOutput> {
  const runner = opts.runner ?? defaultRunner;
  const resolveHost = opts.resolveHost ?? (opts.runner ? null : defaultResolvePublicFetchHost);
  return {
    name: "WebFetch",
    description:
      "Fetch a public HTTP(S) URL and register it as an inspected source. Use after WebSearch to inspect primary pages before citing or synthesizing claims.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    dangerous: false,
    tags: ["web", "fetch", "current", "sources", "research"],
    validate(input) {
      return validateWebFetchInput(input as WebFetchInput);
    },
    async execute(input: WebFetchInput, ctx: ToolContext): Promise<ToolResult<WebFetchOutput>> {
      const start = Date.now();
      const validation = validateWebFetchInput(input);
      if (validation) return errorResult("invalid_input", validation, start);

      const url = stringValue(input.url) ?? "";
      const urlError = await validatePublicFetchUrl(url, resolveHost);
      if (urlError) return errorResult("invalid_url", urlError, start);

      const format = normalizeWebFetchFormat(input.format);
      const timeoutMs = normalizeTimeout(input.timeoutMs);
      try {
        const run = await runner({ url, format }, ctx, timeoutMs);
        const rendered = renderWebFetchContent(run.body, run.contentType, format);
        const finalUrl = run.finalUrl ?? run.url;
        const source = ctx.sourceLedger?.recordSource({
          turnId: ctx.turnId,
          toolName: "WebFetch",
          kind: "web_fetch",
          uri: finalUrl,
          title: rendered.title,
          contentHash: contentHash(rendered.content),
          contentType: run.contentType,
          trustTier: "unknown",
          snippets: rendered.content ? [rendered.content.slice(0, 500)] : [],
        });
        if (source) {
          ctx.emitAgentEvent?.({ type: "source_inspected", source });
        }
        return {
          status: run.statusCode >= 200 && run.statusCode < 400 ? "ok" : "error",
          output: {
            url: run.url,
            finalUrl,
            statusCode: run.statusCode,
            contentType: run.contentType,
            format,
            title: rendered.title,
            content: rendered.content,
            truncated: run.truncated,
            sourceId: source?.sourceId,
          },
          errorCode: run.statusCode >= 200 && run.statusCode < 400 ? undefined : "fetch_failed",
          errorMessage: run.statusCode >= 200 && run.statusCode < 400
            ? undefined
            : `web fetch returned HTTP ${run.statusCode}`,
          durationMs: Date.now() - start,
          metadata: {
            sourceId: source?.sourceId,
            truncated: run.truncated,
          },
        };
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return errorResult("fetch_failed", message, start);
      }
    },
  };
}
