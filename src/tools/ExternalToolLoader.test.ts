import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadExternalTools } from "./ExternalToolLoader.js";
import type { ToolContext } from "../Tool.js";

async function writeToolConfig(
  dir: string,
  name: string,
  config: Record<string, unknown>,
): Promise<string> {
  const toolDir = path.join(dir, name);
  await fs.mkdir(toolDir, { recursive: true });
  const lines: string[] = [];
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "object" && value !== null) {
      lines.push(`${key}:`);
      for (const [k2, v2] of Object.entries(value as Record<string, unknown>)) {
        lines.push(`  ${k2}: ${JSON.stringify(v2)}`);
      }
    } else {
      lines.push(`${key}: ${JSON.stringify(value)}`);
    }
  }
  await fs.writeFile(path.join(toolDir, "tool.config.yaml"), lines.join("\n"), "utf8");
  return toolDir;
}

async function writeToolModule(
  dir: string,
  name: string,
  code: string,
): Promise<void> {
  const toolDir = path.join(dir, name);
  await fs.mkdir(toolDir, { recursive: true });
  await fs.writeFile(path.join(toolDir, "index.mjs"), code, "utf8");
}

function makeToolContext(overrides?: Partial<ToolContext>): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    workspaceRoot: "/tmp/test",
    abortSignal: new AbortController().signal,
    emitProgress: vi.fn(),
    askUser: vi.fn(),
    staging: {
      stageFileWrite: vi.fn(),
      stageTranscriptAppend: vi.fn(),
      stageAuditEvent: vi.fn(),
    },
    ...overrides,
  };
}

describe("ExternalToolLoader", () => {
  let tmpDir: string;
  let toolsDir: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "ext-tool-loader-"));
    toolsDir = path.join(tmpDir, "tools");
    await fs.mkdir(toolsDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("discovers and loads tools from directory", async () => {
    await writeToolConfig(toolsDir, "my-tool", {
      name: "my-tool",
      description: "A test tool",
      permission: "read",
    });
    await writeToolModule(
      toolsDir,
      "my-tool",
      `export default function(sdk, config) {
        return {
          execute: async (input) => ({ result: "ok", input })
        };
      }`,
    );

    const log = vi.fn();
    const { tools, result } = await loadExternalTools([toolsDir], [], log);

    expect(result.loaded).toEqual(["my-tool"]);
    expect(result.failed).toHaveLength(0);
    expect(tools).toHaveLength(1);
    expect(tools[0].name).toBe("my-tool");
    expect(tools[0].kind).toBe("external");
    expect(tools[0].permission).toBe("read");
  });

  it("validates config: missing name", async () => {
    await writeToolConfig(toolsDir, "bad-tool", {
      description: "No name",
      permission: "read",
    });
    await writeToolModule(
      toolsDir,
      "bad-tool",
      `export default () => ({ execute: async () => ({}) })`,
    );

    const log = vi.fn();
    const { tools, result } = await loadExternalTools([toolsDir], [], log);

    expect(tools).toHaveLength(0);
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].error).toContain("missing name");
  });

  it("validates config: invalid permission", async () => {
    await writeToolConfig(toolsDir, "bad-perm", {
      name: "bad-perm",
      description: "Bad permission",
      permission: "admin",
    });
    await writeToolModule(
      toolsDir,
      "bad-perm",
      `export default () => ({ execute: async () => ({}) })`,
    );

    const log = vi.fn();
    const { tools, result } = await loadExternalTools([toolsDir], [], log);

    expect(tools).toHaveLength(0);
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].error).toContain("invalid permission");
  });

  it("execute permission requires trusted dir", async () => {
    await writeToolConfig(toolsDir, "exec-tool", {
      name: "exec-tool",
      description: "Needs trust",
      permission: "execute",
    });
    await writeToolModule(
      toolsDir,
      "exec-tool",
      `export default () => ({ execute: async () => ({}) })`,
    );

    const log = vi.fn();
    // Not in trusted dirs
    const { tools, result } = await loadExternalTools([toolsDir], [], log);
    expect(tools).toHaveLength(0);
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].error).toContain("execute permission requires trusted dir");

    // Now in trusted dirs
    const toolDir = path.join(toolsDir, "exec-tool");
    const { tools: tools2, result: result2 } = await loadExternalTools(
      [toolsDir],
      [toolDir],
      log,
    );
    expect(tools2).toHaveLength(1);
    expect(result2.loaded).toEqual(["exec-tool"]);
  });

  it("failed tools do not block others", async () => {
    // Good tool
    await writeToolConfig(toolsDir, "good-tool", {
      name: "good-tool",
      description: "Works fine",
      permission: "read",
    });
    await writeToolModule(
      toolsDir,
      "good-tool",
      `export default () => ({ execute: async () => "ok" })`,
    );

    // Bad tool — missing description
    await writeToolConfig(toolsDir, "bad-tool", {
      name: "bad-tool",
    });
    await writeToolModule(
      toolsDir,
      "bad-tool",
      `export default () => ({ execute: async () => "ok" })`,
    );

    const log = vi.fn();
    const { tools, result } = await loadExternalTools([toolsDir], [], log);

    expect(tools).toHaveLength(1);
    expect(tools[0].name).toBe("good-tool");
    expect(result.loaded).toEqual(["good-tool"]);
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].error).toContain("missing description");
  });

  it("adaptContext strips internal fields for non-full trust", async () => {
    await writeToolConfig(toolsDir, "ctx-tool", {
      name: "ctx-tool",
      description: "Checks context",
      permission: "read",
      trustLevel: "sandboxed",
    });
    await writeToolModule(
      toolsDir,
      "ctx-tool",
      `export default () => ({
        execute: async (input, ctx) => ({
          hasBotId: !!ctx.botId,
          hasSessionKey: !!ctx.sessionKey,
          hasTurnId: !!ctx.turnId,
          hasWorkspaceRoot: !!ctx.workspaceRoot,
          hasAbortSignal: !!ctx.abortSignal,
          hasEmitProgress: typeof ctx.emitProgress === "function",
          hasStaging: !!ctx.staging,
          hasAskUser: typeof ctx.askUser === "function",
        })
      })`,
    );

    const log = vi.fn();
    const { tools } = await loadExternalTools([toolsDir], [], log);
    const ctx = makeToolContext();
    const result = await tools[0].execute({}, ctx);

    expect(result.status).toBe("ok");
    const output = result.output as Record<string, boolean>;
    // Should have these
    expect(output.hasBotId).toBe(true);
    expect(output.hasSessionKey).toBe(true);
    expect(output.hasTurnId).toBe(true);
    expect(output.hasWorkspaceRoot).toBe(true);
    expect(output.hasAbortSignal).toBe(true);
    expect(output.hasEmitProgress).toBe(true);
    // Should NOT have these (stripped by adaptContext)
    expect(output.hasStaging).toBe(false);
    expect(output.hasAskUser).toBe(false);
  });

  it("handles missing directory gracefully", async () => {
    const log = vi.fn();
    const { tools, result } = await loadExternalTools(
      [path.join(tmpDir, "nonexistent")],
      [],
      log,
    );

    expect(tools).toHaveLength(0);
    expect(result.loaded).toHaveLength(0);
    expect(result.failed).toHaveLength(0);
  });

  it("rejects factory that does not return execute function", async () => {
    await writeToolConfig(toolsDir, "no-exec", {
      name: "no-exec",
      description: "No execute",
      permission: "read",
    });
    await writeToolModule(
      toolsDir,
      "no-exec",
      `export default () => ({ notExecute: async () => "ok" })`,
    );

    const log = vi.fn();
    const { tools, result } = await loadExternalTools([toolsDir], [], log);

    expect(tools).toHaveLength(0);
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].error).toContain("execute()");
  });

  it("catches execute errors and returns error status", async () => {
    await writeToolConfig(toolsDir, "err-tool", {
      name: "err-tool",
      description: "Throws on execute",
      permission: "read",
    });
    await writeToolModule(
      toolsDir,
      "err-tool",
      `export default () => ({
        execute: async () => { throw new Error("boom"); }
      })`,
    );

    const log = vi.fn();
    const { tools } = await loadExternalTools([toolsDir], [], log);
    const result = await tools[0].execute({}, makeToolContext());

    expect(result.status).toBe("error");
    expect(result.errorMessage).toBe("boom");
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });
});
