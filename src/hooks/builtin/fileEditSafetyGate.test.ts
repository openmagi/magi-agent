import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";
import { makeFileEditSafetyGateHook } from "./fileEditSafetyGate.js";

function sha256(text: string): string {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function toolReadTranscript(
  pathValue: string,
  content: string,
  turnId = "turn-1",
): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 1,
      turnId,
      toolUseId: "read-1",
      name: "FileRead",
      input: { path: pathValue },
    },
    {
      kind: "tool_result",
      ts: 2,
      turnId,
      toolUseId: "read-1",
      status: "ok",
      output: JSON.stringify({
        path: pathValue,
        content,
        contentSha256: sha256(content),
      }),
      isError: false,
    },
  ];
}

function hookContext(): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
  };
}

describe("file edit safety gate", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-edit-safety-"));
    await fs.mkdir(path.join(workspaceRoot, "src"), { recursive: true });
    await fs.writeFile(path.join(workspaceRoot, "src/app.ts"), "const value = 1;\n", "utf8");
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("blocks FileEdit when the file was not read earlier in the current turn", async () => {
    const hook = makeFileEditSafetyGateHook({
      workspaceRoot,
      agent: { readSessionTranscript: async () => [] },
    });

    const out = await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "edit-1",
        input: {
          path: "src/app.ts",
          old_string: "1",
          new_string: "2",
        },
      },
      hookContext(),
    );

    expect(out).toEqual({
      action: "block",
      reason: expect.stringContaining("FileRead"),
    });
  });

  it("blocks FileEdit when the file changed after it was read", async () => {
    const transcript = toolReadTranscript("src/app.ts", "const value = 1;\n");
    await fs.writeFile(path.join(workspaceRoot, "src/app.ts"), "const value = 2;\n", "utf8");
    const hook = makeFileEditSafetyGateHook({
      workspaceRoot,
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "edit-1",
        input: {
          path: "src/app.ts",
          old_string: "2",
          new_string: "3",
        },
      },
      hookContext(),
    );

    expect(out).toEqual({
      action: "block",
      reason: expect.stringContaining("stale"),
    });
  });

  it("allows FileEdit when a current-turn FileRead matches current file content", async () => {
    const transcript = toolReadTranscript("src/app.ts", "const value = 1;\n");
    const hook = makeFileEditSafetyGateHook({
      workspaceRoot,
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "edit-1",
        input: {
          path: "src/app.ts",
          old_string: "1",
          new_string: "2",
        },
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("can recover FileRead hashes from truncated JSON transcript output", async () => {
    const content = "const value = 1;\n";
    const transcript = toolReadTranscript("src/app.ts", content);
    const result = transcript[1] as Extract<TranscriptEntry, { kind: "tool_result" }>;
    result.output = `{"path":"src/app.ts","fileSha256":"${sha256(content)}","content":"${"x".repeat(1024)}`;
    const hook = makeFileEditSafetyGateHook({
      workspaceRoot,
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "edit-1",
        input: {
          path: "src/app.ts",
          old_string: "1",
          new_string: "2",
        },
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });
});
