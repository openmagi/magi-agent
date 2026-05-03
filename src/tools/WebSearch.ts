/**
 * WebSearch — search the web via DuckDuckGo HTML lite.
 *
 * Zero external dependencies, no API key required. Parses DuckDuckGo's
 * HTML lite endpoint which doesn't require JavaScript.
 */

import type { Tool, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";

export type WebSearchToolName = "WebSearch" | "web-search" | "web_search";

export interface WebSearchInput {
  query: string;
  maxResults?: number;
  timeoutMs?: number;
}

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface WebSearchOutput {
  query: string;
  results: WebSearchResult[];
  totalResults: number;
  source: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    query: { type: "string", description: "Search query." },
    maxResults: { type: "integer", minimum: 1, maximum: 20, description: "Max results (default 8)." },
    timeoutMs: { type: "integer", minimum: 1000, description: "Timeout in ms (default 15000)." },
  },
  required: ["query"],
} as const;

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_RESULTS = 8;

function parseDdgHtml(html: string): WebSearchResult[] {
  const results: WebSearchResult[] = [];
  const linkRe = /<a[^>]+class="result-link"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  const snippetRe = /<td[^>]+class="result-snippet"[^>]*>([\s\S]*?)<\/td>/gi;

  const links: { url: string; title: string }[] = [];
  let m;
  while ((m = linkRe.exec(html)) !== null) {
    const url = (m[1] ?? "").replace(/&amp;/g, "&");
    const title = (m[2] ?? "").replace(/<[^>]+>/g, "").trim();
    if (url.startsWith("http") && title) links.push({ url, title });
  }

  const snippets: string[] = [];
  while ((m = snippetRe.exec(html)) !== null) {
    snippets.push((m[1] ?? "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim());
  }

  for (let i = 0; i < links.length; i++) {
    const link = links[i];
    if (link) results.push({ title: link.title, url: link.url, snippet: snippets[i] ?? "" });
  }

  // Fallback: extract any non-DDG links if structured parsing failed
  if (results.length === 0) {
    const altRe = /<a[^>]+href="(https?:\/\/[^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
    const seen = new Set<string>();
    while ((m = altRe.exec(html)) !== null) {
      const url = (m[1] ?? "").replace(/&amp;/g, "&");
      const title = (m[2] ?? "").replace(/<[^>]+>/g, "").trim();
      if (url && title.length > 5 && !url.includes("duckduckgo.com") && !seen.has(url)) {
        seen.add(url);
        results.push({ title, url, snippet: "" });
      }
    }
  }

  return results;
}

export function makeWebSearchTool(
  opts: { name?: WebSearchToolName } = {},
): Tool<WebSearchInput, WebSearchOutput> {
  return {
    name: opts.name ?? "WebSearch",
    description:
      "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. " +
      "No API key required. Use WebFetch to read the full content of any result URL.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",

    validate(input) {
      if (!input.query?.trim()) return "Query must not be empty.";
      return null;
    },

    async execute(input): Promise<ToolResult<WebSearchOutput>> {
      const t0 = Date.now();
      const maxResults = Math.min(input.maxResults ?? DEFAULT_MAX_RESULTS, 20);
      const timeoutMs = Math.min(input.timeoutMs ?? DEFAULT_TIMEOUT_MS, 30_000);

      let results: WebSearchResult[] = [];
      const source = "duckduckgo-html";

      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const resp = await fetch(
          `https://html.duckduckgo.com/html/?q=${encodeURIComponent(input.query)}`,
          {
            signal: controller.signal,
            headers: {
              "User-Agent": "ClawyAgent/1.0 (https://github.com/ClawyPro/clawy-agent)",
              Accept: "text/html",
            },
          },
        );
        clearTimeout(timer);

        if (resp.ok) {
          const html = await resp.text();
          results = parseDdgHtml(html);
        }
      } catch {
        // fetch failed
      }

      if (results.length === 0) {
        return errorResult("No search results found. Try a different query.", t0);
      }

      const trimmed = results.slice(0, maxResults);

      return {
        status: "ok",
        output: { query: input.query, results: trimmed, totalResults: trimmed.length, source },
        durationMs: Date.now() - t0,
      };
    },
  };
}
