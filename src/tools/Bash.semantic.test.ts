import { describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ToolContext } from "../Tool.js";
import { interpretBashExit, makeBashTool } from "./Bash.js";

function makeCtx(root: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "approve" }),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("Bash semantic exit handling", () => {
  it("recognizes semantic no-match exits for path-qualified grep binaries", () => {
    expect(interpretBashExit("/usr/bin/grep needle haystack.txt", 1, "")).toMatchObject({
      status: "ok",
      semanticStatus: "no_match",
    });
  });

  it("treats grep no-match exit 1 as a semantic success", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const tool = makeBashTool(root);
      const result = await tool.execute(
        { command: "printf 'alpha\\n' | grep -q beta" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.errorCode).toBeUndefined();
      expect(result.metadata).toMatchObject({
        semanticStatus: "no_match",
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("treats diff exit 1 as a semantic success with differences", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      await fs.writeFile(path.join(root, "a.txt"), "alpha\n", "utf8");
      await fs.writeFile(path.join(root, "b.txt"), "beta\n", "utf8");
      const tool = makeBashTool(root);
      const result = await tool.execute(
        { command: "diff a.txt b.txt" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.errorCode).toBeUndefined();
      expect(result.metadata).toMatchObject({
        semanticStatus: "different",
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
