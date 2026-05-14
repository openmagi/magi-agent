import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeProjectVerificationPlannerTool } from "./ProjectVerificationPlanner.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "agent:main:test:1",
    turnId: "turn-1",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    askUser: async () => {
      throw new Error("askUser unavailable");
    },
    emitProgress: () => {},
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("ProjectVerificationPlanner", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "verification-plan-"));
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("recommends deterministic npm script commands from package metadata", async () => {
    await fs.writeFile(
      path.join(root, "package.json"),
      JSON.stringify(
        {
          scripts: {
            test: "vitest run",
            lint: "tsc --noEmit",
            typecheck: "tsc --noEmit --pretty false",
            build: "tsc -p tsconfig.json",
          },
        },
        null,
        2,
      ),
    );
    await fs.writeFile(path.join(root, "package-lock.json"), "{}\n");
    await fs.writeFile(path.join(root, "tsconfig.json"), "{}\n");
    const tool = makeProjectVerificationPlannerTool(root);

    const result = await tool.execute({}, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      cwd: ".",
      projectTypes: ["node", "typescript"],
      commands: [
        {
          kind: "test",
          command: "npm test",
          runner: "TestRun",
          confidence: "high",
        },
        {
          kind: "lint",
          command: "npm run lint",
          runner: "TestRun",
          confidence: "high",
        },
        {
          kind: "typecheck",
          command: "npm run typecheck",
          runner: "TestRun",
          confidence: "high",
        },
        {
          kind: "build",
          command: "npm run build",
          runner: "TestRun",
          confidence: "high",
        },
      ],
    });
    expect(result.metadata).toMatchObject({
      evidenceKind: "verification_plan",
      commandCount: 4,
      projectTypes: ["node", "typescript"],
    });
  });

  it("recommends language-native verification commands for Python, Go, and Rust projects", async () => {
    await fs.writeFile(
      path.join(root, "pyproject.toml"),
      "[tool.pytest.ini_options]\naddopts = \"-q\"\n",
    );
    await fs.writeFile(path.join(root, "go.mod"), "module example.com/app\n");
    await fs.writeFile(path.join(root, "Cargo.toml"), "[package]\nname = \"app\"\n");
    const tool = makeProjectVerificationPlannerTool(root);

    const result = await tool.execute({}, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(result.output?.projectTypes).toEqual(["python", "go", "rust"]);
    expect(result.output?.commands.map((command) => command.command)).toEqual([
      "python -m pytest",
      "python -m compileall .",
      "go test ./...",
      "cargo test",
      "cargo check",
    ]);
  });

  it("scopes planning to a workspace-relative projectPath", async () => {
    await fs.mkdir(path.join(root, "packages/api"), { recursive: true });
    await fs.writeFile(
      path.join(root, "packages/api/package.json"),
      JSON.stringify({ scripts: { test: "vitest run" } }),
    );
    const tool = makeProjectVerificationPlannerTool(root);

    const result = await tool.execute({ projectPath: "packages/api" }, makeCtx(root));

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      cwd: "packages/api",
      commands: [
        {
          command: "npm test",
          cwd: "packages/api",
        },
      ],
    });
  });
});
