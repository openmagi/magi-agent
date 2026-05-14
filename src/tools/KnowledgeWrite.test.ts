import { describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import {
  makeKnowledgeWriteTool,
  validateKnowledgeWriteInput,
  buildKnowledgeWriteArgs,
  type KnowledgeWriteInput,
  type KnowledgeWriteRunner,
} from "./KnowledgeWrite.js";

function ctx(): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "t-1",
    workspaceRoot: "/tmp/kb-write-test",
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

describe("validateKnowledgeWriteInput", () => {
  it("rejects non-object input", () => {
    expect(validateKnowledgeWriteInput(null as unknown as KnowledgeWriteInput)).toBe("`input` must be an object");
    expect(validateKnowledgeWriteInput([] as unknown as KnowledgeWriteInput)).toBe("`input` must be an object");
  });

  it("rejects invalid action", () => {
    expect(validateKnowledgeWriteInput({ action: "nope" as KnowledgeWriteInput["action"] })).toMatch(/action/);
  });

  it("requires collection for create-collection", () => {
    expect(validateKnowledgeWriteInput({ action: "create-collection" })).toMatch(/collection/);
    expect(validateKnowledgeWriteInput({ action: "create-collection", collection: "docs" })).toBeNull();
  });

  it("requires collection for delete-collection", () => {
    expect(validateKnowledgeWriteInput({ action: "delete-collection" })).toMatch(/collection/);
    expect(validateKnowledgeWriteInput({ action: "delete-collection", collection: "docs" })).toBeNull();
  });

  it("requires collection, filename, content for add", () => {
    expect(validateKnowledgeWriteInput({ action: "add" })).toMatch(/collection/);
    expect(validateKnowledgeWriteInput({ action: "add", collection: "c" })).toMatch(/filename/);
    expect(validateKnowledgeWriteInput({ action: "add", collection: "c", filename: "f" })).toMatch(/content/);
    expect(validateKnowledgeWriteInput({
      action: "add", collection: "c", filename: "f.md", content: "hello",
    })).toBeNull();
  });

  it("requires collection, filename, content for update", () => {
    expect(validateKnowledgeWriteInput({ action: "update" })).toMatch(/collection/);
    expect(validateKnowledgeWriteInput({
      action: "update", collection: "c", filename: "f.md", content: "updated",
    })).toBeNull();
  });

  it("requires collection and filename for delete", () => {
    expect(validateKnowledgeWriteInput({ action: "delete" })).toMatch(/collection/);
    expect(validateKnowledgeWriteInput({ action: "delete", collection: "c" })).toMatch(/filename/);
    expect(validateKnowledgeWriteInput({ action: "delete", collection: "c", filename: "f.md" })).toBeNull();
  });

  it("rejects invalid scope", () => {
    expect(validateKnowledgeWriteInput({
      action: "create-collection", collection: "c", scope: "invalid" as KnowledgeWriteInput["scope"],
    })).toMatch(/scope/);
  });

  it("accepts valid scope", () => {
    expect(validateKnowledgeWriteInput({
      action: "create-collection", collection: "c", scope: "personal",
    })).toBeNull();
    expect(validateKnowledgeWriteInput({
      action: "create-collection", collection: "c", scope: "org",
    })).toBeNull();
  });
});

describe("buildKnowledgeWriteArgs", () => {
  it("builds create-collection args", () => {
    expect(buildKnowledgeWriteArgs({ action: "create-collection", collection: "reports" }))
      .toEqual(["--create-collection", "reports"]);
  });

  it("builds delete-collection args", () => {
    expect(buildKnowledgeWriteArgs({ action: "delete-collection", collection: "old" }))
      .toEqual(["--delete-collection", "old"]);
  });

  it("builds add args", () => {
    expect(buildKnowledgeWriteArgs({
      action: "add", collection: "docs", filename: "test.md", content: "# Test",
    })).toEqual(["--add", "docs", "test.md", "# Test"]);
  });

  it("builds update args", () => {
    expect(buildKnowledgeWriteArgs({
      action: "update", collection: "docs", filename: "test.md", content: "# Updated",
    })).toEqual(["--update", "docs", "test.md", "# Updated"]);
  });

  it("builds delete args", () => {
    expect(buildKnowledgeWriteArgs({ action: "delete", collection: "docs", filename: "test.md" }))
      .toEqual(["--delete", "docs", "test.md"]);
  });
});

describe("makeKnowledgeWriteTool", () => {
  it("returns error for invalid input", async () => {
    const tool = makeKnowledgeWriteTool();
    const result = await tool.execute({ action: "add" } as KnowledgeWriteInput, ctx());
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_input");
  });

  it("executes add with custom runner", async () => {
    const runner: KnowledgeWriteRunner = async (args) => ({
      exitCode: 0,
      signal: null,
      stdout: `Added ${args[2]} to ${args[1]}`,
      stderr: "",
    });
    const tool = makeKnowledgeWriteTool({ runner });
    const result = await tool.execute({
      action: "add",
      collection: "reports",
      filename: "q1.md",
      content: "# Q1 Report",
    }, ctx());
    expect(result.status).toBe("ok");
    expect(result.output).toContain("Added q1.md to reports");
  });

  it("returns error on non-zero exit code", async () => {
    const runner: KnowledgeWriteRunner = async () => ({
      exitCode: 1,
      signal: null,
      stdout: "",
      stderr: "collection not found",
    });
    const tool = makeKnowledgeWriteTool({ runner });
    const result = await tool.execute({
      action: "add", collection: "missing", filename: "f.md", content: "x",
    }, ctx());
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("exit_1");
    expect(result.errorMessage).toContain("collection not found");
  });

  it("passes scope as KB_SCOPE env", async () => {
    let capturedEnv: Record<string, string> | undefined;
    const runner: KnowledgeWriteRunner = async (_args, _ctx, _timeout, extraEnv) => {
      capturedEnv = extraEnv;
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "" };
    };
    const tool = makeKnowledgeWriteTool({ runner });
    await tool.execute({
      action: "create-collection", collection: "shared", scope: "org",
    }, ctx());
    expect(capturedEnv).toEqual({ KB_SCOPE: "org" });
  });

  it("omits scope env when not specified", async () => {
    let capturedEnv: Record<string, string> | undefined;
    const runner: KnowledgeWriteRunner = async (_args, _ctx, _timeout, extraEnv) => {
      capturedEnv = extraEnv;
      return { exitCode: 0, signal: null, stdout: "ok", stderr: "" };
    };
    const tool = makeKnowledgeWriteTool({ runner });
    await tool.execute({ action: "create-collection", collection: "docs" }, ctx());
    expect(capturedEnv).toBeUndefined();
  });

  it("handles spawn error (null exit code)", async () => {
    const runner: KnowledgeWriteRunner = async () => ({
      exitCode: null,
      signal: null,
      stdout: "",
      stderr: "ENOENT",
    });
    const tool = makeKnowledgeWriteTool({ runner });
    const result = await tool.execute({
      action: "delete", collection: "docs", filename: "f.md",
    }, ctx());
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("spawn_error");
  });

  it("includes metadata with scope and args", async () => {
    const runner: KnowledgeWriteRunner = async () => ({
      exitCode: 0, signal: null, stdout: "done", stderr: "",
    });
    const tool = makeKnowledgeWriteTool({ runner });
    const result = await tool.execute({
      action: "add", collection: "c", filename: "f.md", content: "x", scope: "org",
    }, ctx());
    expect(result.metadata).toEqual(expect.objectContaining({
      scope: "org",
      args: ["--add", "c", "f.md"],
    }));
  });

  it("validate method works", () => {
    const tool = makeKnowledgeWriteTool();
    expect(tool.validate!({ action: "add" } as KnowledgeWriteInput)).toMatch(/collection/);
    expect(tool.validate!({
      action: "add", collection: "c", filename: "f.md", content: "x",
    } as KnowledgeWriteInput)).toBeNull();
  });

  it("uses custom name", () => {
    const tool = makeKnowledgeWriteTool({ name: "KnowledgeWrite" });
    expect(tool.name).toBe("KnowledgeWrite");
  });
});
