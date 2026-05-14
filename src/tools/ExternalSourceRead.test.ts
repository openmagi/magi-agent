import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import type { ToolContext } from "../Tool.js";
import { makeExternalSourceReadTool } from "./ExternalSourceRead.js";

const roots: string[] = [];

async function makeRoot(prefix: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  roots.push(root);
  return root;
}

function ctx(
  workspaceRoot: string,
  sourceLedger = new SourceLedgerStore({ now: () => 99 }),
  events: unknown[] = [],
): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "turn-1",
    workspaceRoot,
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

describe("ExternalSourceRead", () => {
  it("records external cache file reads in the source ledger", async () => {
    const workspaceRoot = await makeRoot("external-source-workspace-");
    const cacheRoot = await makeRoot("external-source-cache-");
    await fs.mkdir(
      path.join(cacheRoot, "github.com/anomalyco/opencode/packages/opencode/src"),
      { recursive: true },
    );
    await fs.writeFile(
      path.join(cacheRoot, "github.com/anomalyco/opencode/packages/opencode/src/tool.ts"),
      "export function webResearchTool() { return true; }\n",
      "utf8",
    );
    const ledger = new SourceLedgerStore({ now: () => 4321 });
    const events: unknown[] = [];
    const tool = makeExternalSourceReadTool({ cacheRoot });

    const result = await tool.execute(
      { source: "github.com/anomalyco/opencode", path: "packages/opencode/src/tool.ts" },
      ctx(workspaceRoot, ledger, events),
    );

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({ sourceId: "src_1" });
    expect(result.output).toMatchObject({
      source: "github.com/anomalyco/opencode",
      path: "packages/opencode/src/tool.ts",
      uri: "external:github.com/anomalyco/opencode/packages/opencode/src/tool.ts",
      truncated: false,
    });
    expect(ledger.snapshot()).toMatchObject([
      {
        sourceId: "src_1",
        turnId: "turn-1",
        toolName: "ExternalSourceRead",
        kind: "external_repo",
        uri: "external:github.com/anomalyco/opencode/packages/opencode/src/tool.ts",
        title: "github.com/anomalyco/opencode/packages/opencode/src/tool.ts",
        contentType: "text/plain",
        inspectedAt: 4321,
      },
    ]);
    expect(ledger.snapshot()[0]?.contentHash).toMatch(/^sha256:/);
    expect(events).toMatchObject([
      {
        type: "source_inspected",
        source: {
          sourceId: "src_1",
          kind: "external_repo",
          uri: "external:github.com/anomalyco/opencode/packages/opencode/src/tool.ts",
        },
      },
    ]);
  });

  it("records cached docs URL reads as external docs evidence", async () => {
    const workspaceRoot = await makeRoot("external-source-workspace-");
    const cacheRoot = await makeRoot("external-source-cache-");
    await fs.mkdir(path.join(cacheRoot, "docs/docs.example.com/urlhash"), { recursive: true });
    await fs.writeFile(
      path.join(cacheRoot, "docs/docs.example.com/urlhash/index.md"),
      "# SDK Docs\n\nInstall the SDK.",
      "utf8",
    );
    const ledger = new SourceLedgerStore({ now: () => 9876 });
    const events: unknown[] = [];
    const tool = makeExternalSourceReadTool({ cacheRoot });

    const result = await tool.execute(
      { source: "docs/docs.example.com/urlhash", path: "index.md" },
      ctx(workspaceRoot, ledger, events),
    );

    expect(result.status).toBe("ok");
    expect(ledger.snapshot()).toEqual([
      expect.objectContaining({
        toolName: "ExternalSourceRead",
        kind: "external_doc",
        uri: "external:docs/docs.example.com/urlhash/index.md",
        title: "docs/docs.example.com/urlhash/index.md",
        inspectedAt: 9876,
      }),
    ]);
    expect(events).toEqual([
      expect.objectContaining({
        type: "source_inspected",
        source: expect.objectContaining({ kind: "external_doc" }),
      }),
    ]);
  });

  it("rejects source escapes", async () => {
    const workspaceRoot = await makeRoot("external-source-workspace-");
    const cacheRoot = await makeRoot("external-source-cache-");
    const tool = makeExternalSourceReadTool({ cacheRoot });

    const result = await tool.execute(
      { source: "../outside", path: "file.ts" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("path_escape");
  });

  it("rejects path escapes inside a source", async () => {
    const workspaceRoot = await makeRoot("external-source-workspace-");
    const cacheRoot = await makeRoot("external-source-cache-");
    await fs.mkdir(path.join(cacheRoot, "github.com/anomalyco/opencode"), { recursive: true });
    const tool = makeExternalSourceReadTool({ cacheRoot });

    const result = await tool.execute(
      { source: "github.com/anomalyco/opencode", path: "../secret.md" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("path_escape");
  });
});
