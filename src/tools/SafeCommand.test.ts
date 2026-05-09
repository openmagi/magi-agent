import { describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ToolContext } from "../Tool.js";
import { makeSafeCommandTool } from "./SafeCommand.js";

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

describe("SafeCommand", () => {
  it("runs an allowlisted command without shell expansion", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        {
          command: "printf",
          args: ["%s", "a;echo nope"],
        },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output?.stdout.trim()).toBe("a;echo nope");
      expect(result.output?.stderr).toBe("");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects commands that are not allowlisted", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        { command: "curl", args: ["https://example.com"] },
        makeCtx(root),
      );

      expect(result.status).toBe("permission_denied");
      expect(result.errorCode).toBe("command_not_allowed");
      expect(result.output).toBeUndefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects path arguments that escape the workspace", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        { command: "cat", args: ["/etc/passwd"] },
        makeCtx(root),
      );

      expect(result.status).toBe("permission_denied");
      expect(result.errorCode).toBe("unsafe_argument");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects secret-like workspace paths", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const tool = makeSafeCommandTool(root);
      const envResult = await tool.execute(
        { command: "cat", args: [".env"] },
        makeCtx(root),
      );
      const secretsResult = await tool.execute(
        { command: "cat", args: ["secrets/api-key.txt"] },
        makeCtx(root),
      );

      expect(envResult.status).toBe("permission_denied");
      expect(envResult.errorCode).toBe("unsafe_argument");
      expect(secretsResult.status).toBe("permission_denied");
      expect(secretsResult.errorCode).toBe("unsafe_argument");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects mutating git subcommands", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        { command: "git", args: ["push", "origin", "main"] },
        makeCtx(root),
      );

      expect(result.status).toBe("permission_denied");
      expect(result.errorCode).toBe("git_subcommand_not_allowed");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("scrubs sensitive environment variables", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    const previous = process.env.ANTHROPIC_API_KEY;
    process.env.ANTHROPIC_API_KEY = "secret-value";
    try {
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        {
          command: "env",
          args: [],
        },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output?.stdout).not.toContain("ANTHROPIC_API_KEY");
      expect(result.output?.stdout).not.toContain("secret-value");
    } finally {
      if (previous === undefined) {
        delete process.env.ANTHROPIC_API_KEY;
      } else {
        process.env.ANTHROPIC_API_KEY = previous;
      }
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("reports timed-out commands explicitly", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      await fs.writeFile(path.join(root, "log.txt"), "line\n", "utf8");
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        {
          command: "tail",
          args: ["-f", "log.txt"],
          timeoutMs: 50,
        },
        makeCtx(root),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("timeout");
      expect(result.errorMessage).toContain("timed out after 50ms");
      expect(result.output).toMatchObject({
        command: "tail",
        args: ["-f", "log.txt"],
        exitCode: null,
        timedOut: true,
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("returns a full stdout log file path when allowlisted output is truncated", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "safe-command-"));
    try {
      const outputBytes = 530 * 1024;
      await fs.writeFile(
        path.join(root, "big.log"),
        `${"x".repeat(outputBytes)}SAFE_STDOUT_END`,
        "utf8",
      );
      const tool = makeSafeCommandTool(root);
      const result = await tool.execute(
        {
          command: "cat",
          args: ["big.log"],
        },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output?.truncated).toBe(true);
      expect(result.output?.stdout).not.toContain("SAFE_STDOUT_END");
      expect(result.output?.stdoutFile).toMatch(
        /^\.openmagi\/command-logs\/turn-test\/SafeCommand-\d+-stdout\.log$/,
      );
      expect(result.output?.stderrFile).toBeUndefined();

      const stdoutFile = result.output?.stdoutFile;
      expect(stdoutFile).toBeTruthy();
      const stdoutFull = await fs.readFile(path.join(root, stdoutFile!), "utf8");
      expect(stdoutFull).toHaveLength(outputBytes + "SAFE_STDOUT_END".length);
      expect(stdoutFull.endsWith("SAFE_STDOUT_END")).toBe(true);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
