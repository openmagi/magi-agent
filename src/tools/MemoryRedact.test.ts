import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeMemoryRedactTool } from "./MemoryRedact.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "session-test",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("MemoryRedact", () => {
  it("redacts exact target text from memory files and writes hash-only audit evidence", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "memory-redact-"));
    await fs.mkdir(path.join(root, "memory", "daily"), { recursive: true });
    const memoryPath = "memory/daily/2026-05-08.md";
    const target = "Donggun private token is abc123.";
    await fs.writeFile(
      path.join(root, memoryPath),
      `# Daily\n\nKeep this.\n${target}\nKeep that.\n`,
      "utf8",
    );

    const result = await makeMemoryRedactTool(root).execute(
      {
        mode: "redact",
        target_text: target,
        paths: [memoryPath],
        replacement: "[redacted by user request]",
        confirm_raw_redaction: true,
        reason: "user requested memory deletion",
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      mode: "redact",
      matchedCount: 1,
      changedFiles: [memoryPath],
      verification: { targetStillPresent: false },
    });
    const next = await fs.readFile(path.join(root, memoryPath), "utf8");
    expect(next).not.toContain(target);
    expect(next).toContain("[redacted by user request]");

    const auditPath = result.output?.auditPath;
    expect(auditPath).toMatch(/^memory\/\.redactions\/\d{4}-\d{2}-\d{2}\.jsonl$/);
    const audit = await fs.readFile(path.join(root, auditPath ?? ""), "utf8");
    expect(audit).toContain('"targetSha256"');
    expect(audit).not.toContain(target);
  });

  it("requires explicit raw redaction confirmation before editing memory files", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "memory-redact-"));
    await fs.mkdir(path.join(root, "memory"), { recursive: true });
    await fs.writeFile(path.join(root, "memory/ROOT.md"), "forget me", "utf8");

    const result = await makeMemoryRedactTool(root).execute(
      {
        mode: "redact",
        target_text: "forget me",
        paths: ["memory/ROOT.md"],
      },
      makeCtx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("confirmation_required");
    await expect(fs.readFile(path.join(root, "memory/ROOT.md"), "utf8")).resolves.toBe("forget me");
  });

  it("rejects paths outside memory", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "memory-redact-"));
    await fs.writeFile(path.join(root, "notes.md"), "secret", "utf8");

    const result = await makeMemoryRedactTool(root).execute(
      {
        mode: "redact",
        target_text: "secret",
        paths: ["notes.md"],
        confirm_raw_redaction: true,
      },
      makeCtx(root),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_memory_path");
  });

  it("records no-match evidence without changing files", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "memory-redact-"));
    await fs.mkdir(path.join(root, "memory"), { recursive: true });
    const memoryPath = "memory/ROOT.md";
    await fs.writeFile(path.join(root, memoryPath), "keep this memory", "utf8");

    const result = await makeMemoryRedactTool(root).execute(
      {
        mode: "redact",
        target_text: "missing private detail",
        paths: [memoryPath],
        confirm_raw_redaction: true,
      },
      makeCtx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      matchedCount: 0,
      changedFiles: [],
      verification: { targetStillPresent: false },
    });
    await expect(fs.readFile(path.join(root, memoryPath), "utf8")).resolves.toBe("keep this memory");
    const audit = await fs.readFile(path.join(root, result.output?.auditPath ?? ""), "utf8");
    expect(audit).toContain('"matchedCount":0');
    expect(audit).not.toContain("missing private detail");
  });
});
