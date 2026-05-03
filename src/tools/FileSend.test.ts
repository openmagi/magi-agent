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
  it("sends a workspace file to the current Telegram or Discord channel", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-"));
    try {
      await fs.writeFile(path.join(workspaceRoot, "report.pdf"), "PDF", "utf8");
      const sendFile = vi.fn(async () => {});
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
        sendFile: vi.fn(async () => {}),
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
