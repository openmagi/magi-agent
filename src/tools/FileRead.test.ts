import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import type { ToolContext } from "../Tool.js";
import { makeFileReadTool } from "./FileRead.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "file-read-"));
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
    turnId: "turn-1",
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

describe("FileRead", () => {
  it("records successful file reads in the source ledger", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "docs"), { recursive: true });
    await fs.writeFile(path.join(root, "docs/source.md"), "# Source\n\nFact line\n", "utf8");
    const ledger = new SourceLedgerStore({ now: () => 1234 });
    const events: unknown[] = [];
    const tool = makeFileReadTool(root);

    const result = await tool.execute({ path: "docs/source.md" }, ctx(root, ledger, events));

    expect(result.status).toBe("ok");
    expect(result.metadata).toMatchObject({ sourceId: "src_1" });
    expect(ledger.snapshot()).toMatchObject([
      {
        sourceId: "src_1",
        turnId: "turn-1",
        toolName: "FileRead",
        kind: "file",
        uri: "file:docs/source.md",
        title: "docs/source.md",
        contentType: "text/plain",
        inspectedAt: 1234,
      },
    ]);
    expect(ledger.snapshot()[0]?.contentHash).toMatch(/^sha256:/);
    expect(events).toMatchObject([
      {
        type: "source_inspected",
        source: {
          sourceId: "src_1",
          kind: "file",
          uri: "file:docs/source.md",
        },
      },
    ]);
  });
});
