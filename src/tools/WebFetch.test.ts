import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import type { ToolContext } from "../Tool.js";
import {
  makeWebFetchTool,
  type WebFetchRunner,
} from "./WebFetch.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "web-fetch-"));
  roots.push(root);
  return root;
}

function ctx(
  root: string,
  sourceLedger = new SourceLedgerStore({ now: () => 99 }),
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
    sourceLedger,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("WebFetch", () => {
  it("fetches a public URL and records a source ledger entry", async () => {
    const root = await makeRoot();
    const ledger = new SourceLedgerStore({ now: () => 4242 });
    const events: unknown[] = [];
    const runner: WebFetchRunner = async () => ({
      statusCode: 200,
      url: "https://docs.example.com/page",
      finalUrl: "https://docs.example.com/page?canonical=1",
      contentType: "text/html; charset=utf-8",
      body: "<html><head><title>Docs</title></head><body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>",
      truncated: false,
    });
    const tool = makeWebFetchTool({ runner });

    const result = await tool.execute(
      { url: "https://docs.example.com/page", format: "markdown" },
      ctx(root, ledger, events),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.content).toContain("Hello");
    expect(result.output?.content).toContain("World");
    expect(result.output?.content).not.toContain("bad()");
    expect(result.output?.sourceId).toBe("src_1");
    expect(ledger.snapshot()).toMatchObject([
      {
        sourceId: "src_1",
        turnId: "t-1",
        toolName: "WebFetch",
        kind: "web_fetch",
        uri: "https://docs.example.com/page?canonical=1",
        title: "Docs",
        contentType: "text/html; charset=utf-8",
        inspectedAt: 4242,
      },
    ]);
    expect(ledger.snapshot()[0]?.contentHash).toMatch(/^sha256:/);
    expect(events).toMatchObject([
      {
        type: "source_inspected",
        source: {
          sourceId: "src_1",
          kind: "web_fetch",
          uri: "https://docs.example.com/page?canonical=1",
          title: "Docs",
          inspectedAt: 4242,
        },
      },
    ]);
  });

  it("rejects localhost and metadata URLs before invoking the runner", async () => {
    const root = await makeRoot();
    const calls: string[] = [];
    const tool = makeWebFetchTool({
      runner: async (input) => {
        calls.push(input.url);
        throw new Error("runner should not be called");
      },
    });
    const toolCtx = ctx(root);

    for (const url of [
      "http://localhost:3000",
      "http://127.0.0.1:3000",
      "http://169.254.169.254/latest/meta-data/",
      "ftp://example.com/file",
    ]) {
      const result = await tool.execute({ url }, toolCtx);
      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("invalid_url");
    }
    expect(calls).toEqual([]);
  });
});
