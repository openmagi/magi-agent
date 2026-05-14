import { describe, it, expect, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { makeFileSendTool } from "./FileSend.js";
import type { ToolContext } from "../Tool.js";
import type { ChannelRef } from "../util/types.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "sess-1",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({}),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("FileSend", () => {
  it("defaults web/app file-send uploads to the current source channel", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-"));
    try {
      await fs.writeFile(path.join(workspaceRoot, "report.md"), "# Report", "utf8");
      const binDir = path.join(workspaceRoot, "..", "bin");
      await fs.mkdir(binDir, { recursive: true });
      await fs.writeFile(
        path.join(binDir, "file-send.sh"),
        [
          "#!/bin/sh",
          "echo \"$2\" > \"$1.channel\"",
          "echo '{\"id\":\"00000000-0000-4000-8000-000000000222\"}'",
          "echo '[attachment:00000000-0000-4000-8000-000000000222:report.md]'",
        ].join("\n"),
        "utf8",
      );
      const tool = makeFileSendTool({
        workspaceRoot,
        getSourceChannel: () => ({ type: "app", channelId: "stock-skill" }),
        sendFile: vi.fn(),
        binDir,
        gatewayToken: "gw",
        botId: "bot-1",
        chatProxyUrl: "http://chat-proxy",
      });

      const result = await tool.execute({ path: "report.md" }, makeCtx(workspaceRoot));

      expect(result.status).toBe("ok");
      expect(await fs.readFile(path.join(workspaceRoot, "report.md.channel"), "utf8")).toBe(
        "stock-skill\n",
      );
    } finally {
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  it("sends a workspace file to the current Telegram or Discord channel", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-"));
    try {
      await fs.writeFile(path.join(workspaceRoot, "report.pdf"), "PDF", "utf8");
      const sendFile = vi.fn(async () => ({
        provider: "discord" as const,
        channelId: "chan-1",
        messageId: "msg-1",
      }));
      const tool = makeFileSendTool({
        workspaceRoot,
        getSourceChannel: () => ({ type: "discord", channelId: "chan-1" }),
        sendFile,
        binDir: path.join(workspaceRoot, "..", "bin"),
        gatewayToken: "gw",
        botId: "bot-1",
        chatProxyUrl: "http://chat-proxy",
      });

      const result = await tool.execute(
        { path: "report.pdf", caption: "here", mode: "document" },
        makeCtx(workspaceRoot),
      );

      expect(result.status).toBe("ok");
      expect(result.output).toEqual({
        filename: "report.pdf",
        channel: { type: "discord", channelId: "chan-1" },
        mode: "document",
        providerMessageId: "msg-1",
        deliveryAck: "provider_message_receipt",
      });
      expect(sendFile).toHaveBeenCalledWith(
        { type: "discord", channelId: "chan-1" } satisfies ChannelRef,
        path.join(workspaceRoot, "report.pdf"),
        "here",
        "document",
      );
    } finally {
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  it("rejects paths that escape the workspace", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-"));
    try {
      const tool = makeFileSendTool({
        workspaceRoot,
        getSourceChannel: () => ({ type: "telegram", channelId: "chat-1" }),
        sendFile: vi.fn(async () => ({
          provider: "telegram" as const,
          channelId: "chat-1",
        })),
        binDir: path.join(workspaceRoot, "..", "bin"),
        gatewayToken: "gw",
        botId: "bot-1",
        chatProxyUrl: "http://chat-proxy",
      });

      const result = await tool.execute(
        { path: "../secret.txt" },
        makeCtx(workspaceRoot),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("path_escape");
    } finally {
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });
});
