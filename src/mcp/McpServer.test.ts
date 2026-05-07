/**
 * McpServer unit tests.
 *
 * Validates JSON-RPC 2.0 framing + MCP tools/* dispatch against a
 * fake Agent that exposes a small tool list including one deliberately
 * erroring tool. Covers happy paths, plan-mode filter, unknown tool
 * (-32601), bad args (-32602), tool error (-32603).
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { McpServer } from "./McpServer.js";
import type { Tool } from "../Tool.js";
import { makeFileReadTool } from "../tools/FileRead.js";

function makeErrorTool(): Tool<{ msg?: string }, { ok: boolean }> {
  return {
    name: "AlwaysErrors",
    description: "A tool that always returns status=error.",
    inputSchema: {
      type: "object",
      properties: { msg: { type: "string" } },
      required: [],
    },
    permission: "read",
    async execute(input) {
      return {
        status: "error",
        errorCode: "synthetic",
        errorMessage: `boom: ${input.msg ?? "no-msg"}`,
        durationMs: 1,
      };
    },
  };
}

function makeValidatingTool(): Tool<{ value?: unknown }, { ok: boolean }> {
  return {
    name: "NeedsValue",
    description: "Tool whose validate() requires a string `value`.",
    inputSchema: {
      type: "object",
      properties: { value: { type: "string" } },
      required: ["value"],
    },
    permission: "read",
    validate(input) {
      if (!input || typeof input.value !== "string") {
        return "`value` must be a string";
      }
      return null;
    },
    async execute() {
      return { status: "ok", output: { ok: true }, durationMs: 1 };
    },
  };
}

function makeBashTool(executions: unknown[]): Tool<{ command?: string }, string> {
  return {
    name: "Bash",
    description: "Run a shell command.",
    inputSchema: {
      type: "object",
      properties: { command: { type: "string" } },
      required: ["command"],
    },
    permission: "execute",
    async execute(input) {
      executions.push(input);
      return { status: "ok", output: input.command ?? "", durationMs: 1 };
    },
  };
}

interface FakeAgent {
  config: { botId: string; workspaceRoot: string };
  tools: { list(): Tool[] };
}

function makeFakeAgent(workspaceRoot: string, tools: Tool[]): FakeAgent {
  return {
    config: { botId: "bot-test", workspaceRoot },
    tools: { list: () => tools },
  };
}

describe("McpServer", () => {
  it("initialize returns serverInfo + tools capability", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-init-"));
    try {
      const agent = makeFakeAgent(tmp, [makeFileReadTool(tmp)]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
      });
      expect("result" in res).toBe(true);
      if (!("result" in res)) return;
      const result = res.result as {
        protocolVersion: string;
        capabilities: { tools: { listChanged: boolean } };
        serverInfo: { name: string; version: string };
      };
      expect(result.protocolVersion).toBe("2025-03-26");
      expect(result.capabilities.tools.listChanged).toBe(false);
      expect(result.serverInfo.name).toBe("magi-core-agent");
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/list returns the full tool catalogue", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-list-"));
    try {
      const agent = makeFakeAgent(tmp, [
        makeFileReadTool(tmp),
        makeErrorTool(),
      ]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/list",
      });
      expect("result" in res).toBe(true);
      if (!("result" in res)) return;
      const result = res.result as {
        tools: Array<{ name: string; description: string; inputSchema: object }>;
      };
      expect(result.tools.map((t) => t.name).sort()).toEqual([
        "AlwaysErrors",
        "FileRead",
      ]);
      const fr = result.tools.find((t) => t.name === "FileRead");
      expect(fr?.inputSchema).toMatchObject({ type: "object" });
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/list filters to plan-mode set when permissionMode=plan", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-plan-"));
    try {
      const agent = makeFakeAgent(tmp, [
        makeFileReadTool(tmp),
        makeErrorTool(), // not in PLAN_MODE_ALLOWED_TOOLS
      ]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle(
        { jsonrpc: "2.0", id: 3, method: "tools/list" },
        { permissionMode: "plan" },
      );
      if (!("result" in res)) throw new Error("expected success");
      const result = res.result as {
        tools: Array<{ name: string }>;
      };
      expect(result.tools.map((t) => t.name)).toEqual(["FileRead"]);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/call invokes FileRead on a fixture workspace", async () => {
    const tmpRaw = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-call-"));
    const tmp = await fs.realpath(tmpRaw);
    try {
      await fs.writeFile(path.join(tmp, "hello.txt"), "greetings", "utf8");
      const agent = makeFakeAgent(tmp, [makeFileReadTool(tmp)]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: "abc",
        method: "tools/call",
        params: { name: "FileRead", arguments: { path: "hello.txt" } },
      });
      if (!("result" in res)) throw new Error("expected success");
      const result = res.result as {
        content: Array<{ type: string; text: string }>;
        isError: boolean;
      };
      expect(result.isError).toBe(false);
      const first = result.content[0];
      if (!first) throw new Error("expected one content block");
      expect(first.type).toBe("text");
      const body = JSON.parse(first.text) as { content: string };
      expect(body.content).toBe("greetings");
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/call denies security-critical shell commands before execution", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-perm-"));
    const executions: unknown[] = [];
    try {
      const agent = makeFakeAgent(tmp, [makeBashTool(executions)]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: "perm",
        method: "tools/call",
        params: { name: "Bash", arguments: { command: 'rm -rf "$(pwd)"' } },
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32603);
      expect(res.error.message).toContain("Permission denied");
      expect(res.error.message).toContain("destructive rm -rf");
      expect(executions).toEqual([]);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/call with unknown tool returns -32601", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-unk-"));
    try {
      const agent = makeFakeAgent(tmp, [makeFileReadTool(tmp)]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 10,
        method: "tools/call",
        params: { name: "NoSuchTool", arguments: {} },
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32601);
      expect(res.error.message).toMatch(/Tool not found/);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/call with failing validate() returns -32602", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-bad-"));
    try {
      const agent = makeFakeAgent(tmp, [makeValidatingTool()]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 11,
        method: "tools/call",
        params: { name: "NeedsValue", arguments: { value: 42 } },
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32602);
      expect(res.error.message).toMatch(/value.*must be a string/);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("tools/call with tool error returns -32603", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-err-"));
    try {
      const agent = makeFakeAgent(tmp, [makeErrorTool()]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 12,
        method: "tools/call",
        params: { name: "AlwaysErrors", arguments: { msg: "hi" } },
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32603);
      expect(res.error.message).toMatch(/boom: hi/);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("unknown method returns -32601", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-mnf-"));
    try {
      const agent = makeFakeAgent(tmp, []);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 13,
        method: "resources/list",
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32601);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("invalid params object on tools/call returns -32602", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mcp-noparam-"));
    try {
      const agent = makeFakeAgent(tmp, [makeFileReadTool(tmp)]);
      const mcp = new McpServer({ agent: agent as never });
      const res = await mcp.handle({
        jsonrpc: "2.0",
        id: 14,
        method: "tools/call",
        // missing `name`
        params: { arguments: {} },
      });
      if (!("error" in res)) throw new Error("expected error");
      expect(res.error.code).toBe(-32602);
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });
});
