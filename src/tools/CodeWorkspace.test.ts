import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { makeCodeWorkspaceTool } from "./CodeWorkspace.js";

async function makeRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "magi-code-workspace-"));
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

describe("CodeWorkspace", () => {
  it("creates a slugged git workspace under code/", async () => {
    const root = await makeRoot();
    const tool = makeCodeWorkspaceTool(root);

    const result = await tool.execute(
      { projectName: "My Cool App!", initializeGit: true },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      relativePath: "code/my-cool-app",
      created: true,
      gitInitialized: true,
    });
    await expect(fs.stat(path.join(root, "code/my-cool-app/.git"))).resolves.toBeTruthy();
  });

  it("reuses an existing workspace and can skip git init", async () => {
    const root = await makeRoot();
    const tool = makeCodeWorkspaceTool(root);

    const first = await tool.execute({ projectName: "Repo", initializeGit: false }, ctx(root));
    const second = await tool.execute({ projectName: "Repo", initializeGit: false }, ctx(root));

    expect(first.output?.created).toBe(true);
    expect(first.output?.gitInitialized).toBe(false);
    expect(second.output?.created).toBe(false);
    expect(second.output?.gitInitialized).toBe(false);
  });
});
