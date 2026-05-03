import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ToolContext } from "../Tool.js";
import { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import { makeFileDeliverTool } from "./FileDeliver.js";
import type { ChannelRef } from "../util/types.js";

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

  it("uploads an existing workspace file path without DocumentWrite conversion", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "wsj_pipeline"), { recursive: true });
    await fs.writeFile(
      path.join(root, "wsj_pipeline", "WSJ_PIPELINE_HANDBOOK.md"),
      "# WSJ Pipeline\n\nRunbook body",
    );

    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "att-md" }), {
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
        path: "wsj_pipeline/WSJ_PIPELINE_HANDBOOK.md",
        target: "chat",
        chat: { channel: "general" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.deliveries[0]).toEqual({
      target: "chat",
      status: "sent",
      externalId: "att-md",
      marker: "[attachment:att-md:WSJ_PIPELINE_HANDBOOK.md]",
      attemptCount: 1,
    });

    const body = String(fetchImpl.mock.calls[0]?.[1]?.body);
    expect(body).toBe("[object FormData]");
    const pending = await registry.listUndelivered("s-1", "t-1");
    expect(pending).toEqual([]);
  });

  it("sends chat delivery directly to the current Telegram or Discord channel when available", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "exports", "telegram-report.docx"), "doc");
    const artifact = await registry.register({
      sessionKey: "s-1",
      turnId: "t-1",
      kind: "document",
      format: "docx",
      title: "Telegram Report",
      filename: "telegram-report.docx",
      mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      workspacePath: "exports/telegram-report.docx",
      previewKind: "download-only",
      createdByTool: "DocumentWrite",
      sourceKind: "structured",
    });

    const sendFile = vi.fn(async () => {});
    const tool = makeFileDeliverTool({
      workspaceRoot: root,
      outputRegistry: registry,
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: vi.fn(),
      sleepImpl: async () => {},
      getSourceChannel: () => ({ type: "telegram", channelId: "1234" }),
      sendFile,
    });

    const result = await tool.execute(
      {
        artifactId: artifact.artifactId,
        target: "chat",
        chat: { caption: "here" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.deliveries[0]).toEqual({
      target: "chat",
      status: "sent",
      externalId: "telegram:1234",
      marker: undefined,
      attemptCount: 1,
    });
    expect(sendFile).toHaveBeenCalledWith(
      { type: "telegram", channelId: "1234" } satisfies ChannelRef,
      path.join(root, "exports", "telegram-report.docx"),
      "here",
      "document",
    );

    const updated = await registry.get(artifact.artifactId);
    expect(updated.deliveries).toEqual([
      expect.objectContaining({
        target: "chat",
        status: "sent",
        externalId: "telegram:1234",
        attemptCount: 1,
      }),
    ]);
  });

  it("uses specific mime types for direct HWPX workspace file delivery", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    await fs.mkdir(path.join(root, "exports"), { recursive: true });
    await fs.writeFile(path.join(root, "exports", "minutes.hwpx"), "hwpx");

    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "att-hwpx" }), {
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
        path: "exports/minutes.hwpx",
        target: "chat",
        chat: { channel: "general" },
      },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.deliveries[0]).toEqual({
      target: "chat",
      status: "sent",
      externalId: "att-hwpx",
      marker: "[attachment:att-hwpx:minutes.hwpx]",
      attemptCount: 1,
    });

    const form = fetchImpl.mock.calls[0]?.[1]?.body;
    expect(form).toBeInstanceOf(FormData);
    const file = (form as FormData).get("file");
    expect(file).toBeInstanceOf(File);
    expect((file as File).type).toBe("application/hwp+zip");
  });

  it("rejects workspace file paths outside the workspace", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);
    const tool = makeFileDeliverTool({
      workspaceRoot: root,
      outputRegistry: registry,
      chatProxyUrl: "http://chat-proxy",
      gatewayToken: "gw-token",
      fetchImpl: vi.fn(),
      sleepImpl: async () => {},
    });

    const result = await tool.execute(
      {
        path: "../secret.md",
        target: "chat",
      },
      ctx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("outside workspace");
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
