/**
 * WebFetch — fetch a URL and extract readable text content.
 *
 * Zero external dependencies. Uses Node.js built-in fetch() and strips
 * HTML tags with regex. When Playwright is installed, falls back to
 * browser rendering for JS-heavy pages.
 */

import type { Tool, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";

export interface WebFetchInput {
  url: string;
  raw?: boolean;
  timeoutMs?: number;
}

export interface WebFetchOutput {
  url: string;
  status: number;
  title: string;
  content: string;
  contentLength: number;
  truncated: boolean;
  usedBrowser: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    url: { type: "string", description: "URL to fetch." },
    raw: { type: "boolean", description: "Return raw HTML instead of extracted text." },
    timeoutMs: { type: "integer", minimum: 1000, description: "Timeout in ms (default 30000)." },
  },
  required: ["url"],
} as const;

const MAX_CONTENT_BYTES = 100 * 1024;
const DEFAULT_TIMEOUT_MS = 30_000;

function stripHtml(html: string): string {
  let text = html;
  text = text.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "");
  text = text.replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "");
  text = text.replace(/<nav[^>]*>[\s\S]*?<\/nav>/gi, "");
  text = text.replace(/<footer[^>]*>[\s\S]*?<\/footer>/gi, "");
  text = text.replace(/<header[^>]*>[\s\S]*?<\/header>/gi, "");
  text = text.replace(/<[^>]+>/g, " ");
  text = text.replace(/&nbsp;/g, " ");
  text = text.replace(/&amp;/g, "&");
  text = text.replace(/&lt;/g, "<");
  text = text.replace(/&gt;/g, ">");
  text = text.replace(/&quot;/g, '"');
  text = text.replace(/&#39;/g, "'");
  text = text.replace(/\s+/g, " ");
  return text.trim();
}

function extractTitle(html: string): string {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return match?.[1]?.trim().replace(/\s+/g, " ") ?? "";
}

async function tryPlaywrightFetch(url: string, timeoutMs: number): Promise<{ html: string; status: number } | null> {
  try {
    // Dynamic import — playwright is optional
    const pw = await (Function('return import("playwright")')() as Promise<Record<string, unknown>>);
    const chromium = pw["chromium"] as { launch(opts: { headless: boolean }): Promise<unknown> };
    const browser = await chromium.launch({ headless: true }) as {
      newPage(): Promise<{
        goto(url: string, opts: Record<string, unknown>): Promise<{ status(): number } | null>;
        waitForTimeout(ms: number): Promise<void>;
        content(): Promise<string>;
      }>;
      close(): Promise<void>;
    };
    try {
      const page = await browser.newPage();
      const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
      await page.waitForTimeout(1000);
      const html = await page.content();
      return { html, status: resp?.status() ?? 200 };
    } finally {
      await browser.close();
    }
  } catch {
    return null;
  }
}

function truncateToBytes(text: string, maxBytes: number): { text: string; truncated: boolean } {
  if (Buffer.byteLength(text, "utf8") <= maxBytes) return { text, truncated: false };
  let used = 0;
  let cutoff = 0;
  for (const char of text) {
    const b = Buffer.byteLength(char, "utf8");
    if (used + b > maxBytes) break;
    used += b;
    cutoff++;
  }
  return { text: text.slice(0, cutoff) + "\n\n[truncated]", truncated: true };
}

export function makeWebFetchTool(): Tool<WebFetchInput, WebFetchOutput> {
  return {
    name: "WebFetch",
    description:
      "Fetch a URL and extract its text content. Strips HTML tags, scripts, and navigation. " +
      "Use this to read web pages, documentation, articles, or any publicly accessible URL.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",

    validate(input) {
      try {
        new URL(input.url);
        return null;
      } catch {
        return `Invalid URL: ${input.url}`;
      }
    },

    async execute(input): Promise<ToolResult<WebFetchOutput>> {
      const t0 = Date.now();
      const timeoutMs = Math.min(input.timeoutMs ?? DEFAULT_TIMEOUT_MS, 60_000);

      let html: string;
      let status: number;
      let usedBrowser = false;

      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const resp = await fetch(input.url, {
          signal: controller.signal,
          headers: {
            "User-Agent": "MagiAgent/1.0 (https://github.com/openmagi/magi-agent)",
            Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          },
          redirect: "follow",
        });
        clearTimeout(timer);
        status = resp.status;
        html = await resp.text();
      } catch (err) {
        const browserResult = await tryPlaywrightFetch(input.url, timeoutMs);
        if (browserResult) {
          html = browserResult.html;
          status = browserResult.status;
          usedBrowser = true;
        } else {
          return errorResult(`Fetch failed: ${(err as Error).message}`, t0);
        }
      }

      if (status >= 400) {
        return errorResult(`HTTP ${status}`, t0);
      }

      const title = extractTitle(html);
      const raw = input.raw ? html : stripHtml(html);
      const { text: content, truncated } = truncateToBytes(raw, MAX_CONTENT_BYTES);

      return {
        status: "ok",
        output: { url: input.url, status, title, content, contentLength: content.length, truncated, usedBrowser },
        durationMs: Date.now() - t0,
      };
    },
  };
}
