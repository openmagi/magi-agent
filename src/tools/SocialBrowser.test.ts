import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeSocialBrowserTool, type SocialBrowserRunner } from "./SocialBrowser.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "social-browser-tool-"));
  roots.push(root);
  return root;
}

function makeCtx(root: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "session-1",
    turnId: "turn-1",
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

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("SocialBrowser", () => {
  it("claims an existing dashboard session and keeps the CDP endpoint out of tool output", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: SocialBrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            provider: "x",
            sessionId: "sess-1",
            cdpEndpoint: "ws://secret-cdp",
            maxItems: 2,
          }),
          stderr: "",
          truncated: false,
        };
      }
      return {
        exitCode: 0,
        signal: null,
        stdout: "one\n\ntwo\nthree",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeSocialBrowserTool(root, { runner });

    const result = await tool.execute({ action: "scrape_visible", provider: "x" }, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(result.output?.stdout).toBe("one\n\ntwo\n[truncated to 2 visible items]");
    expect(JSON.stringify(result)).not.toContain("ws://secret-cdp");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["social-browser/claim", JSON.stringify({ provider: "x", maxItems: 20 })] },
      { command: "agent-browser", args: ["--session", "magi-social-x-sess-1", "connect", "ws://secret-cdp"] },
      { command: "agent-browser", args: ["--session", "magi-social-x-sess-1", "scrape"] },
    ]);
  });

  it("checks status without claiming a CDP endpoint", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: SocialBrowserRunner = async (command, args) => {
      calls.push({ command, args });
      return { exitCode: 0, signal: null, stdout: "{\"connected\":true}", stderr: "", truncated: false };
    };
    const tool = makeSocialBrowserTool(root, { runner });

    const result = await tool.execute({ action: "status", provider: "instagram" }, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(calls).toEqual([{ command: "integration.sh", args: ["social-browser/status?provider=instagram"] }]);
  });

  it("rejects provider escapes before claiming a session", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const tool = makeSocialBrowserTool(root, {
      runner: async (command, args) => {
        calls.push({ command, args });
        throw new Error("runner should not be called");
      },
    });

    const result = await tool.execute({ action: "open", provider: "instagram", url: "https://x.com/home" }, makeCtx(root));

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_input");
    expect(calls).toEqual([]);
  });

  it("rejects screenshot paths outside the workspace", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const tool = makeSocialBrowserTool(root, {
      runner: async (command, args) => {
        calls.push({ command, args });
        return { exitCode: 0, signal: null, stdout: "{}", stderr: "", truncated: false };
      },
    });

    const result = await tool.execute({ action: "screenshot", provider: "x", path: "../escape.png" }, makeCtx(root));

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_path");
    expect(calls).toEqual([]);
  });

  it("redacts CDP endpoint fields from failed claim output", async () => {
    const root = await makeRoot();
    const tool = makeSocialBrowserTool(root, {
      runner: async () => ({
        exitCode: 0,
        signal: null,
        stdout: JSON.stringify({ cdpEndpoint: "ws://secret-cdp", error: "bad claim" }),
        stderr: "",
        truncated: false,
      }),
    });

    const result = await tool.execute({ action: "snapshot", provider: "x" }, makeCtx(root));

    expect(result.status).toBe("error");
    expect(JSON.stringify(result)).not.toContain("ws://secret-cdp");
  });
});
