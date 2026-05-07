import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { makeTestRunTool } from "./TestRun.js";

async function makeRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "magi-test-run-"));
}

const ctx = (root: string) => ({
  botId: "bot",
  sessionKey: "session",
  turnId: "turn",
  workspaceRoot: root,
  askUser: async () => ({}),
  emitProgress: () => {},
  abortSignal: new AbortController().signal,
  staging: {
    stageFileWrite: () => {},
    stageTranscriptAppend: () => {},
    stageAuditEvent: () => {},
  },
});

describe("TestRun", () => {
  it("runs a verification command and captures UTF-8 output", async () => {
    const workspaceRoot = await makeRoot();
    const tool = makeTestRunTool(workspaceRoot);

    const result = await tool.execute(
      { command: "printf '안녕 test'", timeoutMs: 5_000 },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      command: "printf '안녕 test'",
      cwd: workspaceRoot,
      exitCode: 0,
      signal: null,
      passed: true,
      stdout: "안녕 test",
    });
    expect(result.metadata).toMatchObject({
      evidenceKind: "verification",
      semanticStatus: "success",
    });
  });

  it("returns error status for failing commands", async () => {
    const workspaceRoot = await makeRoot();
    const tool = makeTestRunTool(workspaceRoot);

    const result = await tool.execute(
      { command: "echo nope >&2; exit 7", timeoutMs: 5_000 },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("exit_7");
    expect(result.output).toMatchObject({
      exitCode: 7,
      passed: false,
      stderr: "nope\n",
    });
  });
});
