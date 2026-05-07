import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeBrowserTool, type BrowserRunner } from "./Browser.js";

const roots: string[] = [];
const resolvePublicHost = async () => [{ address: "93.184.216.34", family: 4 as const }];
const testCdpEndpoint = "ws://browser-worker:9222/devtools?token=t";
const testAgentSession = "magi-browser-sess-1";

function connectCall(
  cdpEndpoint = testCdpEndpoint,
  sessionId = "sess-1",
): { command: string; args: string[] } {
  return {
    command: "agent-browser",
    args: ["--session", `magi-browser-${sessionId}`, "connect", cdpEndpoint],
  };
}

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "browser-tool-"));
  roots.push(root);
  return root;
}

function makeCtx(root: string, sessionKey = "session-1"): ToolContext {
  return {
    botId: "bot-1",
    sessionKey,
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

describe("Browser", () => {
  it("creates a browser session and reuses its cdp endpoint for open and snapshot", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return {
        exitCode: 0,
        signal: null,
        stdout: "ok",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeBrowserTool(root, { runner, resolveHost: resolvePublicHost });
    const ctx = makeCtx(root);

    const create = await tool.execute({ action: "create_session" }, ctx);
    const open = await tool.execute({ action: "open", url: "https://example.com" }, ctx);
    const snapshot = await tool.execute({ action: "snapshot" }, ctx);

    expect(create.status).toBe("ok");
    expect(open.status).toBe("ok");
    expect(snapshot.status).toBe("ok");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "open", "https://example.com"],
      },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "snapshot"],
      },
    ]);
  });

  it("rejects invalid browser URLs before running agent-browser", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      return {
        exitCode: 0,
        signal: null,
        stdout: JSON.stringify({
          sessionId: "sess-1",
          cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
        }),
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeBrowserTool(root, { runner, resolveHost: resolvePublicHost });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const result = await tool.execute({ action: "open", url: "javascript:alert(1)" }, ctx);

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_url");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
    ]);
  });

  it("rejects local, metadata, and cluster browser URLs before running agent-browser", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "opened", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner, resolveHost: resolvePublicHost });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);

    for (const url of [
      "data:text/html,<h1>inline</h1>",
      "http://localhost:3000",
      "http://127.0.0.1:3000",
      "http://169.254.169.254/latest/meta-data/",
      "http://[::1]:3000",
      "http://metadata.google.internal/computeMetadata/v1/",
      "http://browser-worker:3003/health",
      "http://browser-worker.magi-system.svc.cluster.local:3003/health",
    ]) {
      const result = await tool.execute({ action: "open", url }, ctx);

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("invalid_url");
    }
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
    ]);
  });

  it("allows public raw IP URLs with custom ports", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "opened", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner, resolveHost: resolvePublicHost });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const result = await tool.execute({ action: "open", url: "http://45.130.165.214:18427" }, ctx);

    expect(result.status).toBe("ok");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "open", "http://45.130.165.214:18427"],
      },
    ]);
  });

  it("rejects hostnames that resolve to private addresses", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "opened", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, {
      runner,
      resolveHost: async (hostname) => {
        expect(hostname).toBe("tenant-preview.example.test");
        return [{ address: "10.43.0.20", family: 4 }];
      },
    });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const result = await tool.execute({
      action: "open",
      url: "https://tenant-preview.example.test",
    }, ctx);

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_url");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
    ]);
  });

  it("requires an active session for browser commands", async () => {
    const root = await makeRoot();
    const tool = makeBrowserTool(root, {
      runner: async () => {
        throw new Error("runner should not be called");
      },
    });

    const result = await tool.execute({ action: "snapshot" }, makeCtx(root));

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("no_active_session");
  });

  it("runs scrape, click, fill, scroll, and screenshot with safe argument arrays", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    await tool.execute({ action: "scrape" }, ctx);
    await tool.execute({ action: "click", selector: "@e1" }, ctx);
    await tool.execute({ action: "fill", selector: "@e2", text: "hello world" }, ctx);
    await tool.execute({ action: "scroll", direction: "down" }, ctx);
    await tool.execute({ action: "screenshot", path: "screens/page.png" }, ctx);

    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
      { command: "agent-browser", args: ["--session", testAgentSession, "get", "html"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "click", "@e1"] },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "fill", "@e2", "hello world"],
      },
      { command: "agent-browser", args: ["--session", testAgentSession, "scroll", "down"] },
      {
        command: "agent-browser",
        args: [
          "--session",
          testAgentSession,
          "screenshot",
          path.join(root, "screens/page.png"),
        ],
      },
    ]);
  });

  it("normalizes snapshot refs copied from accessibility output", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    await tool.execute({ action: "click", selector: "[ref=e29]" }, ctx);
    await tool.execute({
      action: "fill",
      selector: 'textbox "Investment scope"[ref=e24]',
      text: "stable allocator",
    }, ctx);

    expect(calls.slice(2)).toEqual([
      { command: "agent-browser", args: ["--session", testAgentSession, "click", "@e29"] },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "fill", "@e24", "stable allocator"],
      },
    ]);
  });

  it("falls back from stale snapshot refs to role and label find actions", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      if (args.includes("@e29") || args.includes("@e24")) {
        return { exitCode: 1, signal: null, stdout: "", stderr: "Element not found", truncated: false };
      }
      return { exitCode: 0, signal: null, stdout: "fallback ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const click = await tool.execute({ action: "click", selector: 'button "Submit"[ref=e29]' }, ctx);
    const fill = await tool.execute({
      action: "fill",
      selector: 'textbox "Investment scope"[ref=e24]',
      text: "stable allocator",
    }, ctx);

    expect(click.status).toBe("ok");
    expect(fill.status).toBe("ok");
    expect(calls.slice(2)).toEqual([
      { command: "agent-browser", args: ["--session", testAgentSession, "click", "@e29"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "wait", "@e29"] },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "find", "role", "button", "click", "Submit"],
      },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "fill", "@e24", "stable allocator"],
      },
      { command: "agent-browser", args: ["--session", testAgentSession, "wait", "@e24"] },
      {
        command: "agent-browser",
        args: [
          "--session",
          testAgentSession,
          "find",
          "label",
          "Investment scope",
          "fill",
          "stable allocator",
        ],
      },
    ]);
  });

  it("waits and retries a selector before semantic find fallback", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    let clickAttempts = 0;
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      if (args.includes("click") && args.includes("@e29")) {
        clickAttempts += 1;
        if (clickAttempts === 1) {
          return { exitCode: 1, signal: null, stdout: "", stderr: "Element not found", truncated: false };
        }
      }
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const result = await tool.execute({ action: "click", selector: 'button "Submit"[ref=e29]' }, ctx);

    expect(result.status).toBe("ok");
    expect(calls.slice(2)).toEqual([
      { command: "agent-browser", args: ["--session", testAgentSession, "click", "@e29"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "wait", "@e29"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "click", "@e29"] },
    ]);
  });

  it("supports coordinate and keyboard fallback actions", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    await tool.execute({ action: "mouse_click", x: 120, y: 240 }, ctx);
    await tool.execute({ action: "keyboard_type", text: "hello world" }, ctx);
    await tool.execute({ action: "press", key: "Enter" }, ctx);

    expect(calls.slice(2)).toEqual([
      { command: "agent-browser", args: ["--session", testAgentSession, "mouse", "move", "120", "240"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "mouse", "down", "left"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "mouse", "up", "left"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "keyboard", "type", "hello world"] },
      { command: "agent-browser", args: ["--session", testAgentSession, "press", "Enter"] },
    ]);
  });

  it("uses longer default timeouts for slow browser actions and clamps overrides", async () => {
    const root = await makeRoot();
    const timeouts: Array<{ command: string; action: string; timeoutMs: number }> = [];
    const runner: BrowserRunner = async (command, args, _ctx, timeoutMs) => {
      timeouts.push({ command, action: args[0] ?? "", timeoutMs });
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner, resolveHost: resolvePublicHost });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    await tool.execute({ action: "open", url: "https://example.com" }, ctx);
    await tool.execute({ action: "scrape" }, ctx);
    await tool.execute({ action: "screenshot", path: "screens/page.png" }, ctx);
    await tool.execute({ action: "snapshot", timeoutMs: 999_999 }, ctx);

    expect(timeouts).toEqual([
      { command: "integration.sh", action: "browser/session-create", timeoutMs: 30_000 },
      { command: "agent-browser", action: "--session", timeoutMs: 30_000 },
      { command: "agent-browser", action: "--session", timeoutMs: 60_000 },
      { command: "agent-browser", action: "--session", timeoutMs: 60_000 },
      { command: "agent-browser", action: "--session", timeoutMs: 60_000 },
      { command: "agent-browser", action: "--session", timeoutMs: 120_000 },
    ]);
  });

  it("rejects screenshot paths that escape the workspace", async () => {
    const root = await makeRoot();
    const runner: BrowserRunner = async (command, args) => {
      if (command === "integration.sh") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      if (command === "agent-browser" && args.includes("connect")) {
        return { exitCode: 0, signal: null, stdout: "connected", stderr: "", truncated: false };
      }
      throw new Error("agent-browser screenshot should not be called");
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const result = await tool.execute({ action: "screenshot", path: "../escape.png" }, ctx);

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_path");
  });

  it("closes a browser session and clears local session state", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh" && args[0] === "browser/session-create") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "closed", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const close = await tool.execute({ action: "close_session" }, ctx);
    const snapshot = await tool.execute({ action: "snapshot" }, ctx);

    expect(close.status).toBe("ok");
    expect(snapshot.status).toBe("error");
    expect(snapshot.errorCode).toBe("no_active_session");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
      { command: "integration.sh", args: ["browser/session-close?sessionId=sess-1"] },
    ]);
  });

  it("keeps local session state when close fails so it can be retried", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh" && args[0] === "browser/session-create") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: "sess-1",
            cdpEndpoint: "ws://browser-worker:9222/devtools?token=t",
          }),
          stderr: "",
          truncated: false,
        };
      }
      if (command === "integration.sh") {
        return { exitCode: 1, signal: null, stdout: "", stderr: "close failed", truncated: false };
      }
      return { exitCode: 0, signal: null, stdout: "snapshot ok", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const close = await tool.execute({ action: "close_session" }, ctx);
    const snapshot = await tool.execute({ action: "snapshot" }, ctx);

    expect(close.status).toBe("error");
    expect(close.errorCode).toBe("command_failed");
    expect(snapshot.status).toBe("ok");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall(),
      { command: "integration.sh", args: ["browser/session-close?sessionId=sess-1"] },
      {
        command: "agent-browser",
        args: ["--session", testAgentSession, "snapshot"],
      },
    ]);
  });

  it("closes an existing remote session before replacing it", async () => {
    const root = await makeRoot();
    const calls: Array<{ command: string; args: string[] }> = [];
    let createCount = 0;
    const runner: BrowserRunner = async (command, args) => {
      calls.push({ command, args });
      if (command === "integration.sh" && args[0] === "browser/session-create") {
        createCount += 1;
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            sessionId: `sess-${createCount}`,
            cdpEndpoint: `ws://browser-worker:9222/devtools?token=t${createCount}`,
          }),
          stderr: "",
          truncated: false,
        };
      }
      return { exitCode: 0, signal: null, stdout: "closed", stderr: "", truncated: false };
    };
    const tool = makeBrowserTool(root, { runner });
    const ctx = makeCtx(root);

    await tool.execute({ action: "create_session" }, ctx);
    const replaced = await tool.execute({ action: "create_session", replaceExisting: true }, ctx);

    expect(replaced.status).toBe("ok");
    expect(replaced.output?.sessionId).toBe("sess-2");
    expect(calls).toEqual([
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall("ws://browser-worker:9222/devtools?token=t1", "sess-1"),
      { command: "integration.sh", args: ["browser/session-close?sessionId=sess-1"] },
      { command: "integration.sh", args: ["browser/session-create"] },
      connectCall("ws://browser-worker:9222/devtools?token=t2", "sess-2"),
    ]);
  });
});
