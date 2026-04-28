import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ToolContext } from "../Tool.js";
import { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import { makeFileDeliverTool } from "./FileDeliver.js";

const roots: string[] = [];

function ctx(root: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "t-1",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    abortSignal: AbortSignal.timeout(5_000),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "file-deliver-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("FileDeliver", () => {
  it("uploads an artifact to chat and returns an attachment marker", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "exports", "report.xlsx"), "sheet");
    const artifact = await registry.register({
      sessionKey: "s-1",
      turnId: "t-1",
      kind: "spreadsheet",
      format: "xlsx",
      title: "Report",
      filename: "report.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      workspacePath: "exports/report.xlsx",
      previewKind: "download-only",
      createdByTool: "SpreadsheetWrite",
      sourceKind: "structured",
    });

    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "att-123" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const tool = makeFileDeliverTool({
      workspaceRoot: root,
      outputRegistry: registry,
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl,
      sleepImpl: async () => {},
    });

    const result = await tool.execute(
      {
        artifactId: artifact.artifactId,
        target: "chat",
        chat: { channel: "general" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.deliveries).toEqual([
      {
        target: "chat",
        status: "sent",
        externalId: "att-123",
        marker: "[attachment:att-123:report.xlsx]",
        attemptCount: 1,
      },
    ]);

    const updated = await registry.get(artifact.artifactId);
    expect(updated.deliveries).toEqual([
      expect.objectContaining({
        target: "chat",
        status: "sent",
        externalId: "att-123",
        marker: "[attachment:att-123:report.xlsx]",
        attemptCount: 1,
      }),
    ]);
  });

  it("retries transient chat delivery failures before succeeding", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "exports", "memo.docx"), "doc");
    const artifact = await registry.register({
      sessionKey: "s-1",
      turnId: "t-1",
      kind: "document",
      format: "docx",
      title: "Memo",
      filename: "memo.docx",
      mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      workspacePath: "exports/memo.docx",
      previewKind: "download-only",
      createdByTool: "DocumentWrite",
      sourceKind: "structured",
    });

    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response("temporary outage", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "att-999" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }));
    const sleeps: number[] = [];

    const tool = makeFileDeliverTool({
      workspaceRoot: root,
      outputRegistry: registry,
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl,
      sleepImpl: async (ms) => {
        sleeps.push(ms);
      },
    });

    const result = await tool.execute(
      {
        artifactId: artifact.artifactId,
        target: "chat",
        chat: { channel: "general" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(sleeps).toEqual([10_000]);
    expect(result.output?.deliveries[0]).toEqual({
      target: "chat",
      status: "sent",
      externalId: "att-999",
      marker: "[attachment:att-999:memo.docx]",
      attemptCount: 2,
    });
  });

  it("stores an artifact in KB through the bot-auth upload path", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "exports", "minutes.hwpx"), "hwpx");
    const artifact = await registry.register({
      sessionKey: "s-1",
      turnId: "t-1",
      kind: "document",
      format: "hwpx",
      title: "Minutes",
      filename: "minutes.hwpx",
      mimeType: "application/hwp+zip",
      workspacePath: "exports/minutes.hwpx",
      previewKind: "download-only",
      createdByTool: "DocumentWrite",
      sourceKind: "structured",
    });

    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, collection: "artifacts", filename: "minutes.hwpx" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const tool = makeFileDeliverTool({
      workspaceRoot: root,
      outputRegistry: registry,
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl,
      sleepImpl: async () => {},
    });

    const result = await tool.execute(
      {
        artifactId: artifact.artifactId,
        target: "kb",
        kb: { collection: "artifacts" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.deliveries).toEqual([
      {
        target: "kb",
        status: "sent",
        externalId: "artifacts/minutes.hwpx",
        marker: undefined,
        attemptCount: 1,
      },
    ]);
  });
});
