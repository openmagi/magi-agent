import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToolContext } from "../Tool.js";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import { makeWebSearchTool } from "./WebSearch.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "web-search-"));
  roots.push(root);
  return root;
}

/** Builds a minimal DuckDuckGo HTML lite response with the given results. */
function ddgHtml(
  results: { url: string; title: string; snippet: string }[],
): string {
  const rows = results
    .map(
      (r) =>
        `<tr><td><a class="result-link" href="${r.url}">${r.title}</a></td>` +
        `<td class="result-snippet">${r.snippet}</td></tr>`,
    )
    .join("\n");
  return `<html><body><table>${rows}</table></body></html>`;
}

function ctx(
  root: string,
  sourceLedger?: SourceLedgerStore,
  events: unknown[] = [],
): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "t-1",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    emitAgentEvent: (event) => events.push(event),
    abortSignal: AbortSignal.timeout(5_000),
    ...(sourceLedger ? { sourceLedger } : {}),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("WebSearch", () => {
  it("supports tool name aliases", () => {
    expect(makeWebSearchTool({ name: "WebSearch" }).name).toBe("WebSearch");
    expect(makeWebSearchTool({ name: "web-search" }).name).toBe("web-search");
    expect(makeWebSearchTool({ name: "web_search" }).name).toBe("web_search");
  });

  it("validates empty queries before network access", () => {
    const tool = makeWebSearchTool();
    expect(tool.validate?.({ query: "" })).toBe("Query must not be empty.");
  });

  it("validates missing query field", () => {
    const tool = makeWebSearchTool();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(tool.validate?.({} as any)).toBe("Query must not be empty.");
  });

  it("accepts a valid query", () => {
    const tool = makeWebSearchTool();
    expect(tool.validate?.({ query: "latest AI news" })).toBeNull();
  });

  it("parses DuckDuckGo HTML lite results and returns structured output", async () => {
    const root = await makeRoot();
    const html = ddgHtml([
      {
        url: "https://example.com/ai",
        title: "AI News 2026",
        snippet: "Latest developments in AI",
      },
      {
        url: "https://example.com/ml",
        title: "ML Trends",
        snippet: "Machine learning trends",
      },
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200, headers: { "Content-Type": "text/html" } }),
    );

    const tool = makeWebSearchTool({ name: "WebSearch" });
    const result = await tool.execute(
      { query: "AI news 2026", timeoutMs: 2_000 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toBeDefined();
    expect(result.output!.query).toBe("AI news 2026");
    expect(result.output!.results).toHaveLength(2);
    expect(result.output!.results[0]).toMatchObject({
      title: "AI News 2026",
      url: "https://example.com/ai",
      snippet: "Latest developments in AI",
    });
    expect(result.output!.source).toBe("duckduckgo-html");
    expect(result.output!.totalResults).toBe(2);
  });

  it("records sources in the ledger and emits source_inspected events", async () => {
    const root = await makeRoot();
    const ledger = new SourceLedgerStore({ now: () => 1000 });
    const events: unknown[] = [];

    const html = ddgHtml([
      {
        url: "https://example.com/result",
        title: "Test Result",
        snippet: "A test snippet for source tracking",
      },
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200 }),
    );

    const tool = makeWebSearchTool({ name: "web-search" });
    const result = await tool.execute(
      { query: "source ledger test", timeoutMs: 5_000 },
      ctx(root, ledger, events),
    );

    expect(result.status).toBe("ok");

    const snapshot = ledger.snapshot();
    expect(snapshot).toHaveLength(1);
    expect(snapshot[0]).toMatchObject({
      sourceId: "src_1",
      turnId: "t-1",
      toolName: "web-search",
      kind: "web_search",
      uri: "https://example.com/result",
      title: "Test Result",
      inspectedAt: 1000,
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      type: "source_inspected",
      source: {
        sourceId: "src_1",
        kind: "web_search",
        uri: "https://example.com/result",
        inspectedAt: 1000,
      },
    });
  });

  it("returns an error result when no results are found", async () => {
    const root = await makeRoot();
    const html = ddgHtml([]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200 }),
    );

    const tool = makeWebSearchTool({ name: "WebSearch" });
    const result = await tool.execute({ query: "xyznonexistent" }, ctx(root));

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("No search results found");
  });

  it("returns an error result when fetch throws (network failure)", async () => {
    const root = await makeRoot();

    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new Error("network unreachable"),
    );

    const tool = makeWebSearchTool({ name: "WebSearch" });
    const result = await tool.execute({ query: "network fail" }, ctx(root));

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("No search results found");
  });

  it("returns an error result when response is not ok (e.g. 503)", async () => {
    const root = await makeRoot();

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("Service Unavailable", { status: 503 }),
    );

    const tool = makeWebSearchTool({ name: "WebSearch" });
    const result = await tool.execute({ query: "server error" }, ctx(root));

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("No search results found");
  });

  it("respects maxResults and trims output accordingly", async () => {
    const root = await makeRoot();
    const items = Array.from({ length: 10 }, (_, i) => ({
      url: `https://example.com/${i}`,
      title: `Result ${i}`,
      snippet: `Snippet ${i}`,
    }));

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(ddgHtml(items), { status: 200 }),
    );

    const tool = makeWebSearchTool();
    const result = await tool.execute(
      { query: "many results", maxResults: 3 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output!.results).toHaveLength(3);
    expect(result.output!.totalResults).toBe(3);
  });

  it("clamps maxResults to 20", async () => {
    const root = await makeRoot();
    const items = Array.from({ length: 25 }, (_, i) => ({
      url: `https://example.com/${i}`,
      title: `Result ${i}`,
      snippet: `Snippet ${i}`,
    }));

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(ddgHtml(items), { status: 200 }),
    );

    const tool = makeWebSearchTool();
    const result = await tool.execute(
      { query: "clamp test", maxResults: 50 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output!.results.length).toBeLessThanOrEqual(20);
  });

  it("uses default name WebSearch when none provided", () => {
    const tool = makeWebSearchTool();
    expect(tool.name).toBe("WebSearch");
  });

  it("has net permission and shouldDefer set", () => {
    const tool = makeWebSearchTool();
    expect(tool.permission).toBe("net");
    expect(tool.shouldDefer).toBe(true);
  });

  it("works without sourceLedger (optional dependency)", async () => {
    const root = await makeRoot();
    const html = ddgHtml([
      { url: "https://example.com/a", title: "No Ledger", snippet: "test" },
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200 }),
    );

    const tool = makeWebSearchTool();
    // ctx without sourceLedger
    const result = await tool.execute(
      { query: "no ledger" },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output!.results).toHaveLength(1);
  });

  it("falls back to generic link extraction when structured parsing finds nothing", async () => {
    const root = await makeRoot();
    // HTML with links but without result-link class — triggers fallback parser
    const html = `<html><body>
      <a href="https://fallback.com/page" target="_blank">Fallback Link Title</a>
      <a href="https://duckduckgo.com/about">DDG internal</a>
    </body></html>`;

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200 }),
    );

    const tool = makeWebSearchTool();
    const result = await tool.execute({ query: "fallback parse" }, ctx(root));

    expect(result.status).toBe("ok");
    expect(result.output!.results).toHaveLength(1);
    expect(result.output!.results[0]!.url).toBe("https://fallback.com/page");
    // DDG-internal links should be filtered out
  });

  it("records multiple sources in the ledger for multi-result searches", async () => {
    const root = await makeRoot();
    const ledger = new SourceLedgerStore({ now: () => 2000 });
    const events: unknown[] = [];

    const html = ddgHtml([
      { url: "https://example.com/a", title: "Result A", snippet: "Snippet A" },
      { url: "https://example.com/b", title: "Result B", snippet: "Snippet B" },
      { url: "https://example.com/c", title: "Result C", snippet: "Snippet C" },
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(html, { status: 200 }),
    );

    const tool = makeWebSearchTool({ name: "WebSearch" });
    await tool.execute(
      { query: "multi source", timeoutMs: 5_000 },
      ctx(root, ledger, events),
    );

    expect(ledger.snapshot()).toHaveLength(3);
    expect(ledger.snapshot().map((s) => s.sourceId)).toEqual([
      "src_1",
      "src_2",
      "src_3",
    ]);
    expect(events).toHaveLength(3);
    expect(events.map((e: Record<string, unknown>) => (e as { type: string }).type)).toEqual([
      "source_inspected",
      "source_inspected",
      "source_inspected",
    ]);
  });
});
